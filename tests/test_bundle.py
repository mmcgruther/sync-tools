from __future__ import annotations

import pathlib

import pytest

from sync_tools.bundle import execute_bundle, plan_bundle
from sync_tools.errors import (
    CaseConflictError,
    LFSDetectedError,
    RebaseDetectedError,
    TagMovedError,
)
from sync_tools.git_ops import list_refs
from sync_tools.state import LastSync, RepoConfig

from .conftest import GitRepo, _git, force_rebase, make_commit, move_tag


def _make_repo_config(git_repo: GitRepo, last_sync: LastSync | None = None) -> RepoConfig:
    return RepoConfig(
        id="test/repo",
        source_url=str(git_repo.path),
        dest_path=str(git_repo.bare_path),
        source_local_path=str(git_repo.path),
        last_sync=last_sync,
    )


class TestPlanBundle:
    def test_first_sync_is_full(self, git_repo: GitRepo) -> None:
        repo = _make_repo_config(git_repo)
        current_refs = list_refs(git_repo.path)
        plan = plan_bundle(repo, current_refs, git_repo.path)
        assert plan.is_full_bundle is True
        assert plan.no_changes is False
        assert set(plan.refs_to_bundle) == set(current_refs.keys())
        assert plan.since_shas == {}

    def test_no_changes(self, git_repo: GitRepo) -> None:
        current_refs = list_refs(git_repo.path)
        repo = _make_repo_config(
            git_repo,
            last_sync=LastSync(timestamp="2024-01-01T00:00:00Z", refs=dict(current_refs)),
        )
        plan = plan_bundle(repo, current_refs, git_repo.path)
        assert plan.no_changes is True
        assert plan.refs_to_bundle == []

    def test_incremental_fast_forward(self, git_repo: GitRepo) -> None:
        old_refs = dict(list_refs(git_repo.path))
        repo = _make_repo_config(
            git_repo,
            last_sync=LastSync(timestamp="2024-01-01T00:00:00Z", refs=old_refs),
        )
        make_commit(git_repo.path, "new commit")
        new_refs = list_refs(git_repo.path)
        plan = plan_bundle(repo, new_refs, git_repo.path)
        assert plan.no_changes is False
        assert plan.is_full_bundle is False
        assert "refs/heads/main" in plan.refs_to_bundle
        assert "refs/heads/main" in plan.since_shas
        assert plan.since_shas["refs/heads/main"] == old_refs["refs/heads/main"]

    def test_new_ref_included_without_since(self, git_repo: GitRepo) -> None:
        old_refs = dict(list_refs(git_repo.path))
        repo = _make_repo_config(
            git_repo,
            last_sync=LastSync(timestamp="2024-01-01T00:00:00Z", refs=old_refs),
        )
        # Add a new branch
        _git(["branch", "feature/new"], git_repo.path)
        new_refs = list_refs(git_repo.path)
        plan = plan_bundle(repo, new_refs, git_repo.path)
        assert "refs/heads/feature/new" in plan.refs_to_bundle
        assert "refs/heads/feature/new" not in plan.since_shas

    def test_rebase_raises(self, git_repo: GitRepo) -> None:
        old_refs = dict(list_refs(git_repo.path))
        repo = _make_repo_config(
            git_repo,
            last_sync=LastSync(timestamp="2024-01-01T00:00:00Z", refs=old_refs),
        )
        force_rebase(git_repo.path)
        new_refs = list_refs(git_repo.path)
        with pytest.raises(RebaseDetectedError):
            plan_bundle(repo, new_refs, git_repo.path)

    def test_rebase_allowed(self, git_repo: GitRepo) -> None:
        old_refs = dict(list_refs(git_repo.path))
        repo = _make_repo_config(
            git_repo,
            last_sync=LastSync(timestamp="2024-01-01T00:00:00Z", refs=old_refs),
        )
        force_rebase(git_repo.path)
        new_refs = list_refs(git_repo.path)
        plan = plan_bundle(repo, new_refs, git_repo.path, allow_rebase=True)
        assert "refs/heads/main" in plan.refs_to_bundle
        assert "refs/heads/main" not in plan.since_shas  # no since — full for this branch
        assert any("rebased" in w.lower() or "force" in w.lower() for w in plan.warnings)

    def test_tag_moved_raises(self, git_repo: GitRepo) -> None:
        # Put a second commit on main, then tag it — so the root is a non-ancestor of the tag
        make_commit(git_repo.path, "second commit before tag")
        _git(["tag", "-a", "v1.0.0", "-m", "tag at second commit"], git_repo.path)
        old_refs = dict(list_refs(git_repo.path))
        repo = _make_repo_config(
            git_repo,
            last_sync=LastSync(timestamp="2024-01-01T00:00:00Z", refs=old_refs),
        )
        # Move tag backward to the root commit — root is NOT a descendant of sha_b
        initial = _git(["rev-list", "--max-parents=0", "HEAD"], git_repo.path).stdout.strip()
        move_tag(git_repo.path, "v1.0.0", initial)
        new_refs = list_refs(git_repo.path)
        with pytest.raises(TagMovedError):
            plan_bundle(repo, new_refs, git_repo.path)

    def test_lfs_raises(self, lfs_repo: GitRepo) -> None:
        repo = _make_repo_config(lfs_repo)
        current_refs = list_refs(lfs_repo.path)
        with pytest.raises(LFSDetectedError):
            plan_bundle(repo, current_refs, lfs_repo.path)

    def test_lfs_allowed(self, lfs_repo: GitRepo) -> None:
        repo = _make_repo_config(lfs_repo)
        current_refs = list_refs(lfs_repo.path)
        plan = plan_bundle(repo, current_refs, lfs_repo.path, allow_lfs=True)
        assert plan.is_full_bundle is True
        assert any("lfs" in w.lower() for w in plan.warnings)

    def test_case_conflict_raises(self, git_repo: GitRepo) -> None:
        # Inject two refs that differ only by case into the refs dict
        current_refs = {
            "refs/heads/main": "a" * 40,
            "refs/heads/Main": "b" * 40,
        }
        repo = _make_repo_config(git_repo)
        with pytest.raises(CaseConflictError):
            plan_bundle(repo, current_refs, git_repo.path)


