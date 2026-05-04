from __future__ import annotations

import os
import pathlib
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from .archive import ManifestEntry, extract_archive
from .errors import MissingDestRepoError, RepoResult, SyncToolsError
from .git_ops import fetch_bundle, init_bare_repo, is_git_repo, resolve_ref, verify_bundle
from .state import load_state


@dataclass
class ImportOptions:
    archive_path: pathlib.Path
    state_path: pathlib.Path | None
    workers: int
    auto_init: bool
    timeout: int
    max_retries: int
    dry_run: bool
    verbose: bool = False


@dataclass
class ImportSummary:
    succeeded: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    warned: list[tuple[str, list[str]]] = field(default_factory=list)


def run_import(options: ImportOptions) -> ImportSummary:
    """Main import orchestration."""
    summary = ImportSummary()

    # Load optional state file for dest_path overrides
    dest_overrides: dict[str, str] = {}
    if options.state_path:
        repos = load_state(options.state_path)
        dest_overrides = {r.id: r.dest_path for r in repos}

    with tempfile.TemporaryDirectory(prefix="sync-tools-import-") as _tmp:
        extract_dir = pathlib.Path(_tmp)
        manifest = extract_archive(options.archive_path, extract_dir)

        workers = options.workers or _default_workers()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_entry = {
                pool.submit(
                    _import_one_repo,
                    entry,
                    extract_dir,
                    dest_overrides.get(entry.repo_id),
                    options.auto_init,
                    options.timeout,
                    options.max_retries,
                    options.dry_run,
                ): entry
                for entry in manifest.entries
            }
            for future in as_completed(future_to_entry):
                result: RepoResult = future.result()
                if result.success:
                    summary.succeeded.append(result.repo_id)
                    if result.warnings:
                        summary.warned.append((result.repo_id, result.warnings))
                else:
                    err_msg = str(result.error) if result.error else "unknown error"
                    summary.failed.append((result.repo_id, err_msg))

    return summary


def _import_one_repo(
    entry: ManifestEntry,
    extract_dir: pathlib.Path,
    dest_path_override: str | None,
    auto_init: bool,
    timeout: int,
    max_retries: int,
    dry_run: bool,
) -> RepoResult:
    """Worker function. Always returns RepoResult, never raises."""
    warnings: list[str] = []
    try:
        dest = pathlib.Path(dest_path_override or entry.dest_path)
        bundle_path = extract_dir / entry.bundle_filename

        if not is_git_repo(dest):
            if auto_init:
                warnings.append(f"Destination {dest} not found; initializing bare repo.")
                init_bare_repo(dest)
            else:
                raise MissingDestRepoError(
                    f"Destination repo not found: {dest}. Use --auto-init to create it."
                )

        if not bundle_path.exists():
            raise SyncToolsError(f"Bundle file missing from archive: {entry.bundle_filename}")

        # Copy LFS objects to destination before bundle fetch
        if entry.lfs_objects and entry.lfs_dir and not dry_run:
            dest_lfs = dest / "lfs" / "objects"
            for oid in entry.lfs_objects:
                src_obj = extract_dir / entry.lfs_dir / oid[:2] / oid[2:4] / oid
                if src_obj.exists():
                    dst_obj = dest_lfs / oid[:2] / oid[2:4] / oid
                    dst_obj.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_obj, dst_obj)

        verify_bundle(dest, bundle_path)

        refspecs = [f"+{ref}:{ref}" for ref in entry.exported_refs]

        if not dry_run:
            fetch_bundle(
                dest,
                bundle_path,
                refspecs,
                timeout=timeout,
                max_retries=max_retries,
            )
            # Post-fetch verification: each ref SHA must match manifest
            mismatches: list[str] = []
            for ref, expected_sha in entry.exported_refs.items():
                try:
                    actual_sha = resolve_ref(dest, ref)
                except SyncToolsError:
                    mismatches.append(f"{ref}: not found after fetch")
                    continue
                if actual_sha != expected_sha:
                    mismatches.append(f"{ref}: expected {expected_sha[:8]}, got {actual_sha[:8]}")
            if mismatches:
                raise SyncToolsError(
                    f"Post-fetch verification failed for {entry.repo_id}:\n"
                    + "\n".join(f"  {m}" for m in mismatches)
                )

        return RepoResult(repo_id=entry.repo_id, success=True, warnings=warnings)

    except SyncToolsError as exc:
        return RepoResult(repo_id=entry.repo_id, success=False, error=exc, warnings=warnings)
    except Exception as exc:
        return RepoResult(
            repo_id=entry.repo_id,
            success=False,
            error=SyncToolsError(f"Unexpected error: {exc}"),
            warnings=warnings,
        )


def _default_workers() -> int:
    return min(8, os.cpu_count() or 1)
