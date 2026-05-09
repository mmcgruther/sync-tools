from __future__ import annotations

import pathlib
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from .docker_archive import DockerManifestEntry, extract_docker_archive
from .docker_ops import docker_load, docker_push, docker_rmi, docker_tag
from .errors import SyncToolsError


@dataclass
class DockerImportOptions:
    archive_path: pathlib.Path
    workers: int
    dry_run: bool
    verbose: bool = False


@dataclass
class DockerImportSummary:
    succeeded: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    warned: list[tuple[str, list[str]]] = field(default_factory=list)


@dataclass
class _ImportResult:
    image_id: str
    success: bool
    error: Exception | None = None
    warnings: list[str] = field(default_factory=list)


def run_docker_import(options: DockerImportOptions) -> DockerImportSummary:
    """Main Docker import orchestration."""
    summary = DockerImportSummary()

    with tempfile.TemporaryDirectory(prefix="sync-tools-docker-import-") as _tmp:
        extract_dir = pathlib.Path(_tmp)
        manifest = extract_docker_archive(options.archive_path, extract_dir)

        import_results: list[_ImportResult] = []

        with ThreadPoolExecutor(max_workers=options.workers) as pool:
            future_to_entry = {
                pool.submit(
                    _import_one_image,
                    entry,
                    extract_dir,
                    options.dry_run,
                ): entry
                for entry in manifest.entries
            }
            for future in as_completed(future_to_entry):
                import_results.append(future.result())

        for result in import_results:
            if result.success:
                summary.succeeded.append(result.image_id)
                if result.warnings:
                    summary.warned.append((result.image_id, result.warnings))
            else:
                err_msg = str(result.error) if result.error else "unknown error"
                summary.failed.append((result.image_id, err_msg))

    return summary


def _import_one_image(
    entry: DockerManifestEntry,
    extract_dir: pathlib.Path,
    dry_run: bool,
) -> _ImportResult:
    """Worker function. Always returns _ImportResult, never raises."""
    try:
        image_tar = extract_dir / entry.image_filename
        if not image_tar.exists():
            raise SyncToolsError(f"Image tar not found in archive: {entry.image_filename}")

        if dry_run:
            return _ImportResult(image_id=entry.id, success=True)

        loaded_id = docker_load(image_tar)

        for tag in entry.synced_tag_digests:
            dest_ref = f"{entry.dest_ref}:{tag}"
            docker_tag(loaded_id, dest_ref)
            docker_push(dest_ref)

        docker_rmi(loaded_id)

        return _ImportResult(image_id=entry.id, success=True)

    except SyncToolsError as exc:
        return _ImportResult(image_id=entry.id, success=False, error=exc)
    except Exception as exc:
        return _ImportResult(
            image_id=entry.id,
            success=False,
            error=SyncToolsError(f"Unexpected error: {exc}"),
        )
