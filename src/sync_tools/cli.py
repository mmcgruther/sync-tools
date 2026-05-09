from __future__ import annotations

import os
import pathlib
import sys

import click

from . import __version__
from .docker_export_cmd import DockerExportOptions, DockerExportSummary, run_docker_export
from .docker_import_cmd import DockerImportOptions, DockerImportSummary, run_docker_import
from .errors import StateFileError, SyncToolsError
from .export_cmd import ExportOptions, ExportSummary, run_export
from .import_cmd import ImportOptions, ImportSummary, run_import


def _default_workers() -> int:
    return min(8, os.cpu_count() or 1)


@click.group()
@click.version_option(__version__)
def main() -> None:
    """Sync git repositories across air-gapped networks using git bundles."""


@main.command("export")
@click.argument("state_file", type=click.Path(exists=True, dir_okay=False, path_type=pathlib.Path))
@click.option(
    "--output",
    "-o",
    required=True,
    type=click.Path(dir_okay=False, path_type=pathlib.Path),
    help="Output archive path (.tar.gz).",
)
@click.option(
    "--workers",
    "-w",
    default=None,
    type=int,
    help=f"Thread pool size (default: {_default_workers()}).",
)
@click.option(
    "--allow-rebase",
    is_flag=True,
    default=False,
    help="On detected force-push/rebase, create full bundle for that branch instead of failing.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be bundled without creating archive or updating state.",
)
@click.option("--verbose", "-v", is_flag=True, default=False)
def export_cmd(
    state_file: pathlib.Path,
    output: pathlib.Path,
    workers: int | None,
    allow_rebase: bool,
    dry_run: bool,
    verbose: bool,
) -> None:
    """Read STATE_FILE, create incremental bundles, write archive, update state.

    \b
    Exit codes:
      0  all repos succeeded
      1  at least one repo failed (others may have succeeded)
      2  fatal error (state file unreadable, invalid, etc.)
    """
    options = ExportOptions(
        state_path=state_file,
        output_path=output,
        workers=workers or _default_workers(),
        allow_rebase=allow_rebase,
        dry_run=dry_run,
        verbose=verbose,
    )

    try:
        summary = run_export(options)
    except StateFileError as exc:
        click.echo(f"FATAL: {exc}", err=True)
        sys.exit(2)
    except SyncToolsError as exc:
        click.echo(f"FATAL: {exc}", err=True)
        sys.exit(2)

    _print_export_summary(summary, verbose=verbose, dry_run=dry_run)
    sys.exit(1 if summary.failed else 0)


@main.command("import")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=pathlib.Path))
@click.option(
    "--state-file",
    "-s",
    type=click.Path(dir_okay=False, path_type=pathlib.Path),
    default=None,
    help="Optional state file to override dest_path per repo.",
)
@click.option(
    "--workers",
    "-w",
    default=None,
    type=int,
    help=f"Thread pool size (default: {_default_workers()}).",
)
@click.option(
    "--auto-init",
    is_flag=True,
    default=False,
    help="Create missing bare destination repos with git init --bare.",
)
@click.option(
    "--timeout",
    default=300,
    type=int,
    help="Per-fetch timeout in seconds (default: 300).",
)
@click.option(
    "--retries",
    default=3,
    type=int,
    help="Number of retry attempts on transient fetch failure (default: 3).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Verify bundles without applying them to destination repos.",
)
@click.option("--verbose", "-v", is_flag=True, default=False)
def import_cmd(
    archive: pathlib.Path,
    state_file: pathlib.Path | None,
    workers: int | None,
    auto_init: bool,
    timeout: int,
    retries: int,
    dry_run: bool,
    verbose: bool,
) -> None:
    """Extract ARCHIVE and apply git bundles to destination repositories.

    \b
    Exit codes:
      0  all repos succeeded
      1  at least one repo failed (others may have succeeded)
      2  fatal error (archive unreadable, manifest invalid, etc.)
    """
    options = ImportOptions(
        archive_path=archive,
        state_path=state_file,
        workers=workers or _default_workers(),
        auto_init=auto_init,
        timeout=timeout,
        max_retries=retries,
        dry_run=dry_run,
        verbose=verbose,
    )

    try:
        summary = run_import(options)
    except SyncToolsError as exc:
        click.echo(f"FATAL: {exc}", err=True)
        sys.exit(2)

    _print_import_summary(summary, verbose=verbose, dry_run=dry_run)
    sys.exit(1 if summary.failed else 0)


