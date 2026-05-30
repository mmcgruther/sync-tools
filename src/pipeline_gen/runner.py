from __future__ import annotations

import subprocess

from .config import PipelineConfig, STAGES


class StageError(Exception):
    def __init__(self, repo_id: str, stage: str, returncode: int) -> None:
        self.repo_id = repo_id
        self.stage = stage
        self.returncode = returncode
        super().__init__(f"{repo_id}/{stage} failed (exit {returncode})")


def _run(argv: list[str], repo_id: str, stage: str) -> None:
    result = subprocess.run(argv)
    if result.returncode != 0:
        raise StageError(repo_id, stage, result.returncode)


def run_job(config: PipelineConfig, job: str) -> None:
    """Execute a single repo/stage job (e.g. 'auth-service/build')."""
    repo_id, stage = job.split("/", 1)
    repo = config.repos[repo_id]
    _run(repo.commands[stage], repo_id, stage)


def run_pipeline(config: PipelineConfig, repo_id: str) -> None:
    """Execute all stages for one repo in canonical order (build → test → deploy)."""
    repo = config.repos[repo_id]
    for stage in STAGES:
        if stage in repo.commands:
            _run(repo.commands[stage], repo_id, stage)
