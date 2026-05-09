from __future__ import annotations

import json
import pathlib

from sync_tools.export_cmd import ExportOptions, run_export
from sync_tools.git_ops import is_git_repo, list_refs, resolve_ref
from sync_tools.import_cmd import ImportOptions, run_import

from .conftest import GitRepo, _git, make_commit


def _export(
    state_path: pathlib.Path,
    output_path: pathlib.Path,
    workers: int = 1,
    allow_rebase: bool = False,
) -> None:
    run_export(
        ExportOptions(
            state_path=state_path,
            output_path=output_path,
            workers=workers,
            allow_rebase=allow_rebase,
            dry_run=False,
        )
    )


def _import(
    archive_path: pathlib.Path,
    *,
    state_path: pathlib.Path | None = None,
    auto_init: bool = False,
    dry_run: bool = False,
    workers: int = 1,
) -> None:
    run_import(
        ImportOptions(
            archive_path=archive_path,
            state_path=state_path,
            workers=workers,
            auto_init=auto_init,
            timeout=30,
            max_retries=1,
            dry_run=dry_run,
        )
    )


class TestHappyPath:
    def test_import_applies_refs(
        self,
        tmp_path: pathlib.Path,
        state_file: pathlib.Path,
        git_repo: GitRepo,
    ) -> None:
        archive = tmp_path / "out.tar.gz"
        _export(state_file, archive)

        # Create a fresh bare dest to import into
        fresh_dest = tmp_path / "fresh-dest.git"
        _git(["init", "--bare", str(fresh_dest)], tmp_path)

        # Patch state to point to fresh dest
        state = json.loads(state_file.read_text())
        state["repos"][0]["dest_path"] = str(fresh_dest)
        patched_state = tmp_path / "patched_state.json"
        patched_state.write_text(json.dumps(state), encoding="utf-8")

        result = run_import(
            ImportOptions(
                archive_path=archive,
                state_path=patched_state,
                workers=1,
                auto_init=False,
                timeout=30,
                max_retries=1,
                dry_run=False,
            )
        )
        assert result.failed == []
        assert "test/repo" in result.succeeded

        # Verify refs exist in dest
        dest_refs = list_refs(fresh_dest)
        assert "refs/heads/main" in dest_refs

    def test_refs_match_exported_shas(
        self,
        tmp_path: pathlib.Path,
        state_file: pathlib.Path,
        git_repo: GitRepo,
    ) -> None:
        # Get SHA before export
        src_sha = resolve_ref(git_repo.path, "refs/heads/main")

        archive = tmp_path / "out.tar.gz"
        _export(state_file, archive)

        fresh_dest = tmp_path / "fresh-dest.git"
        _git(["init", "--bare", str(fresh_dest)], tmp_path)

        state = json.loads(state_file.read_text())
        state["repos"][0]["dest_path"] = str(fresh_dest)
        patched_state = tmp_path / "patched_state.json"
        patched_state.write_text(json.dumps(state), encoding="utf-8")

        run_import(
            ImportOptions(
                archive_path=archive,
                state_path=patched_state,
                workers=1,
                auto_init=False,
                timeout=30,
                max_retries=1,
                dry_run=False,
            )
        )

        dest_sha = resolve_ref(fresh_dest, "refs/heads/main")
        assert dest_sha == src_sha


class TestIncrementalImport:
    def test_two_rounds_accumulate_commits(
        self,
        tmp_path: pathlib.Path,
        state_file: pathlib.Path,
        git_repo: GitRepo,
    ) -> None:
        fresh_dest = tmp_path / "fresh-dest.git"
        _git(["init", "--bare", str(fresh_dest)], tmp_path)

        state = json.loads(state_file.read_text())
        state["repos"][0]["dest_path"] = str(fresh_dest)
        patched_state = tmp_path / "patched_state.json"
        patched_state.write_text(json.dumps(state), encoding="utf-8")

        # Round 1
        archive1 = tmp_path / "out1.tar.gz"
        _export(patched_state, archive1)
        _import(archive1, state_path=patched_state)

        # Add commits and do round 2
        sha2 = make_commit(git_repo.path, "second round commit")
        archive2 = tmp_path / "out2.tar.gz"
        _export(patched_state, archive2)
        _import(archive2, state_path=patched_state)

        dest_sha = resolve_ref(fresh_dest, "refs/heads/main")
        assert dest_sha == sha2


class TestMissingDest:
    def test_missing_dest_fails_without_auto_init(
        self,
        tmp_path: pathlib.Path,
        state_file: pathlib.Path,
        git_repo: GitRepo,
    ) -> None:
        archive = tmp_path / "out.tar.gz"
        _export(state_file, archive)

        # Point dest to a non-existent path in state
        state = json.loads(state_file.read_text())
        state["repos"][0]["dest_path"] = str(tmp_path / "nonexistent.git")
        patched_state = tmp_path / "patched.json"
        patched_state.write_text(json.dumps(state), encoding="utf-8")

        result = run_import(
            ImportOptions(
                archive_path=archive,
                state_path=patched_state,
                workers=1,
                auto_init=False,
                timeout=30,
                max_retries=1,
                dry_run=False,
            )
        )
        assert len(result.failed) == 1
        assert (
            "not found" in result.failed[0][1].lower() or "missing" in result.failed[0][1].lower()
        )

    def test_auto_init_creates_repo(
        self,
        tmp_path: pathlib.Path,
        state_file: pathlib.Path,
        git_repo: GitRepo,
    ) -> None:
        archive = tmp_path / "out.tar.gz"
        _export(state_file, archive)

        new_dest = tmp_path / "new-dest.git"
        state = json.loads(state_file.read_text())
        state["repos"][0]["dest_path"] = str(new_dest)
        patched_state = tmp_path / "patched.json"
        patched_state.write_text(json.dumps(state), encoding="utf-8")

        result = run_import(
            ImportOptions(
                archive_path=archive,
                state_path=patched_state,
                workers=1,
                auto_init=True,
                timeout=30,
                max_retries=1,
                dry_run=False,
            )
        )
        assert result.failed == []
        assert is_git_repo(new_dest)
        assert "refs/heads/main" in list_refs(new_dest)


