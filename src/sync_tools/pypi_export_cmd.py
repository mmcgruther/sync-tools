from __future__ import annotations

import pathlib
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from .errors import SyncToolsError
from .pypi_archive import create_pypi_archive
from .pypi_bundle import PackageBundleResult, execute_package_bundle, plan_package_bundle, safe_package_id
from .pypi_ops import pip_download
from .pypi_state import LastPackageSync, PackageConfig, load_pypi_state, save_pypi_state
from .state import now_utc_iso


@dataclass
class PyPIExportOptions:
    state_path: pathlib.Path
    output_path: pathlib.Path
    workers: int
    dry_run: bool
    verbose: bool = False


@dataclass
class PyPIExportSummary:
    succeeded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    warned: list[tuple[str, list[str]]] = field(default_factory=list)
    archive_path: pathlib.Path | None = None


@dataclass
class _PackageResult:
    pkg_id: str
    success: bool
    bundle_result: PackageBundleResult | None = None
    error: Exception | None = None
    warnings: list[str] = field(default_factory=list)
    skipped: bool = False


def run_pypi_export(options: PyPIExportOptions) -> PyPIExportSummary:
    """Main PyPI export orchestration."""
    packages = load_pypi_state(options.state_path)
    summary = PyPIExportSummary()

    with tempfile.TemporaryDirectory(prefix="sync-tools-pypi-export-") as _tmp:
        tmp_dir = pathlib.Path(_tmp)
        pkg_results: list[_PackageResult] = []

        with ThreadPoolExecutor(max_workers=options.workers) as pool:
            future_to_pkg = {
                pool.submit(_export_one_package, pkg, tmp_dir): pkg for pkg in packages
            }
            for future in as_completed(future_to_pkg):
                pkg_results.append(future.result())

        bundle_results: list[PackageBundleResult] = []
        for result in pkg_results:
            if result.success:
                if result.skipped:
                    summary.skipped.append(result.pkg_id)
                else:
                    bundle_results.append(result.bundle_result)  # type: ignore[arg-type]
                    summary.succeeded.append(result.pkg_id)
                if result.warnings:
                    summary.warned.append((result.pkg_id, result.warnings))
            else:
                err_msg = str(result.error) if result.error else "unknown error"
                summary.failed.append((result.pkg_id, err_msg))

        if bundle_results and not options.dry_run:
            create_pypi_archive(bundle_results, options.output_path, tmp_dir)
            summary.archive_path = options.output_path

        if not options.dry_run:
            _update_state(options.state_path, packages, pkg_results)

    return summary


def _export_one_package(config: PackageConfig, tmp_dir: pathlib.Path) -> _PackageResult:
    """Worker function. Always returns _PackageResult, never raises."""
    try:
        download_dir = tmp_dir / "downloads" / safe_package_id(config.id)
        download_dir.mkdir(parents=True, exist_ok=True)

        package_specs = [f"{config.package_name}=={v}" for v in config.versions]
        pip_download(
            packages=package_specs,
            dest_dir=download_dir,
            index_url=config.source_index,
            python_version=config.python_version,
            platform=config.platform,
            include_deps=config.include_deps,
            extra_args=config.extra_pip_args or None,
        )

        plan = plan_package_bundle(config, download_dir)

        if plan.no_changes:
            return _PackageResult(
                pkg_id=config.id, success=True, skipped=True, warnings=plan.warnings
            )

        bundle_dir = tmp_dir / "packages" / safe_package_id(config.id)
        bundle_result = execute_package_bundle(plan, bundle_dir)

        return _PackageResult(
            pkg_id=config.id,
            success=True,
            bundle_result=bundle_result,
            warnings=bundle_result.warnings,
        )

    except SyncToolsError as exc:
        return _PackageResult(pkg_id=config.id, success=False, error=exc)
    except Exception as exc:
        return _PackageResult(
            pkg_id=config.id,
            success=False,
            error=SyncToolsError(f"Unexpected error: {exc}"),
        )


def _update_state(
    state_path: pathlib.Path,
    packages: list[PackageConfig],
    results: list[_PackageResult],
) -> None:
    timestamp = now_utc_iso()
    result_by_id = {r.pkg_id: r for r in results}

    for pkg in packages:
        r = result_by_id.get(pkg.id)
        if r is None:
            continue
        if r.success and not r.skipped and r.bundle_result is not None:
            pkg.last_sync = LastPackageSync(
                timestamp=timestamp,
                synced_files=r.bundle_result.all_synced_files,
            )
        elif r.success and r.skipped:
            if pkg.last_sync:
                pkg.last_sync = LastPackageSync(
                    timestamp=timestamp,
                    synced_files=pkg.last_sync.synced_files,
                )
            else:
                pkg.last_sync = LastPackageSync(timestamp=timestamp, synced_files={})

    save_pypi_state(state_path, packages)
