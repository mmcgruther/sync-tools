from __future__ import annotations

import json
import pathlib
import tarfile
from dataclasses import dataclass, field

from .docker_bundle import ImageBundleResult, safe_image_id
from .errors import BundleError, StateFileError
from .state import now_utc_iso

_DOCKER_MANIFEST_NAME = "manifest.json"
_SUPPORTED_VERSION = "1"
_ARCHIVE_TYPE = "docker"


@dataclass
class DockerManifestEntry:
    id: str
    source_ref: str
    dest_ref: str
    synced_tag_digests: dict[str, str]  # tag -> digest at time of export
    image_filename: str  # e.g. "images/myapp__backend/image.tar"


@dataclass
class DockerManifest:
    version: str
    export_timestamp: str
    entries: list[DockerManifestEntry] = field(default_factory=list)


def create_docker_archive(
    results: list[ImageBundleResult],
    output_path: pathlib.Path,
    tmp_dir: pathlib.Path,
) -> None:
    """Pack all image tars and a manifest into output_path (.tar.gz)."""
    timestamp = now_utc_iso()
    entries: list[DockerManifestEntry] = []

    for result in results:
        safe_id = safe_image_id(result.config.id)
        image_filename = f"images/{safe_id}/image.tar"
        entries.append(
            DockerManifestEntry(
                id=result.config.id,
                source_ref=result.config.source_ref,
                dest_ref=result.config.dest_ref,
                synced_tag_digests=result.synced_tag_digests,
                image_filename=image_filename,
            )
        )

    manifest = DockerManifest(
        version=_SUPPORTED_VERSION,
        export_timestamp=timestamp,
        entries=entries,
    )
    manifest_path = tmp_dir / _DOCKER_MANIFEST_NAME
    manifest_path.write_text(json.dumps(_manifest_to_dict(manifest), indent=2), encoding="utf-8")

    with tarfile.open(output_path, "w:gz") as tar:
        tar.add(str(manifest_path), arcname=_DOCKER_MANIFEST_NAME)
        for result, entry in zip(results, entries):
            tar.add(str(result.image_tar), arcname=entry.image_filename)


def extract_docker_archive(
    archive_path: pathlib.Path,
    extract_dir: pathlib.Path,
) -> DockerManifest:
    """Extract archive and parse manifest.json. Raises StateFileError or BundleError."""
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            _safe_extract(tar, extract_dir)
    except tarfile.TarError as exc:
        raise BundleError(f"Failed to extract Docker archive {archive_path}: {exc}") from exc

    manifest_path = extract_dir / _DOCKER_MANIFEST_NAME
    if not manifest_path.exists():
        raise StateFileError(f"Docker archive missing {_DOCKER_MANIFEST_NAME}: {archive_path}")

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateFileError(f"manifest.json is not valid JSON: {exc}") from exc

    return _parse_manifest(raw)


def _safe_extract(tar: tarfile.TarFile, extract_dir: pathlib.Path) -> None:
    resolved_base = extract_dir.resolve()
    for member in tar.getmembers():
        member_path = (extract_dir / member.name).resolve()
        if not str(member_path).startswith(str(resolved_base)):
            raise BundleError(f"Archive contains path traversal attempt: {member.name!r}")
    tar.extractall(extract_dir)  # noqa: S202 — members validated above


def _manifest_to_dict(manifest: DockerManifest) -> dict:
    return {
        "version": manifest.version,
        "type": _ARCHIVE_TYPE,
        "export_timestamp": manifest.export_timestamp,
        "entries": [
            {
                "id": e.id,
                "source_ref": e.source_ref,
                "dest_ref": e.dest_ref,
                "synced_tag_digests": e.synced_tag_digests,
                "image_filename": e.image_filename,
            }
            for e in manifest.entries
        ],
    }


def _parse_manifest(raw: object) -> DockerManifest:
    if not isinstance(raw, dict):
        raise StateFileError("manifest.json root must be a JSON object")

    version = raw.get("version")
    if version != _SUPPORTED_VERSION:
        raise StateFileError(
            f"Unsupported Docker manifest version {version!r} (expected {_SUPPORTED_VERSION!r})"
        )

    entries_raw = raw.get("entries")
    if not isinstance(entries_raw, list):
        raise StateFileError("manifest.json missing 'entries' list")

    entries: list[DockerManifestEntry] = []
    for e in entries_raw:
        if not isinstance(e, dict):
            raise StateFileError("Each manifest entry must be a JSON object")
        try:
            entries.append(
                DockerManifestEntry(
                    id=e["id"],
                    source_ref=e["source_ref"],
                    dest_ref=e["dest_ref"],
                    synced_tag_digests=e.get("synced_tag_digests", {}),
                    image_filename=e["image_filename"],
                )
            )
        except KeyError as exc:
            raise StateFileError(f"Manifest entry missing field {exc}") from exc

    return DockerManifest(
        version=version,
        export_timestamp=raw.get("export_timestamp", ""),
        entries=entries,
    )
