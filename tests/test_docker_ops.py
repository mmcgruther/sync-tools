from __future__ import annotations

import pathlib

import pytest

from sync_tools.docker_ops import (
    docker_load,
    docker_pull,
    docker_rmi,
    docker_save,
    docker_tag,
    get_image_digest,
    is_docker_available,
)
from sync_tools.errors import DockerCommandError


class TestIsDockerAvailable:
    def test_returns_bool(self) -> None:
        result = is_docker_available()
        assert isinstance(result, bool)


class TestDockerOps:
    """All tests here require Docker daemon — skip if unavailable."""

    def test_docker_pull(self, docker_available: None) -> None:
        # busybox is small and should already be cached from fixture builds
        docker_pull("busybox:1.36")

    def test_get_image_digest(self, docker_available: None, docker_image: str) -> None:
        digest = get_image_digest(docker_image)
        assert digest.startswith("sha256:")
        assert len(digest) > 10

    def test_same_image_same_digest(self, docker_available: None, docker_image: str) -> None:
        d1 = get_image_digest(docker_image)
        d2 = get_image_digest(docker_image)
        assert d1 == d2

    def test_different_images_different_digest(
        self, docker_available: None, docker_image: str, docker_image_v2: str
    ) -> None:
        d1 = get_image_digest(docker_image)
        d2 = get_image_digest(docker_image_v2)
        assert d1 != d2

    def test_docker_save_creates_file(
        self, docker_available: None, docker_image: str, tmp_path: pathlib.Path
    ) -> None:
        out = tmp_path / "image.tar"
        docker_save([docker_image], out)
        assert out.exists()
        assert out.stat().st_size > 0

    def test_docker_load_returns_id(
        self, docker_available: None, docker_image: str, tmp_path: pathlib.Path
    ) -> None:
        import uuid

        out = tmp_path / "image.tar"
        docker_save([docker_image], out)
        # Tag so we can remove it after
        loaded_id = docker_load(out)
        assert loaded_id  # non-empty string
        docker_rmi(loaded_id)

    def test_docker_tag(
        self, docker_available: None, docker_image: str, tmp_path: pathlib.Path
    ) -> None:
        import uuid

        new_tag = f"sync-tools-tagged:{uuid.uuid4().hex[:8]}"
        try:
            docker_tag(docker_image, new_tag)
            digest = get_image_digest(new_tag)
            assert digest.startswith("sha256:")
        finally:
            docker_rmi(new_tag)

    def test_docker_push_bad_registry_raises(
        self, docker_available: None, docker_image: str
    ) -> None:
        import uuid

        bad_ref = f"localhost:9999/no-registry/image:{uuid.uuid4().hex[:8]}"
        docker_tag(docker_image, bad_ref)
        try:
            with pytest.raises(DockerCommandError):
                from sync_tools.docker_ops import docker_push

                docker_push(bad_ref, timeout=15)
        finally:
            docker_rmi(bad_ref)

    def test_bad_image_ref_raises(self, docker_available: None) -> None:
        with pytest.raises(DockerCommandError):
            get_image_digest("nonexistent-image:this-tag-does-not-exist-xyz")
