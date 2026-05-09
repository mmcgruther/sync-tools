from __future__ import annotations

import json
import pathlib
import tarfile

from sync_tools.docker_export_cmd import DockerExportOptions, run_docker_export
from sync_tools.docker_state import load_docker_state

# Use a real pullable image that will be in local cache (built FROM it in fixtures)
_SOURCE_REF = "busybox"
_SOURCE_TAG = "1.36"


def _make_state(
    source_ref: str = _SOURCE_REF,
    dest_ref: str = "dst.invalid/backend",
    tags: list[str] | None = None,
    image_id: str = "test/backend",
) -> dict:
    return {
        "version": "1",
        "images": [
            {
                "id": image_id,
                "source_ref": source_ref,
                "dest_ref": dest_ref,
                "tags": tags or [_SOURCE_TAG],
            }
        ],
    }


def _options(
    state_path: pathlib.Path,
    output_path: pathlib.Path,
    dry_run: bool = False,
    workers: int = 1,
) -> DockerExportOptions:
    return DockerExportOptions(
        state_path=state_path,
        output_path=output_path,
        workers=workers,
        dry_run=dry_run,
    )


class TestDockerExportHappyPath:
    def test_creates_archive(
        self, docker_available: None, tmp_path: pathlib.Path
    ) -> None:
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps(_make_state()), encoding="utf-8")
        out = tmp_path / "out.tar.gz"
        summary = run_docker_export(_options(state_path, out))
        assert len(summary.failed) == 0, summary.failed
        assert "test/backend" in summary.succeeded
        assert out.exists()

    def test_archive_contains_manifest(
        self, docker_available: None, tmp_path: pathlib.Path
    ) -> None:
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps(_make_state()), encoding="utf-8")
        out = tmp_path / "out.tar.gz"
        summary = run_docker_export(_options(state_path, out))
        assert len(summary.failed) == 0, summary.failed
        with tarfile.open(out, "r:gz") as tar:
            assert "manifest.json" in tar.getnames()

    def test_state_updated_after_export(
        self, docker_available: None, tmp_path: pathlib.Path
    ) -> None:
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps(_make_state()), encoding="utf-8")
        out = tmp_path / "out.tar.gz"
        summary = run_docker_export(_options(state_path, out))
        assert len(summary.failed) == 0, summary.failed
        images = load_docker_state(state_path)
        assert images[0].last_sync is not None
        assert _SOURCE_TAG in images[0].last_sync.tag_digests

    def test_second_export_skipped_when_unchanged(
        self, docker_available: None, tmp_path: pathlib.Path
    ) -> None:
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps(_make_state()), encoding="utf-8")
        out1 = tmp_path / "out1.tar.gz"
        summary1 = run_docker_export(_options(state_path, out1))
        assert len(summary1.failed) == 0, summary1.failed

        out2 = tmp_path / "out2.tar.gz"
        summary = run_docker_export(_options(state_path, out2))
        assert "test/backend" in summary.skipped
        assert not out2.exists()


class TestDockerExportDryRun:
    def test_no_archive_created(
        self, docker_available: None, tmp_path: pathlib.Path
    ) -> None:
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps(_make_state()), encoding="utf-8")
        out = tmp_path / "out.tar.gz"
        run_docker_export(_options(state_path, out, dry_run=True))
        assert not out.exists()

    def test_state_not_updated(
        self, docker_available: None, tmp_path: pathlib.Path
    ) -> None:
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps(_make_state()), encoding="utf-8")
        out = tmp_path / "out.tar.gz"
        run_docker_export(_options(state_path, out, dry_run=True))
        images = load_docker_state(state_path)
        assert images[0].last_sync is None


class TestDockerExportFailure:
    def test_bad_image_ref_fails(self, docker_available: None, tmp_path: pathlib.Path) -> None:
        state_path = tmp_path / "state.json"
        state_path.write_text(
            json.dumps(
                _make_state(
                    source_ref="localhost:9999/nonexistent/image",
                    tags=["latest"],
                )
            ),
            encoding="utf-8",
        )
        out = tmp_path / "out.tar.gz"
        summary = run_docker_export(_options(state_path, out))
        assert len(summary.failed) == 1
        assert "test/backend" in summary.failed[0][0]
