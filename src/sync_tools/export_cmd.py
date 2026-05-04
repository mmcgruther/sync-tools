from __future__ import annotations

import os
import pathlib
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from .archive import BundleResult, create_archive
from .bundle import execute_bundle, plan_bundle, safe_repo_id
from .errors import RepoResult, SyncToolsError
from .git_ops import clone_to_temp, is_git_repo, list_refs
from .state import LastSync, RepoConfig, load_state, now_utc_iso, save_state


@dataclass
class ExportOptions:
    state_path: pathlib.Path
    output_path: pathlib.Path
    workers: int
    allow_rebase: bool
    dry_run: bool
    verbose: bool = False


@dataclass
class ExportSummary:
    succeeded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # no changes
    failed: list[tuple[str, str]] = field(default_factory=list)
    warned: list[tuple[str, list[str]]] = field(default_factory=list)
    archive_path: pathlib.Path | None = None


def run_export(options: ExportOptions) -> ExportSummary:
    """Main export orchestration."""
    repos = load_state(options.state_path)
    summary = ExportSummary()

    with tempfile.TemporaryDirectory(prefix="sync-tools-export-") as _tmp:
        tmp_dir = pathlib.Path(_tmp)
        bundle_results: list[BundleResult] = []
        results: list[RepoResult] = []

        workers = options.workers or _default_workers()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_repo = {
                pool.submit(
                    _export_one_repo,
                    repo,
                    tmp_dir,
                    options.allow_rebase,
                ): repo
                for repo in repos
            }
            for future in as_completed(future_to_repo):
                result: RepoResult = future.result()
                results.append(result)

        for result in results:
            if result.success:
                if result.bundle_result is not None:
                    bundle_results.append(result.bundle_result)
                    summary.succeeded.append(result.repo_id)
                else:
                    summary.skipped.append(result.repo_id)
                if result.warnings:
                    summary.warned.append((result.repo_id, result.warnings))
            else:
                err_msg = str(result.error) if result.error else "unknown error"
                summary.failed.append((result.repo_id, err_msg))

        # Create archive if we have bundles and this is not a dry run
        if bundle_results and not options.dry_run:
            create_archive(bundle_results, options.output_path, tmp_dir)
            summary.archive_path = options.output_path

        # Update state: update timestamp for all repos checked; update refs only for succeeded ones
        if not options.dry_run:
            succeeded_ids = set(summary.succeeded)
            # Map bundle results by repo_id for quick lookup
            bundle_by_id = {br.repo.id: br for br in bundle_results}

            timestamp = now_utc_iso()
            for repo in repos:
                if repo.id in succeeded_ids and repo.id in bundle_by_id:
                    br = bundle_by_id[repo.id]
                    # Merge new exported refs into the existing ref set
                    existing_refs = dict(repo.last_sync.refs) if repo.last_sync else {}
                    existing_refs.update(br.exported_refs)
                    existing_lfs_oids = list(repo.last_sync.lfs_oids) if repo.last_sync else []
                    merged_lfs_oids = sorted(set(existing_lfs_oids) | br.lfs_objects.keys())
                    repo.last_sync = LastSync(
                        timestamp=timestamp, refs=existing_refs, lfs_oids=merged_lfs_oids
                    )
                elif repo.id in set(summary.skipped):
                    # Update timestamp but keep existing refs unchanged
                    if repo.last_sync:
                        repo.last_sync = LastSync(timestamp=timestamp, refs=repo.last_sync.refs)
                    else:
                        # No prior sync and no changes — record empty state with timestamp
                        repo.last_sync = LastSync(timestamp=timestamp, refs={})

            save_state(options.state_path, repos)

    return summary


def _export_one_repo(
    repo: RepoConfig,
    tmp_dir: pathlib.Path,
    allow_rebase: bool,
) -> RepoResult:
    """Worker function. Always returns RepoResult, never raises."""
    try:
        source_path = _resolve_source(repo, tmp_dir)
        current_refs = list_refs(source_path)
        bplan = plan_bundle(
            repo=repo,
            current_refs=current_refs,
            source_path=source_path,
            allow_rebase=allow_rebase,
        )

        if bplan.no_changes:
            return RepoResult(
                repo_id=repo.id,
                success=True,
                warnings=bplan.warnings,
                notes=["No changes since last sync — skipped."],
            )

        bundle_dir = tmp_dir / "bundles" / safe_repo_id(repo.id)
        result = execute_bundle(bplan, bundle_dir, source_path, current_refs)

        return RepoResult(
            repo_id=repo.id,
            success=True,
            warnings=result.warnings,
            bundle_result=result,
        )

    except SyncToolsError as exc:
        return RepoResult(repo_id=repo.id, success=False, error=exc)
    except Exception as exc:  # unexpected — wrap to avoid killing the pool
        return RepoResult(
            repo_id=repo.id,
            success=False,
            error=SyncToolsError(f"Unexpected error: {exc}"),
        )


def _resolve_source(repo: RepoConfig, tmp_dir: pathlib.Path) -> pathlib.Path:
    """Return a local path to the source repo, cloning if necessary."""
    if repo.source_local_path:
        p = pathlib.Path(repo.source_local_path)
        if is_git_repo(p):
            return p

    # Fall back to cloning from URL into a per-repo temp sub-directory
    clone_dir = tmp_dir / "clones" / safe_repo_id(repo.id)
    clone_dir.mkdir(parents=True, exist_ok=True)
    return clone_to_temp(repo.source_url, clone_dir)


def _default_workers() -> int:
    return min(8, os.cpu_count() or 1)
