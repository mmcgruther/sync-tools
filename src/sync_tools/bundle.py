from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

from .errors import (
    CaseConflictError,
    HierarchyConflictError,
    LFSDetectedError,
    LFSObjectMissingError,
    RebaseDetectedError,
    TagMovedError,
)
from .git_ops import (
    create_bundle,
    find_case_conflicts,
    find_hierarchy_conflicts,
    has_lfs_objects,
    is_ancestor,
    lfs_object_path,
    lfs_oids_for_refs,
    refs_with_lfs,
)
from .state import RepoConfig


@dataclass
class BundlePlan:
    repo: RepoConfig
    refs_to_bundle: list[str]
    since_shas: dict[str, str]
    warnings: list[str]
    is_full_bundle: bool
    no_changes: bool = False


@dataclass
class BundleResult:
    repo: RepoConfig
    bundle_path: pathlib.Path
    exported_refs: dict[str, str]  # refname -> SHA at time of export
    warnings: list[str] = field(default_factory=list)
    lfs_objects: dict[str, pathlib.Path] = field(default_factory=dict)  # oid -> local path


def plan_bundle(
    repo: RepoConfig,
    current_refs: dict[str, str],
    source_path: pathlib.Path,
    allow_rebase: bool = False,
) -> BundlePlan:
    """
    Examine current_refs against repo.last_sync.refs and produce a BundlePlan.

    Raises:
        LFSDetectedError  — if LFS objects found and repo.lfs_mode is None
        CaseConflictError — if any refs differ only by case
        TagMovedError     — if a tag moved to a non-ancestor commit
        RebaseDetectedError — if a branch was force-pushed and allow_rebase is False
    """
    warnings: list[str] = []

    # LFS handling
    lfs_refs: set[str] = set()
    if has_lfs_objects(source_path):
        if repo.lfs_mode == "skip":
            lfs_refs = refs_with_lfs(source_path, list(current_refs.keys()))
            for ref in sorted(lfs_refs):
                warnings.append(
                    f"Skipping {ref}: contains Git LFS objects (bundle would be incomplete)."
                )
        elif repo.lfs_mode == "allow":
            warnings.append(
                "Git LFS detected: bundle contains only pointer files, not actual LFS object data."
            )
        elif repo.lfs_mode == "sync":
            warnings.append("Git LFS detected: LFS objects will be included in the sync archive.")
        else:
            raise LFSDetectedError(
                f"Repo {repo.id} uses Git LFS. Bundles contain only pointer files, not "
                "LFS object data. Set lfs_mode to 'skip' or 'allow' in the state file."
            )

    filtered_refs = {k: v for k, v in current_refs.items() if k not in lfs_refs}

    # Case conflict check
    conflicts = find_case_conflicts(current_refs)
    if conflicts:
        pairs = ", ".join(f"({a!r} vs {b!r})" for a, b in conflicts)
        raise CaseConflictError(
            f"Repo {repo.id} has refs that differ only by case {pairs}. "
            "This causes failures on case-insensitive filesystems (e.g. Windows)."
        )

    # Hierarchy conflict check
    hierarchy_conflicts = find_hierarchy_conflicts(current_refs)
    if hierarchy_conflicts:
        pairs = ", ".join(f"({a!r} vs {b!r})" for a, b in hierarchy_conflicts)
        raise HierarchyConflictError(
            f"Repo {repo.id} has refs where one name is a path prefix of another: {pairs}. "
            "This cannot be checked out on a standard filesystem."
        )

    # First-time sync
    if repo.last_sync is None:
        refs_list = list(filtered_refs.keys())
        if not refs_list:
            return BundlePlan(
                repo=repo,
                refs_to_bundle=[],
                since_shas={},
                warnings=warnings,
                is_full_bundle=False,
                no_changes=True,
            )
        return BundlePlan(
            repo=repo,
            refs_to_bundle=refs_list,
            since_shas={},
            warnings=warnings,
            is_full_bundle=not lfs_refs,  # use --all only when no refs were filtered
        )

    old_refs = repo.last_sync.refs
    refs_to_bundle: list[str] = []
    since_shas: dict[str, str] = {}

    for ref, new_sha in filtered_refs.items():
        old_sha = old_refs.get(ref)

        if old_sha is None:
            # New ref not seen before — include fully (no since range)
            refs_to_bundle.append(ref)
            continue

        if old_sha == new_sha:
            # Unchanged — skip
            continue

        # Ref changed — check ancestry
        _is_tag = ref.startswith("refs/tags/")

        ancestor = is_ancestor(source_path, old_sha, new_sha)

        if not ancestor:
            if _is_tag:
                raise TagMovedError(
                    f"Tag {ref} in repo {repo.id} moved from {old_sha[:8]} to {new_sha[:8]} "
                    "(new commit is not a descendant of old commit). "
                    "Force-moving tags breaks incremental bundles."
                )
            else:
                if not allow_rebase:
                    raise RebaseDetectedError(
                        f"Branch {ref} in repo {repo.id} was rebased or force-pushed: "
                        f"{old_sha[:8]} is not an ancestor of {new_sha[:8]}. "
                        "Use --allow-rebase to create a full bundle for this branch."
                    )
                warnings.append(
                    f"Branch {ref} was rebased/force-pushed ({old_sha[:8]}→{new_sha[:8]}); "
                    "creating full bundle for this branch."
                )
                refs_to_bundle.append(ref)
                # No since_sha entry → full range for this ref
                continue

        # Clean fast-forward (or tag advancing forward)
        refs_to_bundle.append(ref)
        since_shas[ref] = old_sha

    if not refs_to_bundle:
        return BundlePlan(
            repo=repo,
            refs_to_bundle=[],
            since_shas={},
            warnings=warnings,
            is_full_bundle=False,
            no_changes=True,
        )

    return BundlePlan(
        repo=repo,
        refs_to_bundle=refs_to_bundle,
        since_shas=since_shas,
        warnings=warnings,
        is_full_bundle=False,
    )


