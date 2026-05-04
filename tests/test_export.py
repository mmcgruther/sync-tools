from __future__ import annotations

import json
import pathlib
import tarfile

from sync_tools.export_cmd import ExportOptions, run_export
from sync_tools.state import load_state

from .conftest import GitRepo, _git, force_rebase, make_commit, move_tag


def _make_options(
    state_path: pathlib.Path,
    output_path: pathlib.Path,
    *,
    allow_rebase: bool = False,
    dry_run: bool = False,
    workers: int = 1,
) -> ExportOptions:
    return ExportOptions(
        state_path=state_path,
        output_path=output_path,
        workers=workers,
        allow_rebase=allow_rebase,
        dry_run=dry_run,
    )


class TestHappyPath:
    def test_creates_archive(
        self, tmp_path: pathlib.Path, state_file: pathlib.Path, git_repo: GitRepo
    ) -> None:
        out = tmp_path / "out.tar.gz"
        summary = run_export(_make_options(state_file, out))
        assert out.exists()
        assert "test/repo" in summary.succeeded
        assert summary.failed == []

    def test_state_updated_with_refs(
        self, tmp_path: pathlib.Path, state_file: pathlib.Path, git_repo: GitRepo
    ) -> None:
        out = tmp_path / "out.tar.gz"
        run_export(_make_options(state_file, out))
        repos = load_state(state_file)
        assert repos[0].last_sync is not None
        assert "refs/heads/main" in repos[0].last_sync.refs

    def test_archive_contains_manifest(
        self, tmp_path: pathlib.Path, state_file: pathlib.Path, git_repo: GitRepo
    ) -> None:
        out = tmp_path / "out.tar.gz"
        run_export(_make_options(state_file, out))
        with tarfile.open(out, "r:gz") as tar:
            names = tar.getnames()
        assert "manifest.json" in names

    def test_archive_contains_bundle(
        self, tmp_path: pathlib.Path, state_file: pathlib.Path, git_repo: GitRepo
    ) -> None:
        out = tmp_path / "out.tar.gz"
        run_export(_make_options(state_file, out))
        with tarfile.open(out, "r:gz") as tar:
            names = tar.getnames()
        assert any("bundle.git" in n for n in names)


class TestIncrementalExport:
    def test_second_export_is_smaller(
        self, tmp_path: pathlib.Path, state_file: pathlib.Path, git_repo: GitRepo
    ) -> None:
        # Build a large history before the first export so the full bundle is substantial
        for i in range(20):
            make_commit(git_repo.path, f"base {i}", f"base{i}.txt", "x" * 500)

        out1 = tmp_path / "out1.tar.gz"
        run_export(_make_options(state_file, out1))
        size1 = out1.stat().st_size

        # Add only a small number of commits for the incremental export
        for i in range(2):
            make_commit(git_repo.path, f"delta {i}", f"delta{i}.txt", "y" * 100)

        out2 = tmp_path / "out2.tar.gz"
        run_export(_make_options(state_file, out2))
        size2 = out2.stat().st_size

        assert size2 < size1

    def test_state_refs_updated_after_second_export(
        self, tmp_path: pathlib.Path, state_file: pathlib.Path, git_repo: GitRepo
    ) -> None:
        out1 = tmp_path / "out1.tar.gz"
        run_export(_make_options(state_file, out1))

        new_sha = make_commit(git_repo.path, "next commit")

        out2 = tmp_path / "out2.tar.gz"
        run_export(_make_options(state_file, out2))

        repos = load_state(state_file)
        assert repos[0].last_sync is not None
        assert repos[0].last_sync.refs.get("refs/heads/main") == new_sha