def _print_export_summary(summary: ExportSummary, verbose: bool, dry_run: bool) -> None:
    prefix = "[DRY RUN] " if dry_run else ""
    warn_ids = {repo_id for repo_id, _ in summary.warned}

    click.echo(
        f"{prefix}Export complete: {len(summary.succeeded)} bundled, "
        f"{len(summary.skipped)} skipped (no changes), "
        f"{len(summary.failed)} failed"
        + (f", {len(summary.warned)} with warnings" if summary.warned else "")
    )

    if verbose or summary.failed or summary.warned:
        warned_map = {repo_id: warns for repo_id, warns in summary.warned}
        for repo_id in summary.succeeded:
            tag = "WARN" if repo_id in warn_ids else "  OK"
            warns = warned_map.get(repo_id, [])
            warn_str = "; ".join(warns)
            click.echo(f"  {tag} {repo_id}" + (f": {warn_str}" if warns else ""))
        for repo_id in summary.skipped:
            if verbose:
                click.echo(f"  SKIP {repo_id}: no changes since last sync")
        for repo_id, error_msg in summary.failed:
            click.echo(f"  FAIL {repo_id}: {error_msg}", err=True)

    if summary.archive_path:
        click.echo(f"Archive: {summary.archive_path}")


@main.command("docker-export")
@click.argument("state_file", type=click.Path(exists=True, dir_okay=False, path_type=pathlib.Path))
@click.option(
    "--output",
    "-o",
    required=True,
    type=click.Path(dir_okay=False, path_type=pathlib.Path),
    help="Output archive path (.tar.gz).",
)
@click.option(
    "--workers",
    "-w",
    default=None,
    type=int,
    help=f"Thread pool size (default: {_default_workers()}).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Pull images and detect changes without creating archive or updating state.",
)
@click.option("--verbose", "-v", is_flag=True, default=False)
def docker_export_cmd(
    state_file: pathlib.Path,
    output: pathlib.Path,
    workers: int | None,
    dry_run: bool,
    verbose: bool,
) -> None:
    """Read STATE_FILE, pull Docker images, write archive, update state.

    \b
    Exit codes:
      0  all images succeeded
      1  at least one image failed (others may have succeeded)
      2  fatal error (state file unreadable, invalid, etc.)
    """
    options = DockerExportOptions(
        state_path=state_file,
        output_path=output,
        workers=workers or _default_workers(),
        dry_run=dry_run,
        verbose=verbose,
    )

    try:
        summary = run_docker_export(options)
    except StateFileError as exc:
        click.echo(f"FATAL: {exc}", err=True)
        sys.exit(2)
    except SyncToolsError as exc:
        click.echo(f"FATAL: {exc}", err=True)
        sys.exit(2)

    _print_docker_export_summary(summary, verbose=verbose, dry_run=dry_run)
    sys.exit(1 if summary.failed else 0)