class TestExecuteBundle:
    def test_creates_bundle_file(self, git_repo: GitRepo, tmp_path: pathlib.Path) -> None:
        current_refs = list_refs(git_repo.path)
        repo = _make_repo_config(git_repo)
        plan = plan_bundle(repo, current_refs, git_repo.path)
        output_dir = tmp_path / "bundles"
        result = execute_bundle(plan, output_dir, git_repo.path, current_refs)
        assert result.bundle_path.exists()
        assert result.bundle_path.stat().st_size > 0

    def test_exported_refs_match(self, git_repo: GitRepo, tmp_path: pathlib.Path) -> None:
        current_refs = list_refs(git_repo.path)
        repo = _make_repo_config(git_repo)
        plan = plan_bundle(repo, current_refs, git_repo.path)
        result = execute_bundle(plan, tmp_path / "out", git_repo.path, current_refs)
        for ref in plan.refs_to_bundle:
            assert result.exported_refs[ref] == current_refs[ref]

    def test_incremental_bundle_is_smaller(self, git_repo: GitRepo, tmp_path: pathlib.Path) -> None:
        # Build up a large commit history so the full bundle is substantial
        for i in range(20):
            make_commit(git_repo.path, f"base commit {i}", f"base{i}.txt", "x" * 500)

        current_refs = list_refs(git_repo.path)
        old_refs = dict(current_refs)

        # Full bundle of all 20+ commits
        repo = _make_repo_config(git_repo)
        full_plan = plan_bundle(repo, current_refs, git_repo.path)
        full_result = execute_bundle(full_plan, tmp_path / "full", git_repo.path, current_refs)
        full_size = full_result.bundle_path.stat().st_size

        # Add just 2 new commits, then create an incremental bundle
        make_commit(git_repo.path, "delta 1", "delta1.txt", "y" * 100)
        make_commit(git_repo.path, "delta 2", "delta2.txt", "y" * 100)
        new_refs = list_refs(git_repo.path)

        repo2 = _make_repo_config(
            git_repo,
            last_sync=LastSync(timestamp="2024-01-01T00:00:00Z", refs=old_refs),
        )
        inc_plan = plan_bundle(repo2, new_refs, git_repo.path)
        inc_result = execute_bundle(inc_plan, tmp_path / "inc", git_repo.path, new_refs)
        inc_size = inc_result.bundle_path.stat().st_size

        assert inc_size < full_size