def safe_repo_id(repo_id: str) -> str:
    """Convert a repo ID like 'org/repo' to a filesystem-safe string 'org__repo'."""
    return repo_id.replace("/", "__").replace("\\", "__")


def execute_bundle(
    plan: BundlePlan,
    output_dir: pathlib.Path,
    source_path: pathlib.Path,
    current_refs: dict[str, str],
) -> BundleResult:
    """
    Execute a BundlePlan by calling git bundle create.
    Returns a BundleResult with the bundle path and ref snapshot.
    """
    bundle_path = output_dir / f"{safe_repo_id(plan.repo.id)}.git"
    output_dir.mkdir(parents=True, exist_ok=True)

    create_bundle(
        repo_path=source_path,
        bundle_path=bundle_path,
        refs=plan.refs_to_bundle,
        since_shas=plan.since_shas,
        is_full=plan.is_full_bundle,
    )

    # Record the SHA of each exported ref at the time of export
    exported_refs = {ref: current_refs[ref] for ref in plan.refs_to_bundle}

    # Collect LFS object files for "sync" mode
    lfs_objects: dict[str, pathlib.Path] = {}
    if plan.repo.lfs_mode == "sync" and plan.refs_to_bundle:
        already_synced = set(plan.repo.last_sync.lfs_oids) if plan.repo.last_sync else set()
        all_oids = lfs_oids_for_refs(source_path, plan.refs_to_bundle)
        new_oids = all_oids - already_synced
        missing: list[str] = []
        for oid in new_oids:
            obj_path = lfs_object_path(source_path, oid)
            if obj_path is not None:
                lfs_objects[oid] = obj_path
            else:
                missing.append(oid)
        if missing:
            raise LFSObjectMissingError(
                f"Repo {plan.repo.id}: {len(missing)} LFS object(s) referenced but not "
                "present locally. Run 'git lfs fetch --all' in the source repo.\n"
                + "\n".join(f"  {oid}" for oid in missing[:10])
                + (f"\n  ... and {len(missing) - 10} more" if len(missing) > 10 else "")
            )

    return BundleResult(
        repo=plan.repo,
        bundle_path=bundle_path,
        exported_refs=exported_refs,
        warnings=plan.warnings,
        lfs_objects=lfs_objects,
    )
