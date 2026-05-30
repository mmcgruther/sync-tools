"""
Tests for runner.py.

These tests use the ``subprocess_recorder`` fixture to intercept all
subprocess.run calls and verify:
  - exact argv passed to each external tool
  - canonical stage ordering (build → test → deploy)
  - pipeline aborts on failure and never runs later stages
  - run_job is surgical (only the requested stage executes)
"""

import pytest

from pipeline_gen.config import load_config
from pipeline_gen.runner import StageError, run_job, run_pipeline


# ---------------------------------------------------------------------------
# run_pipeline — full stage sequence
# ---------------------------------------------------------------------------


class TestRunPipeline:
    def test_calls_all_three_stages_in_order(self, repos_ini, subprocess_recorder):
        config = load_config(repos_ini)
        run_pipeline(config, "auth-service")

        subprocess_recorder.assert_sequence(
            ["docker", "build", "-t", "registry.internal/auth-service:dev", "."],
            ["pytest", "tests/", "-v"],
            ["helm", "upgrade", "--install", "auth-service", "./chart", "--set", "image.tag=dev"],
        )

    def test_build_index_lt_test_index_lt_deploy_index(self, repos_ini, subprocess_recorder):
        """Ordering invariant checked positionally — independent of exact argv."""
        config = load_config(repos_ini)
        run_pipeline(config, "data-worker")

        commands = subprocess_recorder.commands
        idx = {
            "build":  next(i for i, c in enumerate(commands) if c[0] == "docker"),
            "test":   next(i for i, c in enumerate(commands) if c[0] == "pytest"),
            "deploy": next(i for i, c in enumerate(commands) if c[0] == "kubectl"),
        }
        assert idx["build"] < idx["test"] < idx["deploy"]

    def test_version_propagated_to_every_stage(self, repos_ini, subprocess_recorder):
        config = load_config(repos_ini, version="3.0.1")
        run_pipeline(config, "auth-service")

        for call in subprocess_recorder.calls:
            joined = " ".join(call.argv)
            if "registry.internal/auth-service" in joined:
                assert "3.0.1" in joined, f"version missing from: {call.argv}"

    # ------------------------------------------------------------------
    # Failure propagation
    # ------------------------------------------------------------------

    def test_build_failure_raises_stage_error(self, repos_ini, subprocess_recorder):
        subprocess_recorder.configure(["docker"], returncode=1)
        config = load_config(repos_ini)

        with pytest.raises(StageError) as exc_info:
            run_pipeline(config, "auth-service")

        err = exc_info.value
        assert err.stage == "build"
        assert err.repo_id == "auth-service"
        assert err.returncode == 1

    def test_build_failure_only_one_call_made(self, repos_ini, subprocess_recorder):
        """Pipeline must stop immediately — test and deploy must not run."""
        subprocess_recorder.configure(["docker"], returncode=1)
        config = load_config(repos_ini)

        with pytest.raises(StageError):
            run_pipeline(config, "auth-service")

        assert len(subprocess_recorder.calls) == 1

    def test_test_failure_never_deploys(self, repos_ini, subprocess_recorder):
        subprocess_recorder.configure(["pytest"], returncode=1)
        config = load_config(repos_ini)

        with pytest.raises(StageError) as exc_info:
            run_pipeline(config, "auth-service")

        assert exc_info.value.stage == "test"
        deployed = any(c.argv[0] == "helm" for c in subprocess_recorder.calls)
        assert not deployed, "deploy must not run after a test failure"

    def test_test_failure_stage_error_attributes(self, repos_ini, subprocess_recorder):
        subprocess_recorder.configure(["pytest"], returncode=2)
        config = load_config(repos_ini)

        with pytest.raises(StageError) as exc_info:
            run_pipeline(config, "auth-service")

        err = exc_info.value
        assert err.stage == "test"
        assert err.returncode == 2

    def test_nonzero_exit_code_preserved_in_error(self, repos_ini, subprocess_recorder):
        subprocess_recorder.configure(["docker"], returncode=127)
        config = load_config(repos_ini)

        with pytest.raises(StageError) as exc_info:
            run_pipeline(config, "auth-service")

        assert exc_info.value.returncode == 127


# ---------------------------------------------------------------------------
# run_job — single stage dispatch
# ---------------------------------------------------------------------------


class TestRunJob:
    def test_build_job_calls_docker(self, repos_ini, subprocess_recorder):
        config = load_config(repos_ini)
        run_job(config, "auth-service/build")

        subprocess_recorder.assert_sequence(
            ["docker", "build", "-t", "registry.internal/auth-service:dev", "."],
        )

    def test_test_job_calls_pytest(self, repos_ini, subprocess_recorder):
        config = load_config(repos_ini)
        run_job(config, "auth-service/test")

        subprocess_recorder.assert_sequence(["pytest", "tests/", "-v"])

    def test_deploy_job_calls_helm(self, repos_ini, subprocess_recorder):
        config = load_config(repos_ini)
        run_job(config, "auth-service/deploy")

        subprocess_recorder.assert_sequence(
            ["helm", "upgrade", "--install", "auth-service", "./chart", "--set", "image.tag=dev"],
        )

    def test_run_job_is_surgical(self, repos_ini, subprocess_recorder):
        """run_job must only invoke the requested stage — nothing else."""
        config = load_config(repos_ini)
        run_job(config, "data-worker/test")

        assert len(subprocess_recorder.calls) == 1
        assert subprocess_recorder.calls[0].argv[0] == "pytest"

    def test_job_failure_raises_stage_error(self, repos_ini, subprocess_recorder):
        subprocess_recorder.configure(["pytest"], returncode=1)
        config = load_config(repos_ini)

        with pytest.raises(StageError):
            run_job(config, "auth-service/test")

    def test_job_with_version_override(self, repos_ini, subprocess_recorder):
        config = load_config(repos_ini, version="5.0.0")
        run_job(config, "data-worker/build")

        argv = subprocess_recorder.calls[0].argv
        assert "registry.internal/data-worker:5.0.0" in argv

    def test_different_repos_call_different_tools(self, repos_ini, subprocess_recorder):
        config = load_config(repos_ini)
        run_job(config, "auth-service/deploy")
        subprocess_recorder.reset()
        run_job(config, "data-worker/deploy")

        # auth-service uses helm; data-worker uses kubectl
        assert subprocess_recorder.calls[0].argv[0] == "kubectl"


# ---------------------------------------------------------------------------
# assert_contains_in_order demo — useful when other calls may be interleaved
# ---------------------------------------------------------------------------


class TestContainsInOrder:
    def test_build_before_deploy_subsequence(self, repos_ini, subprocess_recorder):
        """Demonstrate assert_contains_in_order for subset ordering checks."""
        config = load_config(repos_ini)
        run_pipeline(config, "auth-service")

        subprocess_recorder.assert_contains_in_order(
            ["docker", "build", "-t", "registry.internal/auth-service:dev", "."],
            ["helm", "upgrade", "--install", "auth-service", "./chart", "--set", "image.tag=dev"],
        )
