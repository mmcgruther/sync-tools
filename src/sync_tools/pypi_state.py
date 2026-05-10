from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass, field

from .errors import StateFileError

_SUPPORTED_VERSION = "1"
_DEFAULT_INDEX = "https://pypi.org/simple/"


@dataclass
class LastPackageSync:
    timestamp: str
    synced_files: dict[str, str]  # filename -> sha256 hex digest


@dataclass
class PackageConfig:
    id: str
    package_name: str
    versions: list[str]  # exact version pins, e.g. ["1.26.0", "1.26.1"]
    dest_path: str
    source_index: str = _DEFAULT_INDEX
    python_version: str | None = None  # e.g. "3.11"
    platform: str | None = None  # e.g. "manylinux_2_17_x86_64"
    include_deps: bool = False
    extra_pip_args: list[str] = field(default_factory=list)
    last_sync: LastPackageSync | None = None


def load_pypi_state(path: pathlib.Path) -> list[PackageConfig]:
    """Read and parse the PyPI JSON state file. Raises StateFileError on any problem."""
    if not path.exists():
        raise StateFileError(f"State file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateFileError(f"State file is not valid JSON: {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise StateFileError(f"State file root must be a JSON object: {path}")

    version = raw.get("version")
    if version != _SUPPORTED_VERSION:
        raise StateFileError(
            f"Unsupported state file version {version!r} (expected {_SUPPORTED_VERSION!r}): {path}"
        )

    packages_raw = raw.get("packages")
    if not isinstance(packages_raw, list):
        raise StateFileError(f"State file missing 'packages' list: {path}")

    return [_parse_package(p, path) for p in packages_raw]


def save_pypi_state(path: pathlib.Path, packages: list[PackageConfig]) -> None:
    """Atomically write PyPI state file. Raises StateFileError on write failure."""
    data = {
        "version": _SUPPORTED_VERSION,
        "packages": [_package_to_dict(p) for p in packages],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise StateFileError(f"Failed to write state file {path}: {exc}") from exc


def _parse_package(raw: object, path: pathlib.Path) -> PackageConfig:
    if not isinstance(raw, dict):
        raise StateFileError(f"Each package entry must be a JSON object in {path}")

    def req(key: str) -> str:
        val = raw.get(key)
        if not isinstance(val, str) or not val:
            raise StateFileError(f"Package entry missing required string field '{key}' in {path}")
        return val

    versions_raw = raw.get("versions")
    if not isinstance(versions_raw, list) or not versions_raw:
        raise StateFileError(f"Package entry 'versions' must be a non-empty list in {path}")
    for v in versions_raw:
        if not isinstance(v, str):
            raise StateFileError(f"Each version must be a string in {path}")

    extra_pip_args_raw = raw.get("extra_pip_args", [])
    if not isinstance(extra_pip_args_raw, list):
        raise StateFileError(f"'extra_pip_args' must be a list in {path}")

    last_sync: LastPackageSync | None = None
    ls_raw = raw.get("last_sync")
    if ls_raw is not None:
        if not isinstance(ls_raw, dict):
            raise StateFileError(f"'last_sync' must be a JSON object in {path}")
        ts = ls_raw.get("timestamp")
        if not isinstance(ts, str):
            raise StateFileError(f"'last_sync.timestamp' must be a string in {path}")
        synced_files_raw = ls_raw.get("synced_files", {})
        if not isinstance(synced_files_raw, dict):
            raise StateFileError(f"'last_sync.synced_files' must be a JSON object in {path}")
        last_sync = LastPackageSync(timestamp=ts, synced_files=synced_files_raw)

    return PackageConfig(
        id=req("id"),
        package_name=req("package_name"),
        versions=versions_raw,
        dest_path=req("dest_path"),
        source_index=raw.get("source_index") or _DEFAULT_INDEX,
        python_version=raw.get("python_version") or None,
        platform=raw.get("platform") or None,
        include_deps=bool(raw.get("include_deps", False)),
        extra_pip_args=extra_pip_args_raw,
        last_sync=last_sync,
    )


def _package_to_dict(pkg: PackageConfig) -> dict:
    d: dict = {
        "id": pkg.id,
        "package_name": pkg.package_name,
        "versions": pkg.versions,
        "dest_path": pkg.dest_path,
    }
    if pkg.source_index != _DEFAULT_INDEX:
        d["source_index"] = pkg.source_index
    if pkg.python_version:
        d["python_version"] = pkg.python_version
    if pkg.platform:
        d["platform"] = pkg.platform
    if pkg.include_deps:
        d["include_deps"] = pkg.include_deps
    if pkg.extra_pip_args:
        d["extra_pip_args"] = pkg.extra_pip_args
    if pkg.last_sync:
        d["last_sync"] = {
            "timestamp": pkg.last_sync.timestamp,
            "synced_files": pkg.last_sync.synced_files,
        }
    return d
