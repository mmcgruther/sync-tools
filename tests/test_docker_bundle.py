from __future__ import annotations

import pathlib

from sync_tools.docker_bundle import (
    execute_image_bundle,
    plan_image_bundle,
)
from sync_tools.docker_state import ImageConfig, LastImageSync


def _make_config(tags: list[str] | None = None) -> ImageConfig:
    return ImageConfig(
        id="myapp/backend",
        source_ref="busybox",
        dest_ref="dest.internal/myapp/backend",
        tags=tags or ["latest"],
    )


class TestPlanImageBundle:
    def test_no_changes_when_digests_match(self) -> None:
        config = _make_config()
        config = ImageConfig(
            id=config.id,
            source_ref=config.source_ref,
            dest_ref=config.dest_ref,
            tags=["latest"],
            last_sync=LastImageSync(
                timestamp="2024-01-01T00:00:00Z",
                tag_digests={"latest": "sha256:abc"},
            ),
        )
        plan = plan_image_bundle(config, {"latest": "sha256:abc"})
        assert plan.no_changes is True

    def test_first_sync_has_all_tags(self) -> None:
        config = _make_config(["latest", "v1.0"])
        plan = plan_image_bundle(config, {"latest": "sha256:aaa", "v1.0": "sha256:bbb"})
        assert plan.no_changes is False
        assert "latest" in plan.tags_to_sync
        assert "v1.0" in plan.tags_to_sync

    def test_changed_tag_included(self) -> None:
        config = ImageConfig(
            id="x",
            source_ref="src",
            dest_ref="dst",
            tags=["latest", "v1"],
            last_sync=LastImageSync(
                timestamp="2024-01-01T00:00:00Z",
                tag_digests={"latest": "sha256:old", "v1": "sha256:v1"},
            ),
        )
        plan = plan_image_bundle(config, {"latest": "sha256:new", "v1": "sha256:v1"})
        assert plan.no_changes is False
        assert "latest" in plan.tags_to_sync
        assert "v1" not in plan.tags_to_sync

    def test_new_tag_included(self) -> None:
        config = ImageConfig(
            id="x",
            source_ref="src",
            dest_ref="dst",
            tags=["latest", "v2"],
            last_sync=LastImageSync(
                timestamp="2024-01-01T00:00:00Z",
                tag_digests={"latest": "sha256:abc"},
            ),
        )
        plan = plan_image_bundle(config, {"latest": "sha256:abc", "v2": "sha256:xyz"})
        assert "v2" in plan.tags_to_sync
        assert "latest" not in plan.tags_to_sync


class TestExecuteImageBundle:
    def test_creates_tar(self, docker_available: None, docker_image: str, tmp_path: pathlib.Path) -> None:

        # Extract base and tag from docker_image (e.g. "sync-tools-test:abc123")
        base, tag = docker_image.rsplit(":", 1)
        config = ImageConfig(
            id="test/img",
            source_ref=base,
            dest_ref="dst/img",
            tags=[tag],
        )
        plan = plan_image_bundle(config, {tag: "sha256:fresh"})
        result = execute_image_bundle(plan, tmp_path / "bundle")
        assert result.image_tar.exists()
        assert result.image_tar.stat().st_size > 0

    def test_result_digests_match_plan(
        self, docker_available: None, docker_image: str, tmp_path: pathlib.Path
    ) -> None:
        base, tag = docker_image.rsplit(":", 1)
        config = ImageConfig(
            id="test/img",
            source_ref=base,
            dest_ref="dst/img",
            tags=[tag],
        )
        current_digests = {tag: "sha256:abc"}
        plan = plan_image_bundle(config, current_digests)
        result = execute_image_bundle(plan, tmp_path / "bundle")
        assert result.synced_tag_digests == current_digests
