from __future__ import annotations

import json
import pathlib
import tarfile
from dataclasses import dataclass, field

from .bundle import BundleResult, safe_repo_id
from .errors import BundleError, StateFileError
from .state import now_utc_iso

_MANIFEST_NAME = "manifest.json"
_SUPPORTED_VERSION = "1"


@dataclass
class ManifestEntry:
    repo_id: str
    source_url: str
    dest_path: str
    exported_refs: dict[str, str]
    bundle_filename: str  # relative path inside archive, e.g. "bundles/org__repo/bundle.git"
    export_timestamp: str
    lfs_objects: list[str] = field(default_factory=list)  # OIDs of LFS objects in archive
    lfs_dir: str = ""  # relative prefix for LFS files, e.g. "lfs/org__repo"


@dataclass
class Manifest:
    version: str
    export_timestamp: str
    entries: list[ManifestEntry] = field(default_factory=list)


def create_archive(
    bundle_results: list[BundleResult],
    output_path: pathlib.Path,
    tmp_dir: pathlib.Path,
) -> None:
    """
    Build manifest.json and pack it together with all bundle files into output_path (.tar.gz).
    """
    timestamp = now_utc_iso()
    entries: list[ManifestEntry] = []

    for result in bundle_results:
        safe_id = safe_repo_id(result.repo.id)
        bundle_rel = f"bundles/{safe_id}/bundle.git"
        lfs_oids = list(result.lfs_objects.keys())
        entries.append(
            ManifestEntry(
                repo_id=result.repo.id,
                source_url=result.repo.source_url,
                dest_path=result.repo.dest_path,
                exported_refs=result.exported_refs,
                bundle_filename=bundle_rel,
                export_timestamp=timestamp,
                lfs_objects=lfs_oids,
                lfs_dir=f"lfs/{safe_id}" if lfs_oids else "",
            )
        )

    manifest = Manifest(version=_SUPPORTED_VERSION, export_timestamp=timestamp, entries=entries)
    manifest_path = tmp_dir / _MANIFEST_NAME
    manifest_path.write_text(json.dumps(_manifest_to_dict(manifest), indent=2), encoding="utf-8")

    with tarfile.open(output_path, "w:gz") as tar:
        tar.add(str(manifest_path), arcname=_MANIFEST_NAME)
        for result, entry in zip(bundle_results, entries):
            tar.add(str(result.bundle_path), arcname=entry.bundle_filename)
            for oid, lfs_path in result.lfs_objects.items():
                tar.add(str(lfs_path), arcname=f"{entry.lfs_dir}/{oid[:2]}/{oid[2:4]}/{oid}")


def extract_archive(
    archive_path: pathlib.Path,
    extract_dir: pathlib.Path,
) -> Manifest:
    """
    Extract archive_path into extract_dir (safe extraction) and parse manifest.json.
    Raises StateFileError on bad manifest, BundleError on path traversal attempt.
    """
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            _safe_extract(tar, extract_dir)
    except tarfile.TarError as exc:
        raise BundleError(f"Failed to extract archive {archive_path}: {exc}") from exc

    manifest_path = extract_dir / _MANIFEST_NAME
    if not manifest_path.exists():
        raise StateFileError(f"Archive missing {_MANIFEST_NAME}: {archive_path}")

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateFileError(f"manifest.json is not valid JSON: {exc}") from exc

    return _parse_manifest(raw)


def _safe_extract(tar: tarfile.TarFile, extract_dir: pathlib.Path) -> None:
    """Extract tar members, rejecting any path that would escape extract_dir (zip-slip guard)."""
    resolved_base = extract_dir.resolve()
    for member in tar.getmembers():
        member_path = (extract_dir / member.name).resolve()
        if not str(member_path).startswith(str(resolved_base)):
            raise BundleError(f"Archive contains path traversal attempt: {member.name!r}")
    tar.extractall(extract_dir)  # noqa: S202 — members validated above


def _manifest_to_dict(manifest: Manifest) -> dict:
    entries_list = []
    for e in manifest.entries:
        entry_dict: dict = {
            "repo_id": e.repo_id,
            "source_url": e.source_url,
            "dest_path": e.dest_path,
            "exported_refs": e.exported_refs,
            "bundle_filename": e.bundle_filename,
            "export_timestamp": e.export_timestamp,
        }
        if e.lfs_objects:
            entry_dict["lfs_objects"] = e.lfs_objects
            entry_dict["lfs_dir"] = e.lfs_dir
        entries_list.append(entry_dict)
    return {
        "version": manifest.version,
        "export_timestamp": manifest.export_timestamp,
        "entries": entries_list,
    }


def _parse_manifest(raw: object) -> Manifest:
    if not isinstance(raw, dict):
        raise StateFileError("manifest.json root must be a JSON object")

    version = raw.get("version")
    if version != _SUPPORTED_VERSION:
        raise StateFileError(
            f"Unsupported manifest version {version!r} (expected {_SUPPORTED_VERSION!r})"
        )

    entries_raw = raw.get("entries")
    if not isinstance(entries_raw, list):
        raise StateFileError("manifest.json missing 'entries' list")

    entries: list[ManifestEntry] = []
    for e in entries_raw:
        if not isinstance(e, dict):
            raise StateFileError("Each manifest entry must be a JSON object")
        try:
            entries.append(
                ManifestEntry(
                    repo_id=e["repo_id"],
                    source_url=e["source_url"],
                    dest_path=e["dest_path"],
                    exported_refs=e["exported_refs"],
                    bundle_filename=e["bundle_filename"],
                    export_timestamp=e["export_timestamp"],
                    lfs_objects=e.get("lfs_objects", []),
                    lfs_dir=e.get("lfs_dir", ""),
                )
            )
        except KeyError as exc:
            raise StateFileError(f"Manifest entry missing field {exc}") from exc

    return Manifest(
        version=version,
        export_timestamp=raw.get("export_timestamp", ""),
        entries=entries,
    )
