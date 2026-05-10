from __future__ import annotations

import json
import pathlib
import tarfile

import pytest

from .conftest import make_wheel
from sync_tools.pypi_export_cmd import PyPIExportOptions, run_pypi_export
from sync_tools.pypi_state import load_pypi_state

_PKG_NAME = "testpkg"
_VERSION = "1.0.0"
_VERSION2 = "1.1.0"


def _make_state(
    wheels_dir: pathlib.Path,
    dest_path: str,
    pkg_id: str = _PKG_NAME,
    versions: list[str] | None = None,
) -> dict:
    return {
        "version": "1",
        "packages": [
            {
                "id": pkg_id,
                "package_name": _PKG_NAME,
                "versions": versions or [_VERSION],
                "dest_path": dest_path,
                "extra_pip_args": ["--no-index", "--find-links", str(wheels_dir)],
            }
        ],
    }


def _options(
    state_path: pathlib.Path,
    output_path: pathlib.Path,
    dry_run: bool = False,
    workers: int = 1,
) -> PyPIExportOptions:
    return PyPIExportOptions(
        state_path=state_path,
        output_path=output_path,
        workers=workers,
        dry_run=dry_run,
    )


def _export(
    tmp_path: pathlib.Path,
    wheels_dir: pathlib.Path,
    out_name: str = "out.tar.gz",
    versions: list[str] | None = None,
    dry_run: bool = False,
) -> tuple[pathlib.Path, pathlib.Path]:
    state_path = tmp_path / "state.json"
    dest_path = tmp_path / "dest"
    state_path.write_text(
        json.dumps(_make_state(wheels_dir, str(dest_path), versions=versions)),
        encoding="utf-8",
    )
    out = tmp_path / out_name
    run_pypi_export(_options(state_path, out, dry_run=dry_run))
    return state_path, out


class TestPyPIExportHappyPath:
    def test_creates_archive(self, pip_available: None, tmp_path: pathlib.Path) -> None:
        wheels_dir = tmp_path / "wheels"
        wheels_dir.mkdir()
        make_wheel(wheels_dir, _PKG_NAME, _VERSION)

        _, out = _export(tmp_path, wheels_dir)
        assert out.exists()

    def test_archive_contains_manifest(self, pip_available: None, tmp_path: pathlib.Path) -> None:
        wheels_dir = tmp_path / "wheels"
        wheels_dir.mkdir()
        make_wheel(wheels_dir, _PKG_NAME, _VERSION)

        _, out = _export(tmp_path, wheels_dir)
        with tarfile.open(out, "r:gz") as tar:
            assert "pypi_manifest.json" in tar.getnames()

    def test_archive_contains_wheel(self, pip_available: None, tmp_path: pathlib.Path) -> None:
        wheels_dir = tmp_path / "wheels"
        wheels_dir.mkdir()
        make_wheel(wheels_dir, _PKG_NAME, _VERSION)

        _, out = _export(tmp_path, wheels_dir)
        with tarfile.open(out, "r:gz") as tar:
            names = tar.getnames()
        assert any(_PKG_NAME in n for n in names)

    def test_state_updated_after_export(self, pip_available: None, tmp_path: pathlib.Path) -> None:
        wheels_dir = tmp_path / "wheels"
        wheels_dir.mkdir()
        make_wheel(wheels_dir, _PKG_NAME, _VERSION)

        state_path, _ = _export(tmp_path, wheels_dir)
        pkgs = load_pypi_state(state_path)
        assert pkgs[0].last_sync is not None
        assert any(_PKG_NAME in f for f in pkgs[0].last_sync.synced_files)

    def test_succeeded_in_summary(self, pip_available: None, tmp_path: pathlib.Path) -> None:
        wheels_dir = tmp_path / "wheels"
        wheels_dir.mkdir()
        make_wheel(wheels_dir, _PKG_NAME, _VERSION)

        state_path = tmp_path / "state.json"
        dest_path = tmp_path / "dest"
        state_path.write_text(
            json.dumps(_make_state(wheels_dir, str(dest_path))), encoding="utf-8"
        )
        out = tmp_path / "out.tar.gz"
        summary = run_pypi_export(_options(state_path, out))
        assert len(summary.failed) == 0, summary.failed
        assert _PKG_NAME in summary.succeeded


