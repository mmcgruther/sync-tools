from __future__ import annotations

import pathlib
import shutil
from dataclasses import dataclass, field

from .pypi_ops import compute_file_sha256
from .pypi_state import PackageConfig


def safe_package_id(pkg_id: str) -> str:
    """Replace slashes with double-underscore for use in filesystem paths."""
    return pkg_id.replace("/", "__")


@dataclass
class PackageBundlePlan:
    config: PackageConfig
    new_files: dict[str, pathlib.Path]  # filename -> path in download_dir (only new/changed)
    all_files: dict[str, str]  # filename -> sha256 (every file downloaded this run)
    no_changes: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class PackageBundleResult:
    config: PackageConfig
    package_dir: pathlib.Path  # directory containing only the new files
    synced_files: dict[str, str]  # filename -> sha256 (new files in this bundle)
    all_synced_files: dict[str, str]  # filename -> sha256 (union of prior state + this run)
    warnings: list[str] = field(default_factory=list)


def plan_package_bundle(
    config: PackageConfig,
    download_dir: pathlib.Path,
) -> PackageBundlePlan:
    """Diff downloaded files against last_sync.synced_files by filename + sha256."""
    known_files = config.last_sync.synced_files if config.last_sync else {}

    all_files: dict[str, str] = {}
    new_files: dict[str, pathlib.Path] = {}

    for f in sorted(download_dir.iterdir()):
        if not f.is_file():
            continue
        sha256 = compute_file_sha256(f)
        all_files[f.name] = sha256
        if sha256 != known_files.get(f.name):
            new_files[f.name] = f

    if not new_files:
        return PackageBundlePlan(config=config, new_files={}, all_files=all_files, no_changes=True)

    return PackageBundlePlan(config=config, new_files=new_files, all_files=all_files)


def execute_package_bundle(
    plan: PackageBundlePlan,
    bundle_dir: pathlib.Path,
) -> PackageBundleResult:
    """Copy new package files into bundle_dir."""
    bundle_dir.mkdir(parents=True, exist_ok=True)

    for filename, src_path in plan.new_files.items():
        shutil.copy2(src_path, bundle_dir / filename)

    synced_files = {name: plan.all_files[name] for name in plan.new_files}

    # Merge prior known files with all files from this run (handles new versions)
    existing = plan.config.last_sync.synced_files if plan.config.last_sync else {}
    all_synced = {**existing, **plan.all_files}

    return PackageBundleResult(
        config=plan.config,
        package_dir=bundle_dir,
        synced_files=synced_files,
        all_synced_files=all_synced,
    )
