# sync-tools

Python CLI for syncing git repositories (and eventually binary packages and Docker images) across air-gapped networks using git bundles.

## Development setup

```bash
pip install -e ".[dev]"
```

## Running tests

```bash
pytest
```

All tests use real git subprocess calls on temporary repositories. There is no mocking of git.

Tests that require `git-lfs` skip automatically if it is not installed.

## Project structure

```
src/sync_tools/
  errors.py      — exception hierarchy and RepoResult dataclass
  state.py       — JSON state file: load_state(), save_state(), RepoConfig, LastSync
  git_ops.py     — all git subprocess calls (only module that invokes git directly)
  bundle.py      — plan_bundle(), execute_bundle(), BundlePlan, BundleResult
  archive.py     — tar.gz creation and extraction with zip-slip guard
  export_cmd.py  — run_export(): ThreadPoolExecutor orchestration
  import_cmd.py  — run_import(): parallel bundle application
  cli.py         — Click entry points: sync-tools export / sync-tools import

tests/
  conftest.py    — shared fixtures (git_repo, lfs_repo, state_file) and helpers
                   (make_commit, force_rebase, move_tag)
  test_state.py
  test_git_ops.py
  test_bundle.py
  test_archive.py
  test_export.py  — integration tests
  test_import.py  — integration tests
```

## CLI usage

```bash
# Export (connected/source side)
sync-tools export state.json --output bundle.tar.gz
sync-tools export state.json --output bundle.tar.gz --workers 16 --allow-lfs --allow-rebase

# Import (air-gapped/destination side)
sync-tools import bundle.tar.gz --auto-init
sync-tools import bundle.tar.gz --state-file dest_state.json --dry-run
```

Exit codes: `0` = all succeeded, `1` = partial failure, `2` = fatal error.

## State file format

```json
{
  "version": "1",
  "repos": [
    {
      "id": "org/repo",
      "source_url": "https://git.internal/org/repo.git",
      "source_local_path": "/mirrors/org/repo.git",
      "dest_path": "/mnt/dest/org/repo.git",
      "last_sync": {
        "timestamp": "2024-01-15T10:30:00Z",
        "refs": {
          "refs/heads/main": "abc123...",
          "refs/tags/v1.0.0": "def456..."
        }
      }
    }
  ]
}
```

`source_local_path` is optional. When absent, `source_url` is mirror-cloned into a temp directory during export. `last_sync` is absent on first run (triggers a full bundle).

## Archive format

A `.tar.gz` containing:
- `manifest.json` — ref snapshot at export time and `dest_path` per repo
- `bundles/<repo_id>/bundle.git` — git bundle (repo ID slashes replaced with `__`)

## Key design decisions

- **One bundle per repo** — git resolves cross-ref object dependencies correctly within a single bundle; avoids import ordering problems.
- **ThreadPoolExecutor, not multiprocessing** — git operations are I/O-bound subprocess calls that release the GIL; no serialization overhead.
- **Raw subprocess, not GitPython** — git bundle support requires exact command control.
- **`--allow-lfs` and `--allow-rebase` are explicit opt-in flags** — air-gapped sync is high-stakes; missing LFS data or partial history silently corrupts the destination.
- **Import does not update the state file** — the archive manifest carries the authoritative ref snapshot from the exporting side.
- **`+` prefix on all import refspecs** — required for force-update after a rebase; safe for normal fast-forwards.

## Edge cases handled

| Scenario | Behavior |
|---|---|
| Git LFS objects detected | Fail (bundles contain only pointers). Override with `--allow-lfs`. |
| Tag moved to non-descendant commit | Always fail. Tags are treated as immutable. |
| Branch force-pushed / rebased | Fail by default. Override with `--allow-rebase` (creates full bundle for that branch). |
| Refs differing only by case | Always fail. Case conflicts break Windows case-insensitive checkout. |
| Ref name is path-component prefix of another (e.g. `bugfix` and `bugfix/a`) | Always fail. Cannot coexist on a standard filesystem. |
| Missing prerequisite objects on import | Fail with `MissingPrerequisiteError`; clear message. |
| Missing destination repo on import | Fail with `MissingDestRepoError`. Override with `--auto-init`. |
| No changes since last sync | Silently skipped; state timestamp still updated. |
| Partial failure (N of M repos fail) | Archive created for successful repos; state updated for those; exit code 1. |

## Adding new artifact types

Future modules (binary packages, Docker images) should follow the same pattern:
1. A dedicated module under `src/sync_tools/` with `export_<type>.py` / `import_<type>.py`
2. New subcommands registered in `cli.py`
3. The archive format is extensible — add new entry types to `manifest.json`
