from __future__ import annotations

import click
from pathlib import Path

from .config import load_config
from .runner import run_job, run_pipeline, StageError


@click.group()
def cli() -> None:
    """Pipeline generator — parse repos.ini and run build/test/deploy stages."""


@cli.command("list-jobs")
@click.argument("config_file", type=click.Path(exists=True, path_type=Path))
def list_jobs_cmd(config_file: Path) -> None:
    """Print every repo/stage job in pipeline order."""
    config = load_config(config_file)
    for job in config.list_jobs():
        click.echo(job)


@cli.command("run")
@click.argument("config_file", type=click.Path(exists=True, path_type=Path))
@click.argument("job")
@click.option("--version", default="dev", show_default=True, help="Image/artifact version tag.")
def run_cmd(config_file: Path, job: str, version: str) -> None:
    """Run a single JOB (repo/stage) from CONFIG_FILE."""
    config = load_config(config_file, version=version)
    try:
        run_job(config, job)
    except (KeyError, ValueError) as exc:
        raise click.ClickException(f"Unknown job {job!r}: {exc}")
    except StageError as exc:
        raise click.ClickException(str(exc))
