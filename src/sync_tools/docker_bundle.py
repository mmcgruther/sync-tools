from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

from .docker_ops import docker_save, get_image_digest
from .docker_state import ImageConfig, LastImageSync


def safe_image_id(image_id: str) -> str:
    """Replace slashes with double-underscore for use in filesystem paths."""
    return image_id.replace("/", "__")


@dataclass
class ImageBundlePlan:
    config: ImageConfig
    tags_to_sync: dict[str, str]  # tag -> current digest (only changed/new tags)
    warnings: list[str] = field(default_factory=list)
    no_changes: bool = False


@dataclass
class ImageBundleResult:
    config: ImageConfig
    image_tar: pathlib.Path
    synced_tag_digests: dict[str, str]  # tag -> digest at time of export
    warnings: list[str] = field(default_factory=list)


def plan_image_bundle(
    config: ImageConfig,
    current_tag_digests: dict[str, str],
) -> ImageBundlePlan:
    """Compare each tag's current digest against last_sync.tag_digests.

    Tags absent from last_sync are treated as new. Returns no_changes=True
    if every tag in config.tags is unchanged.
    """
    known_digests = config.last_sync.tag_digests if config.last_sync else {}
    tags_to_sync: dict[str, str] = {}

    for tag in config.tags:
        current = current_tag_digests.get(tag)
        if current is None:
            continue  # tag not found locally (pull may have failed); skip
        if current != known_digests.get(tag):
            tags_to_sync[tag] = current

    if not tags_to_sync:
        return ImageBundlePlan(config=config, tags_to_sync={}, no_changes=True)

    return ImageBundlePlan(config=config, tags_to_sync=tags_to_sync)


def execute_image_bundle(
    plan: ImageBundlePlan,
    tmp_dir: pathlib.Path,
) -> ImageBundleResult:
    """docker save all changed tags into a single tar. Returns ImageBundleResult."""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tar_path = tmp_dir / "image.tar"

    refs = [f"{plan.config.source_ref}:{tag}" for tag in plan.tags_to_sync]
    docker_save(refs, tar_path)

    return ImageBundleResult(
        config=plan.config,
        image_tar=tar_path,
        synced_tag_digests=dict(plan.tags_to_sync),
    )
