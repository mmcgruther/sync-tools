from __future__ import annotations

import io
import json
import pathlib
import tarfile
import unittest.mock as mock

import pytest

from sync_tools.docker_export_cmd import DockerExportOptions, run_docker_export
from sync_tools.docker_import_cmd import DockerImportOptions, run_docker_import

# Use a real pullable image already in local cache from fixture builds
_SOURCE_REF = "busybox"
_SOURCE_TAG = "1.36"


def _make_state(source_ref: str = _SOURCE_REF, dest_ref: str = "dst.invalid/img") -> dict:
    return {
        "version": "1",
        "images": [
            {
                "id": "test/img",
                "source_ref": source_ref,
                "dest_ref": dest_ref,
                "tags": [_SOURCE_TAG],
            }
        ],
    }


def _export_options(
    state_path: pathlib.Path, output_path: pathlib.Path
) -> DockerExportOptions:
    return DockerExportOptions(
        state_path=state_path, output_path=output_path, workers=1, dry_run=False
    )


def _import_options(
    archive_path: pathlib.Path, dry_run: bool = False
) -> DockerImportOptions:
    return DockerImportOptions(archive_path=archive_path, workers=1, dry_run=dry_run)


def _build_export_archive(tmp_path: pathlib.Path) -> pathlib.Path:
    """Export busybox and return archive path."""
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(_make_state()), encoding="utf-8")
    archive = tmp_path / "out.tar.gz"
    summary = run_docker_export(_export_options(state_path, archive))
    assert len(summary.failed) == 0, f"Export failed: {summary.failed}"
    return archive


class TestDockerImportDryRun:
    def test_dry_run_skips_docker_ops(
        self, docker_available: None, tmp_path: pathlib.Path
    ) -> None:
        archive = _build_export_archive(tmp_path)
        summary = run_docker_import(_import_options(archive, dry_run=True))
        assert summary.failed == []
        assert "test/img" in summary.succeeded

    def test_dry_run_with_missing_tar_fails(self, tmp_path: pathlib.Path) -> None:
        manifest_json = json.dumps(
            {
                "version": "1",
                "type": "docker",
                "export_timestamp": "2024-01-01T00:00:00Z",
                "entries": [
                    {
                        "id": "test/missing",
                        "source_ref": "src",
                        "dest_ref": "dst",
                        "synced_tag_digests": {"latest": "sha256:abc"},
                        "image_filename": "images/test__missing/image.tar",
                    }
                ],
            }
        )
        archive = tmp_path / "bad.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            data = manifest_json.encode()
            info = tarfile.TarInfo(name="manifest.json")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
            # Deliberately omit the image.tar entry

        summary = run_docker_import(_import_options(archive, dry_run=False))
        assert len(summary.failed) == 1
        assert "test/missing" in summary.failed[0][0]


class TestDockerImportRoundTrip:
    def test_load_and_tag_succeed(
        self, docker_available: None, tmp_path: pathlib.Path
    ) -> None:
        """
        Full round-trip: export busybox, then import it (with push mocked to no-op).
        Verifies load and tag steps succeed by patching push (no real registry needed).
        """
        archive = _build_export_archive(tmp_path)

        with mock.patch("sync_tools.docker_import_cmd.docker_push"):
            summary = run_docker_import(_import_options(archive))

        assert summary.failed == []
        assert "test/img" in summary.succeeded