class TestNoChanges:
    def test_no_archive_when_nothing_changed(
        self, tmp_path: pathlib.Path, state_file: pathlib.Path, git_repo: GitRepo
    ) -> None:
        out1 = tmp_path / "out1.tar.gz"
        run_export(_make_options(state_file, out1))

        out2 = tmp_path / "out2.tar.gz"
        summary = run_export(_make_options(state_file, out2))

        assert not out2.exists()
        assert "test/repo" in summary.skipped
        assert summary.succeeded == []

    def test_state_timestamp_updated_even_when_skipped(
        self, tmp_path: pathlib.Path, state_file: pathlib.Path, git_repo: GitRepo
    ) -> None:
        out1 = tmp_path / "out1.tar.gz"
        run_export(_make_options(state_file, out1))
        repos_after_first = load_state(state_file)
        ts1 = repos_after_first[0].last_sync.timestamp

        import time

        time.sleep(1.1)  # ensure timestamp changes

        out2 = tmp_path / "out2.tar.gz"
        run_export(_make_options(state_file, out2))
        repos_after_second = load_state(state_file)
        ts2 = repos_after_second[0].last_sync.timestamp

        assert ts2 > ts1


class TestLFSHandling:
    def _lfs_state(self, lfs_repo: GitRepo, extra: dict | None = None) -> dict:
        repo_entry: dict = {
            "id": "test/lfs",
            "source_url": str(lfs_repo.path),
            "source_local_path": str(lfs_repo.path),
            "dest_path": str(lfs_repo.bare_path),
        }
        if extra:
            repo_entry.update(extra)
        return {"version": "1", "repos": [repo_entry]}

    def test_lfs_fails_by_default(self, tmp_path: pathlib.Path, lfs_repo: GitRepo) -> None:
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps(self._lfs_state(lfs_repo)), encoding="utf-8")
        out = tmp_path / "out.tar.gz"
        summary = run_export(_make_options(state_path, out))
        assert len(summary.failed) == 1
        assert "lfs" in summary.failed[0][1].lower()

    def test_lfs_allow_mode(self, tmp_path: pathlib.Path, lfs_repo: GitRepo) -> None:
        state_path = tmp_path / "state.json"
        state_path.write_text(
            json.dumps(self._lfs_state(lfs_repo, {"lfs_mode": "allow"})), encoding="utf-8"
        )
        out = tmp_path / "out.tar.gz"
        summary = run_export(_make_options(state_path, out))
        assert len(summary.failed) == 0
        assert "test/lfs" in summary.succeeded
        assert any("lfs" in w.lower() for _, ws in summary.warned for w in ws)

    def test_lfs_skip_mode_skips_all(self, tmp_path: pathlib.Path, lfs_repo: GitRepo) -> None:
        state_path = tmp_path / "state.json"
        state_path.write_text(
            json.dumps(self._lfs_state(lfs_repo, {"lfs_mode": "skip"})), encoding="utf-8"
        )
        out = tmp_path / "out.tar.gz"
        summary = run_export(_make_options(state_path, out))
        assert len(summary.failed) == 0
        assert "test/lfs" in summary.skipped
        assert any("lfs" in w.lower() for _, ws in summary.warned for w in ws)

    def test_lfs_skip_mode_bundles_non_lfs_branch(
        self, tmp_path: pathlib.Path, mixed_lfs_repo: GitRepo
    ) -> None:
        state = {
            "version": "1",
            "repos": [
                {
                    "id": "test/mixed",
                    "source_url": str(mixed_lfs_repo.path),
                    "source_local_path": str(mixed_lfs_repo.path),
                    "dest_path": str(mixed_lfs_repo.bare_path),
                    "lfs_mode": "skip",
                }
            ],
        }
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        out = tmp_path / "out.tar.gz"
        summary = run_export(_make_options(state_path, out))
        assert len(summary.failed) == 0
        assert "test/mixed" in summary.succeeded
        assert out.exists()
        assert any("lfs" in w.lower() for _, ws in summary.warned for w in ws)


class TestRebaseHandling:
    def test_rebase_fails_by_default(
        self, tmp_path: pathlib.Path, state_file: pathlib.Path, git_repo: GitRepo
    ) -> None:
        out1 = tmp_path / "out1.tar.gz"
        run_export(_make_options(state_file, out1))

        force_rebase(git_repo.path)

        out2 = tmp_path / "out2.tar.gz"
        summary = run_export(_make_options(state_file, out2))
        assert len(summary.failed) == 1
        assert "rebase" in summary.failed[0][1].lower() or "force" in summary.failed[0][1].lower()

    def test_rebase_allowed_with_flag(
        self, tmp_path: pathlib.Path, state_file: pathlib.Path, git_repo: GitRepo
    ) -> None:
        out1 = tmp_path / "out1.tar.gz"
        run_export(_make_options(state_file, out1))

        force_rebase(git_repo.path)

        out2 = tmp_path / "out2.tar.gz"
        summary = run_export(_make_options(state_file, out2, allow_rebase=True))
        assert len(summary.failed) == 0
        assert "test/repo" in summary.succeeded


