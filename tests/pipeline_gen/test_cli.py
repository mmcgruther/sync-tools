"""
CLI integration tests.

Uses Click's CliRunner to invoke commands in-process so the
``subprocess_recorder`` fixture still intercepts subprocess.run calls.
"""

from click.testing import CliRunner

from pipeline_gen.cli import cli


class TestListJobsCommand:
    def test_outputs_all_six_jobs(self, repos_ini):
        result = CliRunner().invoke(cli, ["list-jobs", str(repos_ini)])
        assert result.exit_code == 0
        lines = result.output.strip().splitlines()
        expected = [
            "auth-service/build", "auth-service/test", "auth-service/deploy",
            "data-worker/build",  "data-worker/test",  "data-worker/deploy",
        ]
        assert lines == expected

    def test_stage_order_within_repo(self, repos_ini):
        result = CliRunner().invoke(cli, ["list-jobs", str(repos_ini)])
        lines = result.output.strip().splitlines()
        auth_stages = [l.split("/")[1] for l in lines if l.startswith("auth-service/")]
        assert auth_stages == ["build", "test", "deploy"]


class TestRunCommand:
    def test_run_build_dispatches_docker(self, repos_ini, subprocess_recorder):
        result = CliRunner().invoke(cli, ["run", str(repos_ini), "auth-service/build"])

        assert result.exit_code == 0
        subprocess_recorder.assert_sequence(
            ["docker", "build", "-t", "registry.internal/auth-service:dev", "."],
        )

    def test_run_with_version_flag(self, repos_ini, subprocess_recorder):
        result = CliRunner().invoke(
            cli, ["run", str(repos_ini), "auth-service/build", "--version", "9.9.9"]
        )

        assert result.exit_code == 0
        argv = subprocess_recorder.calls[0].argv
        assert "registry.internal/auth-service:9.9.9" in argv

    def test_run_failure_exits_nonzero(self, repos_ini, subprocess_recorder):
        subprocess_recorder.configure(["docker"], returncode=1)
        result = CliRunner().invoke(cli, ["run", str(repos_ini), "auth-service/build"])

        assert result.exit_code != 0

    def test_run_failure_prints_error(self, repos_ini, subprocess_recorder):
        subprocess_recorder.configure(["pytest"], returncode=1)
        result = CliRunner().invoke(cli, ["run", str(repos_ini), "auth-service/test"])

        assert "auth-service/test" in result.output
        assert "failed" in result.output.lower()

    def test_run_test_job_calls_pytest(self, repos_ini, subprocess_recorder):
        result = CliRunner().invoke(cli, ["run", str(repos_ini), "data-worker/test"])

        assert result.exit_code == 0
        assert subprocess_recorder.calls[0].argv[0] == "pytest"

    def test_run_deploy_data_worker_calls_kubectl(self, repos_ini, subprocess_recorder):
        result = CliRunner().invoke(cli, ["run", str(repos_ini), "data-worker/deploy"])

        assert result.exit_code == 0
        assert subprocess_recorder.calls[0].argv[0] == "kubectl"
