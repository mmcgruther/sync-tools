from __future__ import annotations

import json
import pathlib

import pytest

from sync_tools.docker_state import ImageConfig, LastImageSync, load_docker_state, save_docker_state
from sync_tools.errors import StateFileError


def _write(tmp_path: pathlib.Path, data: dict) -> pathlib.Path:
    p = tmp_path / "docker_state.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _minimal_state(extra: dict | None = None) -> dict:
    entry: dict = {
        "id": "myapp/backend",
        "source_ref": "source.internal/myapp/backend",
        "dest_ref": "dest.internal/myapp/backend",
        "tags": ["latest", "v1.2.3"],
    }
    if extra:
        entry.update(extra)
    return {"version": "1", "images": [entry]}


class TestLoadDockerState:
    def test_round_trip(self, tmp_path: pathlib.Path) -> None:
        path = _write(tmp_path, _minimal_state())
        images = load_docker_state(path)
        assert len(images) == 1
        img = images[0]
        assert img.id == "myapp/backend"
        assert img.source_ref == "source.internal/myapp/backend"
        assert img.dest_ref == "dest.internal/myapp/backend"
        assert img.tags == ["latest", "v1.2.3"]
        assert img.last_sync is None

    def test_last_sync_parsed(self, tmp_path: pathlib.Path) -> None:
        state = _minimal_state(
            {
                "last_sync": {
                    "timestamp": "2024-01-01T00:00:00Z",
                    "tag_digests": {"latest": "sha256:abc", "v1.2.3": "sha256:def"},
                }
            }
        )
        path = _write(tmp_path, state)
        images = load_docker_state(path)
        assert images[0].last_sync is not None
        assert images[0].last_sync.timestamp == "2024-01-01T00:00:00Z"
        assert images[0].last_sync.tag_digests["latest"] == "sha256:abc"

    def test_missing_file_raises(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(StateFileError, match="not found"):
            load_docker_state(tmp_path / "nonexistent.json")

    def test_invalid_json_raises(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("not json", encoding="utf-8")
        with pytest.raises(StateFileError, match="valid JSON"):
            load_docker_state(p)

    def test_wrong_version_raises(self, tmp_path: pathlib.Path) -> None:
        state = _minimal_state()
        state["version"] = "99"
        path = _write(tmp_path, state)
        with pytest.raises(StateFileError, match="version"):
            load_docker_state(path)

    def test_missing_id_raises(self, tmp_path: pathlib.Path) -> None:
        state = _minimal_state()
        del state["images"][0]["id"]
        path = _write(tmp_path, state)
        with pytest.raises(StateFileError, match="id"):
            load_docker_state(path)

    def test_empty_tags_raises(self, tmp_path: pathlib.Path) -> None:
        state = _minimal_state()
        state["images"][0]["tags"] = []
        path = _write(tmp_path, state)
        with pytest.raises(StateFileError, match="tags"):
            load_docker_state(path)

    def test_tags_not_list_raises(self, tmp_path: pathlib.Path) -> None:
        state = _minimal_state()
        state["images"][0]["tags"] = "latest"
        path = _write(tmp_path, state)
        with pytest.raises(StateFileError, match="tags"):
            load_docker_state(path)

    def test_multiple_entries(self, tmp_path: pathlib.Path) -> None:
        state: dict = {
            "version": "1",
            "images": [
                {
                    "id": "app/a",
                    "source_ref": "src/a",
                    "dest_ref": "dst/a",
                    "tags": ["latest"],
                },
                {
                    "id": "app/b",
                    "source_ref": "src/b",
                    "dest_ref": "dst/b",
                    "tags": ["v2"],
                },
            ],
        }
        path = _write(tmp_path, state)
        images = load_docker_state(path)
        assert len(images) == 2
        assert images[0].id == "app/a"
        assert images[1].id == "app/b"


class TestSaveDockerState:
    def test_save_and_reload(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "out.json"
        images = [
            ImageConfig(
                id="myapp/backend",
                source_ref="src/backend",
                dest_ref="dst/backend",
                tags=["latest"],
                last_sync=LastImageSync(
                    timestamp="2024-06-01T00:00:00Z",
                    tag_digests={"latest": "sha256:abc"},
                ),
            )
        ]
        save_docker_state(path, images)
        reloaded = load_docker_state(path)
        assert len(reloaded) == 1
        assert reloaded[0].id == "myapp/backend"
        assert reloaded[0].last_sync is not None
        assert reloaded[0].last_sync.tag_digests["latest"] == "sha256:abc"

    def test_no_last_sync_omitted(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "out.json"
        images = [ImageConfig(id="x/y", source_ref="s", dest_ref="d", tags=["t"], last_sync=None)]
        save_docker_state(path, images)
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert "last_sync" not in raw["images"][0]

    def test_empty_tag_digests_omitted(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "out.json"
        images = [
            ImageConfig(
                id="x/y",
                source_ref="s",
                dest_ref="d",
                tags=["t"],
                last_sync=LastImageSync(timestamp="2024-01-01T00:00:00Z", tag_digests={}),
            )
        ]
        save_docker_state(path, images)
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert "tag_digests" not in raw["images"][0].get("last_sync", {})
