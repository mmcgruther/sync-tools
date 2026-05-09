from __future__ import annotations

import pathlib
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from .docker_archive import create_docker_archive
from .docker_bundle import ImageBundleResult, execute_image_bundle, plan_image_bundle, safe_image_id
from .docker_ops import docker_pull, get_image_digest
from .docker_state import ImageConfig, LastImageSync, load_docker_state, save_docker_state
from .errors import SyncToolsError
from .state import now_utc_iso


@dataclass
class DockerExportOptions:
    state_path: pathlib.Path
    output_path: pathlib.Path
    workers: int
    dry_run: bool
    verbose: bool = False


@dataclass
class DockerExportSummary:
    succeeded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    warned: list[tuple[str, list[str]]] = field(default_factory=list)
    archive_path: pathlib.Path | None = None


@dataclass
class _ImageResult:
    image_id: str
    success: bool
    bundle_result: ImageBundleResult | None = None
    error: Exception | None = None
    warnings: list[str] = field(default_factory=list)
    skipped: bool = False


def run_docker_export(options: DockerExportOptions) -> DockerExportSummary:
    """Main Docker export orchestration."""
    images = load_docker_state(options.state_path)
    summary = DockerExportSummary()

    with tempfile.TemporaryDirectory(prefix="sync-tools-docker-export-") as _tmp:
        tmp_dir = pathlib.Path(_tmp)
        image_results: list[_ImageResult] = []

        with ThreadPoolExecutor(max_workers=options.workers) as pool:
            future_to_image = {
                pool.submit(_export_one_image, img, tmp_dir): img
                for img in images
            }
            for future in as_completed(future_to_image):
                image_results.append(future.result())

        bundle_results: list[ImageBundleResult] = []
        for result in image_results:
            if result.success:
                if result.skipped:
                    summary.skipped.append(result.image_id)
                else:
                    bundle_results.append(result.bundle_result)  # type: ignore[arg-type]
                    summary.succeeded.append(result.image_id)
                if result.warnings:
                    summary.warned.append((result.image_id, result.warnings))
            else:
                err_msg = str(result.error) if result.error else "unknown error"
                summary.failed.append((result.image_id, err_msg))

        if bundle_results and not options.dry_run:
            create_docker_archive(bundle_results, options.output_path, tmp_dir)
            summary.archive_path = options.output_path

        if not options.dry_run:
            _update_state(options.state_path, images, image_results)

    return summary


def _export_one_image(config: ImageConfig, tmp_dir: pathlib.Path) -> _ImageResult:
    """Worker function. Always returns _ImageResult, never raises."""
    try:
        # Pull all tags and collect current digests
        current_tag_digests: dict[str, str] = {}
        for tag in config.tags:
            ref = f"{config.source_ref}:{tag}"
            docker_pull(ref)
            current_tag_digests[tag] = get_image_digest(ref)

        plan = plan_image_bundle(config, current_tag_digests)

        if plan.no_changes:
            return _ImageResult(image_id=config.id, success=True, skipped=True)

        img_tmp = tmp_dir / "images" / safe_image_id(config.id)
        bundle_result = execute_image_bundle(plan, img_tmp)

        return _ImageResult(
            image_id=config.id,
            success=True,
            bundle_result=bundle_result,
            warnings=bundle_result.warnings,
        )

    except SyncToolsError as exc:
        return _ImageResult(image_id=config.id, success=False, error=exc)
    except Exception as exc:
        return _ImageResult(
            image_id=config.id,
            success=False,
            error=SyncToolsError(f"Unexpected error: {exc}"),
        )


def _update_state(
    state_path: pathlib.Path,
    images: list[ImageConfig],
    results: list[_ImageResult],
) -> None:
    timestamp = now_utc_iso()
    result_by_id = {r.image_id: r for r in results}

    for img in images:
        r = result_by_id.get(img.id)
        if r is None:
            continue
        if r.success and not r.skipped and r.bundle_result is not None:
            existing_digests = dict(img.last_sync.tag_digests) if img.last_sync else {}
            existing_digests.update(r.bundle_result.synced_tag_digests)
            img.last_sync = LastImageSync(timestamp=timestamp, tag_digests=existing_digests)
        elif r.success and r.skipped:
            if img.last_sync:
                img.last_sync = LastImageSync(
                    timestamp=timestamp, tag_digests=img.last_sync.tag_digests
                )
            else:
                img.last_sync = LastImageSync(timestamp=timestamp, tag_digests={})

    save_docker_state(state_path, images)
