from __future__ import annotations

import configparser
import shlex
from dataclasses import dataclass
from pathlib import Path

# Canonical stage order — enforced by list_jobs() and run_pipeline()
STAGES = ("build", "test", "deploy")


@dataclass
class RepoConfig:
    id: str
    source: str
    commands: dict[str, list[str]]  # stage -> argv (already interpolated)


@dataclass
class PipelineConfig:
    repos: dict[str, RepoConfig]

    def list_jobs(self) -> list[str]:
        """All repo/stage pairs in pipeline order (repos in ini order, stages canonical)."""
        return [
            f"{repo_id}/{stage}"
            for repo_id in self.repos
            for stage in STAGES
            if stage in self.repos[repo_id].commands
        ]


def load_config(path: Path, *, version: str = "dev") -> PipelineConfig:
    """
    Parse repos.ini into a PipelineConfig.

    ``version`` overrides any ``version`` key in [DEFAULT]; it flows into
    every %(version)s placeholder in command strings.
    """
    cp = configparser.ConfigParser()
    cp.read(path)

    # vars= takes highest precedence in configparser interpolation — it wins
    # over both per-section values and [DEFAULT], so a CLI-supplied --version
    # always overrides whatever the file declares as its default.
    override = {"version": version}

    repos: dict[str, RepoConfig] = {}
    for section in cp.sections():
        source = cp.get(section, "source", fallback="")
        commands: dict[str, list[str]] = {}
        for stage in STAGES:
            raw = cp.get(section, stage, fallback=None, vars=override)
            if raw:
                commands[stage] = shlex.split(raw)
        repos[section] = RepoConfig(id=section, source=source, commands=commands)

    return PipelineConfig(repos=repos)
