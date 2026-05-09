from __future__ import annotations

import pathlib
import tarfile

import pytest

from sync_tools.docker_archive import (
    DockerManifestEntry,
    create_docker_archive,
    extract_docker_archive,
)
from sync_tools.docker_bundle import ImageBundleResult
from sync_tools.docker_state import ImageConfig
from sync_tools.errors import BundleError


def _make_config(image_id: str = "myapp/backend") -> ImageConfig:
    return ImageConfig(
        id=image_id,
        source_ref=f"src/{image_id}",
        dest_ref=f"dst/{image_id}",
        tags=["latest"],
    )


def _make_result(tmp_dir: pathlib.Path, image_id: str = "myapp/backend") -> ImageBundleResult:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tar_path = tmp_dir / "image.tar"
    tar_path.write_bytes(b"fake docker save output")
    return ImageBundleResult(
        config=_make_config(image_id),
        image_tar=tar_path,
        synced_tag_digests={"latest": "sha256:abc123"},
    )


class TestCreateDockerArchive:
    def test_creates_archive(self, tmp_path: pathlib.Path) -> None:
        result = _make_result(tmp_path / "img")
        (tmp_path / "img").mkdir(parents=True, exist_ok=True)
        out = tmp_path / "out.tar.gz"
        create_docker_archive([result], out, tmp_path)
        assert out.exists()
        assert out.stat().st_size > 0

    def test_archive_contains_manifest(self, tmp_path: pathlib.Path) -> None:
        img_dir = tmp_path / "img"
        img_dir.mkdir()
        result = _make_result(img_dir)
        out = tmp_path / "out.tar.gz"
        create_docker_archive([result], out, tmp_path)
        with tarfile.open(out, "r:gz") as tar:
            assert "manifest.json" in tar.getnames()

    def test_archive_contains_image_tar(self, tmp_path: pathlib.Path) -> None:
        img_dir = tmp_path / "img"
        img_dir.mkdir()
        result = _make_result(img_dir)
        out = tmp_path / "out.tar.gz"
        create_docker_archive([result], out, tmp_path)
        with tarfile.open(out, "r:gz") as tar:
            names = tar.getnames()
        assert any("image.tar" in n for n in names)

    def test_safe_id_replaces_slashes(self, tmp_path: pathlib.Path) -> None:
        img_dir = tmp_path / "img"
        img_dir.mkdir()
        result = _make_result(img_dir, image_id="org/project/backend")
        out = tmp_path / "out.tar.gz"
        create_docker_archive([result], out, tmp_path)
        with tarfile.open(out, "r:gz") as tar:
            names = tar.getnames()
        assert any("org__project__backend" in n for n in names)


class TestExtractDockerArchive:
    def test_round_trip(self, tmp_path: pathlib.Path) -> None:
        img_dir = tmp_path / "img"
        img_dir.mkdir()
        result = _make_result(img_dir)
        archive = tmp_path / "out.tar.gz"
        create_docker_archive([result], archive, tmp_path)

        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        manifest = extract_docker_archive(archive, extract_dir)

        assert len(manifest.entries) == 1
        assert manifest.entries[0].id == "myapp/backend"
        assert manifest.entries[0].synced_tag_digests["latest"] == "sha256:abc123"

    def test_image_tar_extracted(self, tmp_path: pathlib.Path) -> None:
        img_dir = tmp_path / "img"
        img_dir.mkdir()
        result = _make_result(img_dir)
        archive = tmp_path / "out.tar.gz"
        create_docker_archive([result], archive, tmp_path)

        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        manifest = extract_docker_archive(archive, extract_dir)

        image_file = extract_dir / manifest.entries[0].image_filename
        assert image_file.exists()

    def test_path_traversal_rejected(self, tmp_path: pathlib.Path) -> None:
        archive = tmp_path / "evil.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            info = tarfile.TarInfo(name="../../evil.txt")
            info.size = 4
            import io
            tar.addfile(info, io.BytesIO(b"evil"))

        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        with pytest.raises(BundleError, match="traversal"):
            extract_docker_archive(archive, extract_dir)
