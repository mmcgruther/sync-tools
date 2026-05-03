from __future__ import annotations

import json
import pathlib

import pytest

from sync_tools.errors import StateFileError
from sync_tools.state import LastSync, RepoConfig, load_state, now_utc_iso, save_state


def _write_state(path: pathlib.Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _minimal_state(repos: list[dict] | None = None) -> dict:
    return {
        "version": "1",
        "repos": repos
        or [
            {
                "id": "org/repo",
                "source_url": "https://git.example.com/org/repo.git",
                "dest_path": "/mnt/dest/repo.git",
            }
        ],
    }


class TestLoadState:
    def test_roundtrip(self, tmp_path: pathlib.Path) -> None:
        state_path = tmp_path / "state.json"
        repos = [
            RepoConfig(
                id="org/repo",
                source_url="https://git.example.com/org/repo.git",
                dest_path="/mnt/dest/repo.git",
                source_local_path="/mirrors/repo.git",
                last_sync=LastSync(
                    timestamp="2024-01-01T00:00:00Z",
                    refs={"refs/heads/main": "a" * 40},
                ),
            )
        ]
        save_state(state_path, repos)
        loaded = load_state(state_path)
        assert len(loaded) == 1
        r = loaded[0]
        assert r.id == "org/repo"
        assert r.source_url == "https://git.example.com/org/repo.git"
        assert r.dest_path == "/mnt/dest/repo.git"
        assert r.source_local_path == "/mirrors/repo.git"
        assert r.last_sync is not None
        assert r.last_sync.timestamp == "2024-01-01T00:00:00Z"
        assert r.last_sync.refs == {"refs/heads/main": "a" * 40}

    def test_missing_file(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(StateFileError, match="not found"):
            load_state(tmp_path / "nonexistent.json")

    def test_bad_json(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(StateFileError, match="not valid JSON"):
            load_state(p)

    def test_wrong_version(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "state.json"
        _write_state(p, {"version": "99", "repos": []})
        with pytest.raises(StateFileError, match="Unsupported state file version"):
            load_state(p)

    def test_no_last_sync(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "state.json"
        _write_state(p, _minimal_state())
        repos = load_state(p)
        assert repos[0].last_sync is None

    def test_not_dict_root(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "state.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(StateFileError):
            load_state(p)

    def test_missing_repos_list(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "state.json"
        _write_state(p, {"version": "1"})
        with pytest.raises(StateFileError, match="'repos' list"):
            load_state(p)

    def test_missing_required_repo_field(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "state.json"
        _write_state(p, {"version": "1", "repos": [{"id": "org/repo"}]})
        with pytest.raises(StateFileError, match="source_url"):
            load_state(p)


class TestSaveState:
    def test_atomic_write(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "state.json"
        repos = [
            RepoConfig(
                id="x/y",
                source_url="https://example.com/x.git",
                dest_path="/dest/x.git",
            )
        ]
        save_state(p, repos)
        assert p.exists()
        # Temp file should be cleaned up
        assert not (tmp_path / "state.json.tmp").exists()

    def test_overwrites_existing(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "state.json"
        repos_a = [RepoConfig(id="a/b", source_url="u", dest_path="d")]
        repos_b = [RepoConfig(id="c/d", source_url="u2", dest_path="d2")]
        save_state(p, repos_a)
        save_state(p, repos_b)
        loaded = load_state(p)
        assert loaded[0].id == "c/d"


class TestNowUtcIso:
    def test_format(self) -> None:
        ts = now_utc_iso()
        assert ts.endswith("Z")
        assert "T" in ts
        assert len(ts) == 20  # "YYYY-MM-DDTHH:MM:SSZ"