class TestMissingPrerequisite:
    def test_incomplete_dest_fails_verify(
        self,
        tmp_path: pathlib.Path,
        state_file: pathlib.Path,
        git_repo: GitRepo,
    ) -> None:
        """
        Export a repo, add more commits, export again (incremental).
        Try to import the incremental bundle into an empty bare repo.
        This should fail because the prerequisite commits are missing.
        """
        # First export — establishes baseline
        archive1 = tmp_path / "out1.tar.gz"
        _export(state_file, archive1)

        # Add commits and create incremental export
        make_commit(git_repo.path, "new commit after first export")
        archive2 = tmp_path / "out2.tar.gz"
        _export(state_file, archive2)

        # Try to import incremental bundle into a fresh (empty) repo
        fresh_dest = tmp_path / "empty-dest.git"
        _git(["init", "--bare", str(fresh_dest)], tmp_path)

        state = json.loads(state_file.read_text())
        state["repos"][0]["dest_path"] = str(fresh_dest)
        patched_state = tmp_path / "patched.json"
        patched_state.write_text(json.dumps(state), encoding="utf-8")

        result = run_import(
            ImportOptions(
                archive_path=archive2,
                state_path=patched_state,
                workers=1,
                auto_init=False,
                timeout=30,
                max_retries=1,
                dry_run=False,
            )
        )
        assert len(result.failed) == 1
        error_msg = result.failed[0][1].lower()
        assert "prerequisite" in error_msg or "missing" in error_msg or "verify" in error_msg


class TestLFSSyncImport:
    def _lfs_sync_state(self, lfs_repo: GitRepo, dest_path: pathlib.Path) -> dict:
        return {
            "version": "1",
            "repos": [
                {
                    "id": "test/lfs",
                    "source_url": str(lfs_repo.path),
                    "source_local_path": str(lfs_repo.path),
                    "dest_path": str(dest_path),
                    "lfs_mode": "sync",
                }
            ],
        }

    def test_lfs_objects_copied_to_dest(self, tmp_path: pathlib.Path, lfs_repo: GitRepo) -> None:
        fresh_dest = tmp_path / "fresh-dest.git"
        _git(["init", "--bare", str(fresh_dest)], tmp_path)

        state_path = tmp_path / "state.json"
        state_path.write_text(
            __import__("json").dumps(self._lfs_sync_state(lfs_repo, fresh_dest)),
            encoding="utf-8",
        )

        archive = tmp_path / "out.tar.gz"
        _export(state_path, archive)

        # Import into fresh dest
        state_path2 = tmp_path / "state2.json"
        import json as _json

        state2 = _json.loads(state_path.read_text())
        state2["repos"][0]["dest_path"] = str(fresh_dest)
        state_path2.write_text(_json.dumps(state2), encoding="utf-8")

        _import(archive, state_path=state_path2)

        # Verify LFS objects exist in the destination
        dest_lfs = fresh_dest / "lfs" / "objects"
        assert dest_lfs.exists()
        lfs_files = list(dest_lfs.rglob("*"))
        lfs_files = [f for f in lfs_files if f.is_file()]
        assert len(lfs_files) > 0

    def test_dry_run_skips_lfs_copy(self, tmp_path: pathlib.Path, lfs_repo: GitRepo) -> None:
        fresh_dest = tmp_path / "fresh-dest.git"
        _git(["init", "--bare", str(fresh_dest)], tmp_path)

        state_path = tmp_path / "state.json"
        state_path.write_text(
            __import__("json").dumps(self._lfs_sync_state(lfs_repo, fresh_dest)),
            encoding="utf-8",
        )

        archive = tmp_path / "out.tar.gz"
        _export(state_path, archive)

        import json as _json

        state2 = _json.loads(state_path.read_text())
        state2["repos"][0]["dest_path"] = str(fresh_dest)
        state_path2 = tmp_path / "state2.json"
        state_path2.write_text(_json.dumps(state2), encoding="utf-8")

        _import(archive, state_path=state_path2, dry_run=True)

        dest_lfs = fresh_dest / "lfs" / "objects"
        assert not dest_lfs.exists()


class TestDryRun:
    def test_dry_run_does_not_modify_dest(
        self,
        tmp_path: pathlib.Path,
        state_file: pathlib.Path,
        git_repo: GitRepo,
    ) -> None:
        archive = tmp_path / "out.tar.gz"
        _export(state_file, archive)

        fresh_dest = tmp_path / "fresh-dest.git"
        _git(["init", "--bare", str(fresh_dest)], tmp_path)

        state = json.loads(state_file.read_text())
        state["repos"][0]["dest_path"] = str(fresh_dest)
        patched_state = tmp_path / "patched.json"
        patched_state.write_text(json.dumps(state), encoding="utf-8")

        result = run_import(
            ImportOptions(
                archive_path=archive,
                state_path=patched_state,
                workers=1,
                auto_init=False,
                timeout=30,
                max_retries=1,
                dry_run=True,
            )
        )
        assert result.failed == []
        # Dest should be empty (verify ran but fetch was skipped)
        dest_refs = list_refs(fresh_dest)
        assert dest_refs == {}