@main.command("docker-import")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=pathlib.Path))
@click.option(
    "--workers",
    "-w",
    default=None,
    type=int,
    help=f"Thread pool size (default: {_default_workers()}).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Validate archive without running docker operations.",
)
@click.option("--verbose", "-v", is_flag=True, default=False)
def docker_import_cmd(
    archive: pathlib.Path,
    workers: int | None,
    dry_run: bool,
    verbose: bool,
) -> None:
    """Extract ARCHIVE and push Docker images to destination registries.

    \b
    Exit codes:
      0  all images succeeded
      1  at least one image failed (others may have succeeded)
      2  fatal error (archive unreadable, manifest invalid, etc.)
    """
    options = DockerImportOptions(
        archive_path=archive,
        workers=workers or _default_workers(),
        dry_run=dry_run,
        verbose=verbose,
    )

    try:
        summary = run_docker_import(options)
    except SyncToolsError as exc:
        click.echo(f"FATAL: {exc}", err=True)
        sys.exit(2)

    _print_docker_import_summary(summary, verbose=verbose, dry_run=dry_run)
    sys.exit(1 if summary.failed else 0)


def _print_docker_export_summary(
    summary: DockerExportSummary, verbose: bool, dry_run: bool
) -> None:
    prefix = "[DRY RUN] " if dry_run else ""
    warn_ids = {img_id for img_id, _ in summary.warned}

    click.echo(
        f"{prefix}Docker export complete: {len(summary.succeeded)} exported, "
        f"{len(summary.skipped)} skipped (no changes), "
        f"{len(summary.failed)} failed"
        + (f", {len(summary.warned)} with warnings" if summary.warned else "")
    )

    if verbose or summary.failed or summary.warned:
        warned_map = {img_id: warns for img_id, warns in summary.warned}
        for img_id in summary.succeeded:
            tag = "WARN" if img_id in warn_ids else "  OK"
            warns = warned_map.get(img_id, [])
            warn_str = "; ".join(warns)
            click.echo(f"  {tag} {img_id}" + (f": {warn_str}" if warns else ""))
        for img_id in summary.skipped:
            if verbose:
                click.echo(f"  SKIP {img_id}: no changes since last sync")
        for img_id, error_msg in summary.failed:
            click.echo(f"  FAIL {img_id}: {error_msg}", err=True)

    if summary.archive_path:
        click.echo(f"Archive: {summary.archive_path}")


def _print_docker_import_summary(
    summary: DockerImportSummary, verbose: bool, dry_run: bool
) -> None:
    prefix = "[DRY RUN] " if dry_run else ""
    warn_ids = {img_id for img_id, _ in summary.warned}

    click.echo(
        f"{prefix}Docker import complete: {len(summary.succeeded)} succeeded, "
        f"{len(summary.failed)} failed"
        + (f", {len(summary.warned)} with warnings" if summary.warned else "")
    )

    if verbose or summary.failed or summary.warned:
        warned_map = {img_id: warns for img_id, warns in summary.warned}
        for img_id in summary.succeeded:
            tag = "WARN" if img_id in warn_ids else "  OK"
            warns = warned_map.get(img_id, [])
            warn_str = "; ".join(warns)
            click.echo(f"  {tag} {img_id}" + (f": {warn_str}" if warns else ""))
        for img_id, error_msg in summary.failed:
            click.echo(f"  FAIL {img_id}: {error_msg}", err=True)


def _print_import_summary(summary: ImportSummary, verbose: bool, dry_run: bool) -> None:
    prefix = "[DRY RUN] " if dry_run else ""
    warn_ids = {repo_id for repo_id, _ in summary.warned}

    click.echo(
        f"{prefix}Import complete: {len(summary.succeeded)} succeeded, "
        f"{len(summary.failed)} failed"
        + (f", {len(summary.warned)} with warnings" if summary.warned else "")
    )

    if verbose or summary.failed or summary.warned:
        warned_map = {repo_id: warns for repo_id, warns in summary.warned}
        for repo_id in summary.succeeded:
            tag = "WARN" if repo_id in warn_ids else "  OK"
            warns = warned_map.get(repo_id, [])
            warn_str = "; ".join(warns)
            click.echo(f"  {tag} {repo_id}" + (f": {warn_str}" if warns else ""))
        for repo_id, error_msg in summary.failed:
            click.echo(f"  FAIL {repo_id}: {error_msg}", err=True)
