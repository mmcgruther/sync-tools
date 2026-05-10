from __future__ import annotations

import json
import pathlib
import sys
import tarfile
from dataclasses import dataclass, field

from .errors import BundleError, StateFileError
from .pypi_bundle import PackageBundleResult, safe_package_id
from .state import now_utc_iso

_PYPI_MANIFEST_NAME = "pypi_manifest.json"
_SUPPORTED_VERSION = "1"


@dataclass
class PyPIManifestEntry:
    id: str
    package_name: str
    dest_path: str
    synced_files: dict[str, str]  # filename -> sha256
    package_dir: str  # relative prefix inside the archive, e.g. "packages/numpy"


@dataclass
class PyPIManifest:
    version: str
    export_timestamp: str
    entries: list[PyPIManifestEntry] = field(default_factory=list)


def create_pypi_archive(
    results: list[PackageBundleResult],
    output_path: pathlib.Path,
    tmp_dir: pathlib.Path,
) -> None:
    """Pack all package files and a manifest into output_path (.tar.gz)."""
    timestamp = now_utc_iso()
    entries: list[PyPIManifestEntry] = []

    for result in results:
        safe_id = safe_package_id(result.config.id)
        pkg_dir = f"packages/{safe_id}"
        entries.append(
            PyPIManifestEntry(
                id=result.config.id,
                package_name=result.config.package_name,
                dest_path=result.config.dest_path,
                synced_files=result.synced_files,
                package_dir=pkg_dir,
            )
        )

    manifest = PyPIManifest(
        version=_SUPPORTED_VERSION,
        export_timestamp=timestamp,
        entries=entries,
    )
    manifest_path = tmp_dir / _PYPI_MANIFEST_NAME
    manifest_path.write_text(json.dumps(_manifest_to_dict(manifest), indent=2), encoding="utf-8")

    with tarfile.open(output_path, "w:gz") as tar:
        tar.add(str(manifest_path), arcname=_PYPI_MANIFEST_NAME)
        for result, entry in zip(results, entries):
            for filename in result.synced_files:
                file_path = result.package_dir / filename
                tar.add(str(file_path), arcname=f"{entry.package_dir}/{filename}")


def extract_pypi_archive(
    archive_path: pathlib.Path,
    extract_dir: pathlib.Path,
) -> PyPIManifest:
    """Extract archive and parse pypi_manifest.json. Raises StateFileError or BundleError."""
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            _safe_extract(tar, extract_dir)
    except tarfile.TarError as exc:
        raise BundleError(f"Failed to extract PyPI archive {archive_path}: {exc}") from exc

    manifest_path = extract_dir / _PYPI_MANIFEST_NAME
    if not manifest_path.exists():
        raise StateFileError(f"PyPI archive missing {_PYPI_MANIFEST_NAME}: {archive_path}")

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateFileError(f"{_PYPI_MANIFEST_NAME} is not valid JSON: {exc}") from exc

    return _parse_manifest(raw)


def _safe_extract(tar: tarfile.TarFile, extract_dir: pathlib.Path) -> None:
    resolved_base = extract_dir.resolve()
    for member in tar.getmembers():
        member_path = (extract_dir / member.name).resolve()
        if not str(member_path).startswith(str(resolved_base)):
            raise BundleError(f"Archive contains path traversal attempt: {member.name!r}")
    if sys.version_info >= (3, 12):
        tar.extractall(extract_dir, filter="fully_trusted")  # noqa: S202
    else:
        tar.extractall(extract_dir)  # noqa: S202


def _manifest_to_dict(manifest: PyPIManifest) -> dict:
    return {
        "version": manifest.version,
        "type": "pypi",
        "export_timestamp": manifest.export_timestamp,
        "entries": [
            {
                "id": e.id,
                "package_name": e.package_name,
                "dest_path": e.dest_path,
                "synced_files": e.synced_files,
                "package_dir": e.package_dir,
            }
            for e in manifest.entries
        ],
    }


def _parse_manifest(raw: object) -> PyPIManifest:
    if not isinstance(raw, dict):
        raise StateFileError(f"{_PYPI_MANIFEST_NAME} root must be a JSON object")

    version = raw.get("version")
    if version != _SUPPORTED_VERSION:
        raise StateFileError(
            f"Unsupported PyPI manifest version {version!r} (expected {_SUPPORTED_VERSION!r})"
        )

    entries_raw = raw.get("entries")
    if not isinstance(entries_raw, list):
        raise StateFileError(f"{_PYPI_MANIFEST_NAME} missing 'entries' list")

    entries: list[PyPIManifestEntry] = []
    for e in entries_raw:
        if not isinstance(e, dict):
            raise StateFileError("Each PyPI manifest entry must be a JSON object")
        try:
            entries.append(
                PyPIManifestEntry(
                    id=e["id"],
                    package_name=e["package_name"],
                    dest_path=e["dest_path"],
                    synced_files=e.get("synced_files", {}),
                    package_dir=e["package_dir"],
                )
            )
        except KeyError as exc:
            raise StateFileError(f"PyPI manifest entry missing field {exc}") from exc

    return PyPIManifest(
        version=version,
        export_timestamp=raw.get("export_timestamp", ""),
        entries=entries,
    )
