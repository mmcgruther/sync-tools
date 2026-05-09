from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass

from .errors import StateFileError

_SUPPORTED_VERSION = "1"


@dataclass
class LastImageSync:
    timestamp: str
    tag_digests: dict[str, str]  # tag -> sha256:... digest


@dataclass
class ImageConfig:
    id: str           # e.g. "myapp/backend"
    source_ref: str   # base ref without tag
    dest_ref: str     # base ref without tag
    tags: list[str]   # tags to sync (same for source + dest)
    last_sync: LastImageSync | None = None


def load_docker_state(path: pathlib.Path) -> list[ImageConfig]:
    """Read and parse the Docker JSON state file. Raises StateFileError on any problem."""
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

    images_raw = raw.get("images")
    if not isinstance(images_raw, list):
        raise StateFileError(f"State file missing 'images' list: {path}")

    return [_parse_image(entry, path) for entry in images_raw]


def save_docker_state(path: pathlib.Path, images: list[ImageConfig]) -> None:
    """Atomically write Docker state file. Raises StateFileError on write failure."""
    data = {
        "version": _SUPPORTED_VERSION,
        "images": [_image_to_dict(img) for img in images],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise StateFileError(f"Failed to write state file {path}: {exc}") from exc


def _parse_image(raw: object, path: pathlib.Path) -> ImageConfig:
    if not isinstance(raw, dict):
        raise StateFileError(f"Each image entry must be a JSON object in {path}")

    def req(key: str) -> str:
        val = raw.get(key)
        if not isinstance(val, str) or not val:
            raise StateFileError(f"Image entry missing required string field '{key}' in {path}")
        return val

    tags_raw = raw.get("tags")
    if not isinstance(tags_raw, list) or not tags_raw:
        raise StateFileError(f"Image entry 'tags' must be a non-empty list in {path}")
    for t in tags_raw:
        if not isinstance(t, str):
            raise StateFileError(f"Each tag must be a string in {path}")

    last_sync: LastImageSync | None = None
    ls_raw = raw.get("last_sync")
    if ls_raw is not None:
        if not isinstance(ls_raw, dict):
            raise StateFileError(f"'last_sync' must be a JSON object in {path}")
        ts = ls_raw.get("timestamp")
        if not isinstance(ts, str):
            raise StateFileError(f"'last_sync.timestamp' must be a string in {path}")
        tag_digests_raw = ls_raw.get("tag_digests", {})
        if not isinstance(tag_digests_raw, dict):
            raise StateFileError(f"'last_sync.tag_digests' must be a JSON object in {path}")
        last_sync = LastImageSync(timestamp=ts, tag_digests=tag_digests_raw)

    return ImageConfig(
        id=req("id"),
        source_ref=req("source_ref"),
        dest_ref=req("dest_ref"),
        tags=tags_raw,
        last_sync=last_sync,
    )


def _image_to_dict(img: ImageConfig) -> dict:
    d: dict = {
        "id": img.id,
        "source_ref": img.source_ref,
        "dest_ref": img.dest_ref,
        "tags": img.tags,
    }
    if img.last_sync:
        ls_dict: dict = {"timestamp": img.last_sync.timestamp}
        if img.last_sync.tag_digests:
            ls_dict["tag_digests"] = img.last_sync.tag_digests
        d["last_sync"] = ls_dict
    return d
