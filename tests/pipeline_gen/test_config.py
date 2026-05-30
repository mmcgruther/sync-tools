"""Tests for config.py — pure parsing, no subprocess involvement."""

from pipeline_gen.config import STAGES, load_config


def test_loads_both_repos(repos_ini):
    config = load_config(repos_ini)
    assert set(config.repos) == {"auth-service", "data-worker"}


def test_source_field(repos_ini):
    config = load_config(repos_ini)
    assert config.repos["auth-service"].source == "git@git.internal:auth-service.git"
    assert config.repos["data-worker"].source == "git@git.internal:data-worker.git"


def test_all_three_stages_present(repos_ini):
    config = load_config(repos_ini)
    for repo in config.repos.values():
        assert set(repo.commands) == {"build", "test", "deploy"}


def test_commands_are_argv_lists(repos_ini):
    config = load_config(repos_ini)
    build_argv = config.repos["auth-service"].commands["build"]
    assert isinstance(build_argv, list)
    assert build_argv[0] == "docker"


def test_registry_interpolated_into_build(repos_ini):
    config = load_config(repos_ini)
    build_argv = config.repos["auth-service"].commands["build"]
    assert "registry.internal/auth-service:dev" in build_argv


def test_version_override_flows_into_commands(repos_ini):
    config = load_config(repos_ini, version="2.3.4")
    build_argv = config.repos["auth-service"].commands["build"]
    assert "registry.internal/auth-service:2.3.4" in build_argv


def test_version_override_in_deploy_command(repos_ini):
    config = load_config(repos_ini, version="1.0.0")
    deploy_argv = config.repos["auth-service"].commands["deploy"]
    # helm --set image.tag=<version> should carry the override
    assert any("image.tag=1.0.0" in arg for arg in deploy_argv)


def test_list_jobs_contains_all_repo_stage_pairs(repos_ini):
    config = load_config(repos_ini)
    jobs = config.list_jobs()
    for repo_id in ("auth-service", "data-worker"):
        for stage in STAGES:
            assert f"{repo_id}/{stage}" in jobs


def test_list_jobs_stage_order_within_repo(repos_ini):
    """build must precede test must precede deploy for every repo."""
    config = load_config(repos_ini)
    jobs = config.list_jobs()
    for repo_id in config.repos:
        repo_jobs = [j for j in jobs if j.startswith(f"{repo_id}/")]
        stages = [j.split("/", 1)[1] for j in repo_jobs]
        assert stages == [s for s in STAGES if s in config.repos[repo_id].commands]


def test_partial_stages_only_listed(tmp_path):
    """A repo with only build and test should not appear in deploy jobs."""
    ini = tmp_path / "repos.ini"
    ini.write_text(
        "[repo-a]\n"
        "build = make build\n"
        "test  = make test\n"
    )
    config = load_config(ini)
    jobs = config.list_jobs()
    assert "repo-a/build" in jobs
    assert "repo-a/test" in jobs
    assert "repo-a/deploy" not in jobs
