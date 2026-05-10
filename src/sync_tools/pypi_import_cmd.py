from __future__ import annotations

import pathlib
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from .errors import SyncToolsError
from .pypi_archive import PyPIManifestEntry, extract_pypi_archive
from .pypi_ops import compute_file_sha256


@dataclass
class PyPIImportOptions:
    archive_path: pathlib.Path
    workers: int
    dry_run: bool
    verbose: bool = False


@dataclass
class PyPIImportSummary:
    succeeded: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    warned: list[tuple[str, list[str]]] = field(default_factory=list)


@dataclass
class _ImportResult:
    pkg_id: str
    success: bool
    error: Exception | None = None
    warnings: list[str] = field(default_factory=list)


def run_pypi_import(options: PyPIImportOptions) -> PyPIImportSummary:
    """Main PyPI import orchestration."""
    summary = PyPIImportSummary()

    with tempfile.TemporaryDirectory(prefix="sync-tools-pypi-import-") as _tmp:
        extract_dir = pathlib.Path(_tmp)
        manifest = extract_pypi_archive(options.archive_path, extract_dir)

        import_results: list[_ImportResult] = []

        with ThreadPoolExecutor(max_workers=options.workers) as pool:
            future_to_entry = {
                pool.submit(_import_one_package, entry, extract_dir, options.dry_run): entry
                for entry in manifest.entries
            }
            for future in as_completed(future_to_entry):
                import_results.append(future.result())

        for result in import_results:
            if result.success:
                summary.succeeded.append(result.pkg_id)
                if result.warnings:
                    summary.warned.append((result.pkg_id, result.warnings))
            else:
                err_msg = str(result.error) if result.error else "unknown error"
                summary.failed.append((result.pkg_id, err_msg))

    return summary


def _import_one_package(
    entry: PyPIManifestEntry,
    extract_dir: pathlib.Path,
    dry_run: bool,
) -> _ImportResult:
    """Worker function. Always returns _ImportResult, never raises."""
    try:
        pkg_dir = extract_dir / entry.package_dir
        dest = pathlib.Path(entry.dest_path)

        # Verify all files exist in the archive and sha256 matches before touching dest
        missing: list[str] = []
        corrupt: list[str] = []
        for filename, expected_sha256 in entry.synced_files.items():
            src = pkg_dir / filename
            if not src.exists():
                missing.append(filename)
            elif compute_file_sha256(src) != expected_sha256:
                corrupt.append(filename)

        if missing:
            raise SyncToolsError(
                f"Package files missing from archive for {entry.id}: {', '.join(missing)}"
            )
        if corrupt:
            raise SyncToolsError(
                f"SHA256 mismatch in archive for {entry.id}: {', '.join(corrupt)}"
            )

        if dry_run:
            return _ImportResult(pkg_id=entry.id, success=True)

        dest.mkdir(parents=True, exist_ok=True)

        for filename in entry.synced_files:
            shutil.copy2(pkg_dir / filename, dest / filename)

        return _ImportResult(pkg_id=entry.id, success=True)

    except SyncToolsError as exc:
        return _ImportResult(pkg_id=entry.id, success=False, error=exc)
    except Exception as exc:
        return _ImportResult(
            pkg_id=entry.id,
            success=False,
            error=SyncToolsError(f"Unexpected error: {exc}"),
        )
