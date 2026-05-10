from __future__ import annotations

import json
import pathlib

import pytest

from sync_tools.errors import StateFileError
from sync_tools.pypi_state import LastPackageSync, PackageConfig, load_pypi_state, save_pypi_state


def _write(path: pathlib.Path, data: dict) -> pathlib.Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _minimal_state(extra: dict | None = None) -> dict:
    pkg: dict = {
        "id": "numpy",
        "package_name": "numpy",
        "versions": ["1.26.0"],
        "dest_path": "/dest/numpy",
    }
    if extra:
        pkg.update(extra)
    return {"version": "1", "packages": [pkg]}


class TestLoadPyPIState:
    def test_missing_file_raises(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(StateFileError, match="not found"):
            load_pypi_state(tmp_path / "nope.json")

    def test_invalid_json_raises(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("not json", encoding="utf-8")
        with pytest.raises(StateFileError, match="not valid JSON"):
            load_pypi_state(p)

    def test_wrong_version_raises(self, tmp_path: pathlib.Path) -> None:
        p = _write(tmp_path / "s.json", {"version": "99", "packages": []})
        with pytest.raises(StateFileError, match="Unsupported"):
            load_pypi_state(p)

    def test_missing_packages_key_raises(self, tmp_path: pathlib.Path) -> None:
        p = _write(tmp_path / "s.json", {"version": "1"})
        with pytest.raises(StateFileError, match="'packages' list"):
            load_pypi_state(p)

    def test_minimal_valid_state(self, tmp_path: pathlib.Path) -> None:
        p = _write(tmp_path / "s.json", _minimal_state())
        pkgs = load_pypi_state(p)
        assert len(pkgs) == 1
        pkg = pkgs[0]
        assert pkg.id == "numpy"
        assert pkg.package_name == "numpy"
        assert pkg.versions == ["1.26.0"]
        assert pkg.dest_path == "/dest/numpy"
        assert pkg.source_index == "https://pypi.org/simple/"
        assert pkg.python_version is None
        assert pkg.platform is None
        assert pkg.include_deps is False
        assert pkg.extra_pip_args == []
        assert pkg.last_sync is None

    def test_all_optional_fields_parsed(self, tmp_path: pathlib.Path) -> None:
        data = _minimal_state(
            {
                "source_index": "https://internal.corp/simple/",
                "python_version": "3.11",
                "platform": "manylinux_2_17_x86_64",
                "include_deps": True,
                "extra_pip_args": ["--prefer-binary"],
            }
        )
        p = _write(tmp_path / "s.json", data)
        pkg = load_pypi_state(p)[0]
        assert pkg.source_index == "https://internal.corp/simple/"
        assert pkg.python_version == "3.11"
        assert pkg.platform == "manylinux_2_17_x86_64"
        assert pkg.include_deps is True
        assert pkg.extra_pip_args == ["--prefer-binary"]

    def test_last_sync_parsed(self, tmp_path: pathlib.Path) -> None:
        data = _minimal_state(
            {
                "last_sync": {
                    "timestamp": "2024-01-15T10:30:00Z",
                    "synced_files": {"numpy-1.26.0-py3-none-any.whl": "abc123"},
                }
            }
        )
        p = _write(tmp_path / "s.json", data)
        pkg = load_pypi_state(p)[0]
        assert pkg.last_sync is not None
        assert pkg.last_sync.timestamp == "2024-01-15T10:30:00Z"
        assert pkg.last_sync.synced_files == {"numpy-1.26.0-py3-none-any.whl": "abc123"}

    def test_missing_id_raises(self, tmp_path: pathlib.Path) -> None:
        data = {"version": "1", "packages": [{"package_name": "x", "versions": ["1.0"], "dest_path": "/d"}]}
        p = _write(tmp_path / "s.json", data)
        with pytest.raises(StateFileError, match="'id'"):
            load_pypi_state(p)

    def test_empty_versions_raises(self, tmp_path: pathlib.Path) -> None:
        data = _minimal_state({"versions": []})
        p = _write(tmp_path / "s.json", data)
        with pytest.raises(StateFileError, match="'versions'"):
            load_pypi_state(p)

    def test_non_string_version_raises(self, tmp_path: pathlib.Path) -> None:
        data = _minimal_state({"versions": [1, 2]})
        p = _write(tmp_path / "s.json", data)
        with pytest.raises(StateFileError, match="version must be a string"):
            load_pypi_state(p)

    def test_invalid_last_sync_raises(self, tmp_path: pathlib.Path) -> None:
        data = _minimal_state({"last_sync": "bad"})
        p = _write(tmp_path / "s.json", data)
        with pytest.raises(StateFileError, match="'last_sync'"):
            load_pypi_state(p)


class TestSavePyPIState:
    def test_roundtrip_minimal(self, tmp_path: pathlib.Path) -> None:
        pkg = PackageConfig(
            id="scipy",
            package_name="scipy",
            versions=["1.11.0"],
            dest_path="/dest/scipy",
        )
        path = tmp_path / "out.json"
        save_pypi_state(path, [pkg])
        loaded = load_pypi_state(path)
        assert len(loaded) == 1
        assert loaded[0].id == "scipy"
        assert loaded[0].versions == ["1.11.0"]
        assert loaded[0].source_index == "https://pypi.org/simple/"

    def test_roundtrip_with_last_sync(self, tmp_path: pathlib.Path) -> None:
        pkg = PackageConfig(
            id="mylib",
            package_name="mylib",
            versions=["2.0.0"],
            dest_path="/d",
            last_sync=LastPackageSync(
                timestamp="2024-01-01T00:00:00Z",
                synced_files={"mylib-2.0.0-py3-none-any.whl": "deadbeef"},
            ),
        )
        path = tmp_path / "out.json"
        save_pypi_state(path, [pkg])
        loaded = load_pypi_state(path)
        assert loaded[0].last_sync is not None
        assert loaded[0].last_sync.synced_files == {"mylib-2.0.0-py3-none-any.whl": "deadbeef"}

    def test_default_index_not_serialized(self, tmp_path: pathlib.Path) -> None:
        pkg = PackageConfig(id="x", package_name="x", versions=["1.0"], dest_path="/d")
        path = tmp_path / "out.json"
        save_pypi_state(path, [pkg])
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert "source_index" not in raw["packages"][0]

    def test_custom_index_is_serialized(self, tmp_path: pathlib.Path) -> None:
        pkg = PackageConfig(
            id="x",
            package_name="x",
            versions=["1.0"],
            dest_path="/d",
            source_index="https://internal/simple/",
        )
        path = tmp_path / "out.json"
        save_pypi_state(path, [pkg])
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["packages"][0]["source_index"] == "https://internal/simple/"

    def test_atomic_write(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "state.json"
        save_pypi_state(path, [])
        assert path.exists()
        assert not (tmp_path / "state.json.tmp").exists()