class TestTagMoved:
    def test_tag_moved_fails(self, tmp_path: pathlib.Path, git_repo: GitRepo) -> None:
        # Create tag at a second commit so it can be moved backward to root
        make_commit(git_repo.path, "second commit before tag")
        _git(["tag", "-a", "v1.0.0", "-m", "release at second commit"], git_repo.path)
        state = {
            "version": "1",
            "repos": [
                {
                    "id": "test/repo",
                    "source_url": str(git_repo.path),
                    "source_local_path": str(git_repo.path),
                    "dest_path": str(git_repo.bare_path),
                }
            ],
        }
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps(state), encoding="utf-8")

        out1 = tmp_path / "out1.tar.gz"
        run_export(_make_options(state_path, out1))

        # Move tag backward to root commit — root is not a descendant of the tagged commit
        initial = _git(["rev-list", "--max-parents=0", "HEAD"], git_repo.path).stdout.strip()
        move_tag(git_repo.path, "v1.0.0", initial)

        out2 = tmp_path / "out2.tar.gz"
        summary = run_export(_make_options(state_path, out2))
        assert len(summary.failed) == 1
        assert "tag" in summary.failed[0][1].lower()


class TestCaseConflict:
    def test_case_conflict_fails(
        self, tmp_path: pathlib.Path, state_file: pathlib.Path, git_repo: GitRepo
    ) -> None:
        # We can't actually create two refs that differ only by case on a case-insensitive FS
        # without a special workaround, so we test via plan_bundle directly with injected refs.
        # For the export integration, we patch list_refs to return conflicting refs.
        import unittest.mock as mock

        conflicting = {
            "refs/heads/main": "a" * 40,
            "refs/heads/Main": "b" * 40,
        }

        out = tmp_path / "out.tar.gz"
        with mock.patch("sync_tools.export_cmd.list_refs", return_value=conflicting):
            summary = run_export(_make_options(state_file, out))

        assert len(summary.failed) == 1
        assert "case" in summary.failed[0][1].lower()


class TestDryRun:
    def test_no_archive_created(
        self, tmp_path: pathlib.Path, state_file: pathlib.Path, git_repo: GitRepo
    ) -> None:
        out = tmp_path / "out.tar.gz"
        run_export(_make_options(state_file, out, dry_run=True))
        assert not out.exists()

    def test_state_not_updated(
        self, tmp_path: pathlib.Path, state_file: pathlib.Path, git_repo: GitRepo
    ) -> None:
        out = tmp_path / "out.tar.gz"
        run_export(_make_options(state_file, out, dry_run=True))
        repos = load_state(state_file)
        assert repos[0].last_sync is None  # state unchanged


class TestPartialFailure:
    def test_one_succeeds_one_fails(self, tmp_path: pathlib.Path, git_repo: GitRepo) -> None:
        # Add a second repo pointing to a non-existent path
        state = {
            "version": "1",
            "repos": [
                {
                    "id": "test/good",
                    "source_url": str(git_repo.path),
                    "source_local_path": str(git_repo.path),
                    "dest_path": str(git_repo.bare_path),
                },
                {
                    "id": "test/bad",
                    "source_url": "file:///nonexistent/repo.git",
                    "dest_path": str(tmp_path / "bad-dest.git"),
                },
            ],
        }
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        out = tmp_path / "out.tar.gz"
        summary = run_export(_make_options(state_path, out, workers=2))

        assert "test/good" in summary.succeeded
        assert any(repo_id == "test/bad" for repo_id, _ in summary.failed)
        # Archive still created for the one that succeeded
        assert out.exists()
