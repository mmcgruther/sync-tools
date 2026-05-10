from __future__ import annotations

import pathlib

import pytest

from .conftest import make_wheel
from sync_tools.errors import SyncToolsError
from sync_tools.pypi_archive import create_pypi_archive
from sync_tools.pypi_bundle import PackageBundleResult
from sync_tools.pypi_import_cmd import PyPIImportOptions, run_pypi_import
from sync_tools.pypi_ops import compute_file_sha256
from sync_tools.pypi_state import PackageConfig


def _make_config(pkg_id: str = "numpy", dest_path: str = "/dest/numpy") -> PackageConfig:
    return PackageConfig(
        id=pkg_id,
        package_name=pkg_id,
        versions=["1.0.0"],
        dest_path=dest_path,
    )


def _make_bundle_result(
    tmp_path: pathlib.Path, pkg_id: str, dest_path: str
) -> PackageBundleResult:
    wheels_dir = tmp_path / "wheels" / pkg_id
    wheels_dir.mkdir(parents=True)
    whl = make_wheel(wheels_dir, pkg_id, "1.0.0")
    synced = {whl.name: compute_file_sha256(whl)}
    return PackageBundleResult(
        config=_make_config(pkg_id, dest_path),
        package_dir=wheels_dir,
        synced_files=synced,
        all_synced_files=synced,
    )


def _make_archive(tmp_path: pathlib.Path, dest_path: str) -> pathlib.Path:
    result = _make_bundle_result(tmp_path, "numpy", dest_path)
    out = tmp_path / "out.tar.gz"
    create_pypi_archive([result], out, tmp_path)
    return out


def _options(
    archive: pathlib.Path, dry_run: bool = False, workers: int = 1
) -> PyPIImportOptions:
    return PyPIImportOptions(archive_path=archive, workers=workers, dry_run=dry_run)


class TestPyPIImportHappyPath:
    def test_copies_files_to_dest(self, tmp_path: pathlib.Path) -> None:
        dest = tmp_path / "dest"
        archive = _make_archive(tmp_path, str(dest))
        summary = run_pypi_import(_options(archive))
        assert len(summary.failed) == 0, summary.failed
        assert "numpy" in summary.succeeded
        assert any(dest.glob("numpy-*.whl"))

    def test_dest_dir_created_if_missing(self, tmp_path: pathlib.Path) -> None:
        dest = tmp_path / "new" / "nested" / "dest"
        archive = _make_archive(tmp_path, str(dest))
        run_pypi_import(_options(archive))
        assert dest.exists()

    def test_sha256_verified_on_import(self, tmp_path: pathlib.Path) -> None:
        dest = tmp_path / "dest"
        archive = _make_archive(tmp_path, str(dest))
        summary = run_pypi_import(_options(archive))
        assert len(summary.failed) == 0

    def test_multiple_packages_imported(self, tmp_path: pathlib.Path) -> None:
        r1 = _make_bundle_result(tmp_path, "numpy", str(tmp_path / "dest_numpy"))
        r2 = _make_bundle_result(tmp_path, "scipy", str(tmp_path / "dest_scipy"))
        out = tmp_path / "out.tar.gz"
        create_pypi_archive([r1, r2], out, tmp_path)

        summary = run_pypi_import(_options(out))
        assert len(summary.failed) == 0
        assert "numpy" in summary.succeeded
        assert "scipy" in summary.succeeded

    def test_imported_file_content_matches(self, tmp_path: pathlib.Path) -> None:
        dest = tmp_path / "dest"
        result = _make_bundle_result(tmp_path, "numpy", str(dest))
        out = tmp_path / "out.tar.gz"
        create_pypi_archive([result], out, tmp_path)

        run_pypi_import(_options(out))

        src_wheel = tmp_path / "wheels" / "numpy" / "numpy-1.0.0-py3-none-any.whl"
        dst_wheel = dest / "numpy-1.0.0-py3-none-any.whl"
        assert dst_wheel.exists()
        assert compute_file_sha256(dst_wheel) == compute_file_sha256(src_wheel)


class TestPyPIImportDryRun:
    def test_no_files_copied(self, tmp_path: pathlib.Path) -> None:
        dest = tmp_path / "dest"
        archive = _make_archive(tmp_path, str(dest))
        run_pypi_import(_options(archive, dry_run=True))
        assert not dest.exists()

    def test_succeeds_with_valid_archive(self, tmp_path: pathlib.Path) -> None:
        dest = tmp_path / "dest"
        archive = _make_archive(tmp_path, str(dest))
        summary = run_pypi_import(_options(archive, dry_run=True))
        assert len(summary.failed) == 0
        assert "numpy" in summary.succeeded


class TestPyPIImportFailure:
    def test_corrupt_file_in_archive_fails(self, tmp_path: pathlib.Path) -> None:
        import tarfile
        import json

        dest = tmp_path / "dest"
        result = _make_bundle_result(tmp_path, "numpy", str(dest))
        out = tmp_path / "out.tar.gz"
        create_pypi_archive([result], out, tmp_path)

        # Tamper: re-pack archive with corrupted wheel content (different bytes, same size)
        tampered = tmp_path / "tampered.tar.gz"
        import copy
        import io

        with tarfile.open(out, "r:gz") as src, tarfile.open(tampered, "w:gz") as dst:
            for member in src.getmembers():
                if member.name.endswith(".whl"):
                    bad_data = b"X" * member.size
                    info = copy.copy(member)
                    dst.addfile(info, io.BytesIO(bad_data))
                else:
                    f = src.extractfile(member)
                    if f is not None:
                        dst.addfile(member, f)
                    else:
                        dst.addfile(member)

        summary = run_pypi_import(_options(tampered))
        assert len(summary.failed) == 1
        assert "numpy" in summary.failed[0][0]
