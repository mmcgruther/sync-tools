from __future__ import annotations

import json
import pathlib
import tarfile

import pytest

from .conftest import make_wheel
from sync_tools.errors import BundleError, StateFileError
from sync_tools.pypi_archive import (
    PyPIManifestEntry,
    create_pypi_archive,
    extract_pypi_archive,
)
from sync_tools.pypi_bundle import PackageBundleResult, safe_package_id
from sync_tools.pypi_ops import compute_file_sha256
from sync_tools.pypi_state import PackageConfig


def _make_config(pkg_id: str = "numpy", dest: str = "/dest/numpy") -> PackageConfig:
    return PackageConfig(
        id=pkg_id,
        package_name=pkg_id.split("/")[-1],
        versions=["1.0.0"],
        dest_path=dest,
    )


def _make_result(
    tmp_path: pathlib.Path, pkg_id: str = "numpy", filenames: list[str] | None = None
) -> PackageBundleResult:
    safe_id = safe_package_id(pkg_id)
    pkg_name = pkg_id.split("/")[-1]
    if filenames is None:
        filenames = [f"{pkg_name}-1.0.0-py3-none-any.whl"]

    pkg_dir = tmp_path / "bundle" / safe_id
    pkg_dir.mkdir(parents=True)

    synced: dict[str, str] = {}
    for name in filenames:
        p = pkg_dir / name
        p.write_bytes(b"fake wheel content: " + name.encode())
        synced[name] = compute_file_sha256(p)

    return PackageBundleResult(
        config=_make_config(pkg_id),
        package_dir=pkg_dir,
        synced_files=synced,
        all_synced_files=synced,
    )


class TestCreatePyPIArchive:
    def test_creates_tar_gz(self, tmp_path: pathlib.Path) -> None:
        result = _make_result(tmp_path)
        out = tmp_path / "out.tar.gz"
        create_pypi_archive([result], out, tmp_path)
        assert out.exists()

    def test_archive_contains_manifest(self, tmp_path: pathlib.Path) -> None:
        result = _make_result(tmp_path)
        out = tmp_path / "out.tar.gz"
        create_pypi_archive([result], out, tmp_path)
        with tarfile.open(out, "r:gz") as tar:
            assert "pypi_manifest.json" in tar.getnames()

    def test_archive_contains_wheel_file(self, tmp_path: pathlib.Path) -> None:
        result = _make_result(tmp_path)
        out = tmp_path / "out.tar.gz"
        create_pypi_archive([result], out, tmp_path)
        with tarfile.open(out, "r:gz") as tar:
            names = tar.getnames()
        assert any("numpy-1.0.0-py3-none-any.whl" in n for n in names)

    def test_manifest_type_field(self, tmp_path: pathlib.Path) -> None:
        result = _make_result(tmp_path)
        out = tmp_path / "out.tar.gz"
        create_pypi_archive([result], out, tmp_path)
        with tarfile.open(out, "r:gz") as tar:
            raw = json.loads(tar.extractfile("pypi_manifest.json").read())  # type: ignore[union-attr]
        assert raw["type"] == "pypi"

    def test_multiple_packages(self, tmp_path: pathlib.Path) -> None:
        r1 = _make_result(tmp_path, "numpy")
        r2 = _make_result(tmp_path, "scipy")
        out = tmp_path / "out.tar.gz"
        create_pypi_archive([r1, r2], out, tmp_path)
        with tarfile.open(out, "r:gz") as tar:
            names = tar.getnames()
        assert any("numpy" in n for n in names)
        assert any("scipy" in n for n in names)

    def test_safe_id_replaces_slashes(self, tmp_path: pathlib.Path) -> None:
        result = _make_result(tmp_path, "org/mylib")
        out = tmp_path / "out.tar.gz"
        create_pypi_archive([result], out, tmp_path)
        with tarfile.open(out, "r:gz") as tar:
            names = tar.getnames()
        assert any("org__mylib" in n for n in names)


class TestExtractPyPIArchive:
    def _round_trip(self, tmp_path: pathlib.Path, result: PackageBundleResult) -> tuple:
        out = tmp_path / "out.tar.gz"
        create_pypi_archive([result], out, tmp_path)
        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        manifest = extract_pypi_archive(out, extract_dir)
        return manifest, extract_dir

    def test_manifest_parsed(self, tmp_path: pathlib.Path) -> None:
        result = _make_result(tmp_path)
        manifest, _ = self._round_trip(tmp_path, result)
        assert len(manifest.entries) == 1
        assert manifest.entries[0].id == "numpy"
        assert manifest.entries[0].package_name == "numpy"
        assert manifest.entries[0].dest_path == "/dest/numpy"

    def test_synced_files_in_manifest(self, tmp_path: pathlib.Path) -> None:
        result = _make_result(tmp_path)
        manifest, _ = self._round_trip(tmp_path, result)
        assert "numpy-1.0.0-py3-none-any.whl" in manifest.entries[0].synced_files

    def test_files_extracted_to_correct_path(self, tmp_path: pathlib.Path) -> None:
        result = _make_result(tmp_path)
        manifest, extract_dir = self._round_trip(tmp_path, result)
        entry = manifest.entries[0]
        wheel = extract_dir / entry.package_dir / "numpy-1.0.0-py3-none-any.whl"
        assert wheel.exists()

    def test_missing_manifest_raises(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "empty.tar.gz"
        with tarfile.open(out, "w:gz"):
            pass
        with pytest.raises(StateFileError, match="pypi_manifest.json"):
            extract_pypi_archive(out, tmp_path / "ex")

    def test_path_traversal_raises(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "evil.tar.gz"
        with tarfile.open(out, "w:gz") as tar:
            import io

            data = b"evil"
            info = tarfile.TarInfo(name="../../evil.txt")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        with pytest.raises(BundleError, match="path traversal"):
            extract_pypi_archive(out, tmp_path / "ex")

    def test_wrong_manifest_version_raises(self, tmp_path: pathlib.Path) -> None:
        bad = {"version": "99", "type": "pypi", "export_timestamp": "x", "entries": []}
        out = tmp_path / "bad.tar.gz"
        manifest_path = tmp_path / "pypi_manifest.json"
        manifest_path.write_text(json.dumps(bad), encoding="utf-8")
        with tarfile.open(out, "w:gz") as tar:
            tar.add(str(manifest_path), arcname="pypi_manifest.json")
        with pytest.raises(StateFileError, match="Unsupported"):
            extract_pypi_archive(out, tmp_path / "ex")