class TestPyPIExportIncrementalSync:
    def test_second_export_skipped_when_unchanged(
        self, pip_available: None, tmp_path: pathlib.Path
    ) -> None:
        wheels_dir = tmp_path / "wheels"
        wheels_dir.mkdir()
        make_wheel(wheels_dir, _PKG_NAME, _VERSION)

        state_path, _ = _export(tmp_path, wheels_dir, "out1.tar.gz")

        out2 = tmp_path / "out2.tar.gz"
        summary = run_pypi_export(_options(state_path, out2))
        assert _PKG_NAME in summary.skipped
        assert not out2.exists()

    def test_new_version_triggers_incremental_export(
        self, pip_available: None, tmp_path: pathlib.Path
    ) -> None:
        from sync_tools.pypi_ops import compute_file_sha256
        from sync_tools.pypi_state import LastPackageSync, load_pypi_state, save_pypi_state

        wheels_dir = tmp_path / "wheels"
        wheels_dir.mkdir()
        make_wheel(wheels_dir, _PKG_NAME, _VERSION)
        make_wheel(wheels_dir, _PKG_NAME, _VERSION2)

        # Seed state: v1 already synced, v2 is new
        whl_v1 = wheels_dir / f"testpkg-{_VERSION}-py3-none-any.whl"
        state_path = tmp_path / "state.json"
        dest_path = tmp_path / "dest"
        state_path.write_text(
            json.dumps(_make_state(wheels_dir, str(dest_path), versions=[_VERSION, _VERSION2])),
            encoding="utf-8",
        )
        pkgs = load_pypi_state(state_path)
        pkgs[0].last_sync = LastPackageSync(
            timestamp="2024-01-01T00:00:00Z",
            synced_files={whl_v1.name: compute_file_sha256(whl_v1)},
        )
        save_pypi_state(state_path, pkgs)

        out2 = tmp_path / "out2.tar.gz"
        summary = run_pypi_export(_options(state_path, out2))
        assert _PKG_NAME in summary.succeeded, summary.failed
        assert out2.exists()
        # Incremental: only new version in archive
        with tarfile.open(out2, "r:gz") as tar:
            names = tar.getnames()
        assert any(_VERSION2 in n for n in names)
        assert not any(f"{_PKG_NAME}-{_VERSION}-" in n for n in names)

    def test_state_tracks_all_versions_after_incremental(
        self, pip_available: None, tmp_path: pathlib.Path
    ) -> None:
        wheels_dir = tmp_path / "wheels"
        wheels_dir.mkdir()
        make_wheel(wheels_dir, _PKG_NAME, _VERSION)
        make_wheel(wheels_dir, _PKG_NAME, _VERSION2)

        state_path = tmp_path / "state.json"
        dest_path = tmp_path / "dest"
        state_path.write_text(
            json.dumps(
                _make_state(wheels_dir, str(dest_path), versions=[_VERSION, _VERSION2])
            ),
            encoding="utf-8",
        )
        out = tmp_path / "out.tar.gz"
        run_pypi_export(_options(state_path, out))

        pkgs = load_pypi_state(state_path)
        synced = pkgs[0].last_sync.synced_files  # type: ignore[union-attr]
        assert any(_VERSION in f for f in synced)
        assert any(_VERSION2 in f for f in synced)


class TestPyPIExportDryRun:
    def test_no_archive_created(self, pip_available: None, tmp_path: pathlib.Path) -> None:
        wheels_dir = tmp_path / "wheels"
        wheels_dir.mkdir()
        make_wheel(wheels_dir, _PKG_NAME, _VERSION)

        _, out = _export(tmp_path, wheels_dir, dry_run=True)
        assert not out.exists()

    def test_state_not_updated(self, pip_available: None, tmp_path: pathlib.Path) -> None:
        wheels_dir = tmp_path / "wheels"
        wheels_dir.mkdir()
        make_wheel(wheels_dir, _PKG_NAME, _VERSION)

        state_path, _ = _export(tmp_path, wheels_dir, dry_run=True)
        pkgs = load_pypi_state(state_path)
        assert pkgs[0].last_sync is None


class TestPyPIExportFailure:
    def test_bad_package_name_fails(self, pip_available: None, tmp_path: pathlib.Path) -> None:
        wheels_dir = tmp_path / "wheels"
        wheels_dir.mkdir()
        # No wheels in dir; pip will fail to find the package

        state_path = tmp_path / "state.json"
        dest_path = tmp_path / "dest"
        state_path.write_text(
            json.dumps(_make_state(wheels_dir, str(dest_path))), encoding="utf-8"
        )
        out = tmp_path / "out.tar.gz"
        summary = run_pypi_export(_options(state_path, out))
        assert len(summary.failed) == 1
        assert _PKG_NAME in summary.failed[0][0]
