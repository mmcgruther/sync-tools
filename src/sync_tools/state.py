from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .errors import StateFileError

_SUPPORTED_VERSION = "1"


@dataclass
class LastSync:
    timestamp: str
    refs: dict[str, str]  # refname -> full SHA
    lfs_oids: list[str] = field(default_factory=list)  # SHA-256 OIDs of synced LFS objects


@dataclass
class RepoConfig:
    id: str
    source_url: str
    dest_path: str
    source_local_path: str | None = None
    last_sync: LastSync | None = None
    lfs_mode: str | None = None  # None=fail on LFS, "skip"=skip LFS refs, "allow"=include with warning


def load_state(path: pathlib.Path) -> list[RepoConfig]:
    """Read and parse the JSON state file. Raises StateFileError on any problem."""
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

    repos_raw = raw.get("repos")
    if not isinstance(repos_raw, list):
        raise StateFileError(f"State file missing 'repos' list: {path}")

    return [_parse_repo(r, path) for r in repos_raw]


def save_state(path: pathlib.Path, repos: list[RepoConfig]) -> None:
    """Atomically write state file. Raises StateFileError on write failure."""
    data = {
        "version": _SUPPORTED_VERSION,
        "repos": [_repo_to_dict(r) for r in repos],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise StateFileError(f"Failed to write state file {path}: {exc}") from exc


def now_utc_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_repo(raw: object, path: pathlib.Path) -> RepoConfig:
    if not isinstance(raw, dict):
        raise StateFileError(f"Each repo entry must be a JSON object in {path}")

    def req(key: str) -> str:
        val = raw.get(key)
        if not isinstance(val, str) or not val:
            raise StateFileError(f"Repo entry missing required string field '{key}' in {path}")
        return val

    last_sync: LastSync | None = None
    ls_raw = raw.get("last_sync")
    if ls_raw is not None:
        if not isinstance(ls_raw, dict):
            raise StateFileError(f"'last_sync' must be a JSON object in {path}")
        ts = ls_raw.get("timestamp")
        refs = ls_raw.get("refs")
        if not isinstance(ts, str):
            raise StateFileError(f"'last_sync.timestamp' must be a string in {path}")
        if not isinstance(refs, dict):
            raise StateFileError(f"'last_sync.refs' must be a JSON object in {path}")
        lfs_oids_raw = ls_raw.get("lfs_oids", [])
        if not isinstance(lfs_oids_raw, list):
            raise StateFileError(f"'last_sync.lfs_oids' must be a list in {path}")
        last_sync = LastSync(timestamp=ts, refs=refs, lfs_oids=lfs_oids_raw)

    lfs_mode_raw = raw.get("lfs_mode")
    if lfs_mode_raw is not None:
        if lfs_mode_raw not in ("skip", "allow", "sync"):
            raise StateFileError(
                f"'lfs_mode' must be 'skip', 'allow', or 'sync' (got {lfs_mode_raw!r}) in {path}"
            )
    lfs_mode: str | None = lfs_mode_raw

    return RepoConfig(
        id=req("id"),
        source_url=req("source_url"),
        dest_path=req("dest_path"),
        source_local_path=raw.get("source_local_path") or None,
        last_sync=last_sync,
        lfs_mode=lfs_mode,
    )


def _repo_to_dict(repo: RepoConfig) -> dict:
    d: dict = {
        "id": repo.id,
        "source_url": repo.source_url,
        "dest_path": repo.dest_path,
    }
    if repo.source_local_path:
        d["source_local_path"] = repo.source_local_path
    if repo.lfs_mode:
        d["lfs_mode"] = repo.lfs_mode
    if repo.last_sync:
        ls_dict: dict = {"timestamp": repo.last_sync.timestamp, "refs": repo.last_sync.refs}
        if repo.last_sync.lfs_oids:
            ls_dict["lfs_oids"] = repo.last_sync.lfs_oids
        d["last_sync"] = ls_dict
    return d
