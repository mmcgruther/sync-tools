# sync-tools

Python CLI for syncing git repositories, Docker images, and PyPI packages across air-gapped networks.

## Development setup

```bash
pip install -e ".[dev]"
```

## Running tests

```bash
pytest
```

All tests use real subprocess calls on real temporary state. There is no mocking of git or pip.

Tests that require `git-lfs` skip automatically if it is not installed.

Tests that require `pip` use `--no-index --find-links` with locally created minimal wheels — no network access needed.

## Project structure

```
src/sync_tools/
  errors.py           — exception hierarchy and RepoResult dataclass
  state.py            — git JSON state file: load_state(), save_state(), RepoConfig, LastSync
  git_ops.py          — all git subprocess calls (only module that invokes git directly)
  bundle.py           — plan_bundle(), execute_bundle(), BundlePlan, BundleResult
  archive.py          — git tar.gz creation/extraction with zip-slip guard
  export_cmd.py       — run_export(): ThreadPoolExecutor orchestration
  import_cmd.py       — run_import(): parallel bundle application
  docker_state.py     — Docker JSON state file: ImageConfig, LastImageSync
  docker_ops.py       — docker subprocess calls
  docker_bundle.py    — plan_image_bundle(), execute_image_bundle()
  docker_archive.py   — Docker tar.gz creation/extraction
  docker_export_cmd.py — run_docker_export()
  docker_import_cmd.py — run_docker_import()
  pypi_state.py       — PyPI JSON state file: PackageConfig, LastPackageSync
  pypi_ops.py         — pip subprocess calls: pip_download(), compute_file_sha256()
  pypi_bundle.py      — plan_package_bundle(), execute_package_bundle()
  pypi_archive.py     — PyPI tar.gz creation/extraction (pypi_manifest.json)
  pypi_export_cmd.py  — run_pypi_export()
  pypi_import_cmd.py  — run_pypi_import()
  cli.py              — Click entry points for all six commands

tests/
  conftest.py         — shared fixtures and helpers (make_commit, make_wheel, pip_available, …)
  test_state.py
  test_git_ops.py
  test_bundle.py
  test_archive.py
  test_export.py      — git integration tests
  test_import.py      — git integration tests
  test_pypi_state.py
  test_pypi_archive.py
  test_pypi_export.py — PyPI integration tests (skip if pip unavailable)
  test_pypi_import.py — PyPI integration tests
```

## CLI usage

```bash
# Git repos — export (connected/source side)
sync-tools export state.json --output bundle.tar.gz
sync-tools export state.json --output bundle.tar.gz --workers 16 --allow-lfs --allow-rebase

# Git repos — import (air-gapped/destination side)
sync-tools import bundle.tar.gz --auto-init
sync-tools import bundle.tar.gz --state-file dest_state.json --dry-run

# Docker images — export / import
sync-tools docker-export docker_state.json --output images.tar.gz
sync-tools docker-import images.tar.gz

# PyPI packages — export / import
sync-tools pypi-export pypi_state.json --output packages.tar.gz
sync-tools pypi-import packages.tar.gz
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

## Archive formats

**Git bundle archive** (`.tar.gz`):
- `manifest.json` — ref snapshot at export time and `dest_path` per repo
- `bundles/<repo_id>/bundle.git` — git bundle (repo ID slashes replaced with `__`)

**Docker archive** (`.tar.gz`):
- `manifest.json` — image metadata and tag digests at export time
- `images/<image_id>/image.tar` — docker save output

**PyPI archive** (`.tar.gz`):
- `pypi_manifest.json` — package metadata and per-file sha256 at export time
- `packages/<pkg_id>/<filename>.whl` — wheel or sdist files (only new/changed since last sync)

## PyPI state file format

```json
{
  "version": "1",
  "packages": [
    {
      "id": "numpy",
      "package_name": "numpy",
      "versions": ["1.26.0", "1.26.1"],
      "dest_path": "/mnt/dest/packages/numpy",
      "source_index": "https://pypi.org/simple/",
      "python_version": "3.11",
      "platform": "manylinux_2_17_x86_64",
      "include_deps": false,
      "extra_pip_args": [],
      "last_sync": {
        "timestamp": "2024-01-15T10:30:00Z",
        "synced_files": {
          "numpy-1.26.0-cp311-cp311-manylinux_2_17_x86_64.whl": "sha256hex..."
        }
      }
    }
  ]
}
```

- `versions` must be exact pins (e.g. `"1.26.0"`), not ranges.
- `source_index` defaults to `https://pypi.org/simple/` and is omitted from the file when default.
- `platform` triggers `--only-binary :all:` automatically (cross-platform downloads cannot build source).
- `extra_pip_args` is passed verbatim to `pip download`; use `["--no-index", "--find-links", "/path"]` to source from a local directory.
- Import side copies `.whl`/`.tar.gz` files directly to `dest_path`; serve with `pip install --find-links` or a PyPI-compatible server (pypi-server, devpi).

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

New artifact types should follow the established pattern (Docker and PyPI are the reference implementations):
1. `<type>_state.py` — dataclasses + load/save functions
2. `<type>_ops.py` — subprocess calls (never called directly from cmd modules)
3. `<type>_bundle.py` — plan + execute logic with `no_changes` detection
4. `<type>_archive.py` — `<type>_manifest.json` creation/extraction with zip-slip guard
5. `<type>_export_cmd.py` / `<type>_import_cmd.py` — ThreadPoolExecutor orchestration
6. Register two subcommands in `cli.py`
