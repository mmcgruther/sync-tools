from __future__ import annotations

import pathlib

import pytest

from sync_tools.errors import GitCommandError
from sync_tools.git_ops import (
    clone_to_temp,
    find_case_conflicts,
    find_hierarchy_conflicts,
    has_lfs_objects,
    is_ancestor,
    is_git_repo,
    lfs_object_path,
    lfs_oids_for_refs,
    list_refs,
    refs_with_lfs,
    resolve_ref,
    run_git,
)

from .conftest import GitRepo, _git, make_commit


class TestRunGit:
    def test_success(self, git_repo: GitRepo) -> None:
        result = run_git(["rev-parse", "--git-dir"], cwd=git_repo.path)
        assert result.returncode == 0

    def test_nonzero_raises(self, tmp_path: pathlib.Path) -> None:
        # rev-parse in non-repo should fail
        with pytest.raises(GitCommandError) as exc_info:
            run_git(["rev-parse", "HEAD"], cwd=tmp_path)
        assert exc_info.value.returncode != 0

    def test_error_message_includes_stderr(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(GitCommandError) as exc_info:
            run_git(["rev-parse", "nonexistent-ref"], cwd=tmp_path)
        # The string representation should mention git
        assert "git" in str(exc_info.value).lower()


class TestListRefs:
    def test_returns_refs(self, git_repo: GitRepo) -> None:
        refs = list_refs(git_repo.path)
        assert "refs/heads/main" in refs
        sha = refs["refs/heads/main"]
        assert len(sha) == 40
        assert all(c in "0123456789abcdef" for c in sha)

    def test_includes_tags(self, git_repo: GitRepo) -> None:
        _git(["tag", "v0.1"], git_repo.path)
        refs = list_refs(git_repo.path)
        assert "refs/tags/v0.1" in refs

    def test_empty_bare_repo(self, tmp_path: pathlib.Path) -> None:
        bare = tmp_path / "empty.git"
        bare.mkdir()
        run_git(["init", "--bare", str(bare)], cwd=tmp_path)
        refs = list_refs(bare)
        assert refs == {}


class TestIsAncestor:
    def test_true_for_linear_history(self, git_repo: GitRepo) -> None:
        first = resolve_ref(git_repo.path, "HEAD")
        make_commit(git_repo.path, "second")
        second = resolve_ref(git_repo.path, "HEAD")
        assert is_ancestor(git_repo.path, first, second) is True

    def test_false_for_non_ancestor(self, git_repo: GitRepo) -> None:
        first = resolve_ref(git_repo.path, "HEAD")
        make_commit(git_repo.path, "second")
        second = resolve_ref(git_repo.path, "HEAD")
        # Reverse: second is not an ancestor of first
        assert is_ancestor(git_repo.path, second, first) is False

    def test_same_commit_is_ancestor_of_itself(self, git_repo: GitRepo) -> None:
        sha = resolve_ref(git_repo.path, "HEAD")
        assert is_ancestor(git_repo.path, sha, sha) is True


class TestHasLfsObjects:
    def test_no_lfs_in_normal_repo(self, git_repo: GitRepo) -> None:
        assert has_lfs_objects(git_repo.path) is False


class TestRefsWithLfs:
    def test_no_lfs_in_normal_repo(self, git_repo: GitRepo) -> None:
        refs = list(list_refs(git_repo.path).keys())
        assert refs_with_lfs(git_repo.path, refs) == set()

    def test_all_refs_have_lfs(self, lfs_repo: GitRepo) -> None:
        refs = list(list_refs(lfs_repo.path).keys())
        result = refs_with_lfs(lfs_repo.path, refs)
        assert "refs/heads/main" in result

    def test_partial_lfs(self, mixed_lfs_repo: GitRepo) -> None:
        refs = list(list_refs(mixed_lfs_repo.path).keys())
        result = refs_with_lfs(mixed_lfs_repo.path, refs)
        assert "refs/heads/main" in result
        assert "refs/heads/no-lfs" not in result


class TestFindCaseConflicts:
    def test_no_conflicts(self) -> None:
        refs = {
            "refs/heads/main": "a" * 40,
            "refs/heads/feature": "b" * 40,
            "refs/tags/v1.0": "c" * 40,
        }
        assert find_case_conflicts(refs) == []

    def test_detects_conflict(self) -> None:
        refs = {
            "refs/heads/Foo": "a" * 40,
            "refs/heads/foo": "b" * 40,
            "refs/heads/bar": "c" * 40,
        }
        conflicts = find_case_conflicts(refs)
        assert len(conflicts) == 1
        pair = conflicts[0]
        assert set(pair) == {"refs/heads/Foo", "refs/heads/foo"}

    def test_multiple_conflicts(self) -> None:
        refs = {
            "refs/heads/A": "a" * 40,
            "refs/heads/a": "b" * 40,
            "refs/heads/B": "c" * 40,
            "refs/heads/b": "d" * 40,
        }
        conflicts = find_case_conflicts(refs)
        assert len(conflicts) == 2

    def test_empty(self) -> None:
        assert find_case_conflicts({}) == []


class TestFindHierarchyConflicts:
    def test_no_conflicts(self) -> None:
        refs = {
            "refs/heads/main": "a" * 40,
            "refs/heads/bugfix": "b" * 40,
            "refs/heads/feature/new": "c" * 40,
        }
        assert find_hierarchy_conflicts(refs) == []

    def test_detects_conflict(self) -> None:
        refs = {
            "refs/heads/bugfix": "a" * 40,
            "refs/heads/bugfix/a": "b" * 40,
        }
        conflicts = find_hierarchy_conflicts(refs)
        assert len(conflicts) == 1
        assert conflicts[0] == ("refs/heads/bugfix", "refs/heads/bugfix/a")

    def test_multiple_children(self) -> None:
        refs = {
            "refs/heads/bugfix": "a" * 40,
            "refs/heads/bugfix/a": "b" * 40,
            "refs/heads/bugfix/b": "c" * 40,
        }
        conflicts = find_hierarchy_conflicts(refs)
        assert len(conflicts) == 2

    def test_no_false_positive_on_common_prefix(self) -> None:
        refs = {
            "refs/heads/bugfix": "a" * 40,
            "refs/heads/bugfix-long": "b" * 40,
        }
        assert find_hierarchy_conflicts(refs) == []

    def test_empty(self) -> None:
        assert find_hierarchy_conflicts({}) == []


class TestIsGitRepo:
    def test_true_for_non_bare(self, git_repo: GitRepo) -> None:
        assert is_git_repo(git_repo.path) is True

    def test_true_for_bare(self, git_repo: GitRepo) -> None:
        assert is_git_repo(git_repo.bare_path) is True

    def test_false_for_non_repo(self, tmp_path: pathlib.Path) -> None:
        assert is_git_repo(tmp_path) is False

    def test_false_for_missing(self, tmp_path: pathlib.Path) -> None:
        assert is_git_repo(tmp_path / "nonexistent") is False


class TestLfsOidsForRefs:
    def test_no_lfs_in_normal_repo(self, git_repo: GitRepo) -> None:
        refs = list(list_refs(git_repo.path).keys())
        assert lfs_oids_for_refs(git_repo.path, refs) == set()

    def test_returns_oids_for_lfs_repo(self, lfs_repo: GitRepo) -> None:
        refs = list(list_refs(lfs_repo.path).keys())
        result = lfs_oids_for_refs(lfs_repo.path, refs)
        assert len(result) > 0
        for oid in result:
            assert len(oid) == 64
            assert all(c in "0123456789abcdef" for c in oid)

    def test_partial_lfs_only_lfs_ref_contributes(self, mixed_lfs_repo: GitRepo) -> None:
        refs = list(list_refs(mixed_lfs_repo.path).keys())
        all_oids = lfs_oids_for_refs(mixed_lfs_repo.path, refs)
        no_lfs_oids = lfs_oids_for_refs(mixed_lfs_repo.path, ["refs/heads/no-lfs"])
        main_oids = lfs_oids_for_refs(mixed_lfs_repo.path, ["refs/heads/main"])
        assert len(no_lfs_oids) == 0
        assert len(main_oids) > 0
        assert all_oids == main_oids


class TestLfsObjectPath:
    def test_returns_none_when_missing(self, tmp_path: pathlib.Path) -> None:
        repo = tmp_path / "fake-repo"
        repo.mkdir()
        oid = "a" * 64
        assert lfs_object_path(repo, oid) is None

    def test_finds_bare_repo_object(self, tmp_path: pathlib.Path) -> None:
        repo = tmp_path / "fake-bare.git"
        oid = "abcdef1234" + "0" * 54
        obj_dir = repo / "lfs" / "objects" / oid[:2] / oid[2:4]
        obj_dir.mkdir(parents=True)
        obj_file = obj_dir / oid
        obj_file.write_bytes(b"fake lfs data")
        result = lfs_object_path(repo, oid)
        assert result == obj_file

    def test_finds_nonbare_repo_object(self, tmp_path: pathlib.Path) -> None:
        repo = tmp_path / "fake-nonbare"
        oid = "deadbeef12" + "0" * 54
        obj_dir = repo / ".git" / "lfs" / "objects" / oid[:2] / oid[2:4]
        obj_dir.mkdir(parents=True)
        obj_file = obj_dir / oid
        obj_file.write_bytes(b"fake lfs data")
        result = lfs_object_path(repo, oid)
        assert result == obj_file


class TestCloneToTemp:
    def test_clones_local_repo(self, git_repo: GitRepo, tmp_path: pathlib.Path) -> None:
        clone_dir = tmp_path / "clone"
        clone_dir.mkdir()
        cloned = clone_to_temp(str(git_repo.path), clone_dir)
        assert cloned.exists()
        assert is_git_repo(cloned)
        refs = list_refs(cloned)
        assert any("main" in r for r in refs)
