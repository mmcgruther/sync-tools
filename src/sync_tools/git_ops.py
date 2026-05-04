from __future__ import annotations

import pathlib
import shutil
import subprocess
import time
from collections import defaultdict

from .errors import BundleError, GitCommandError, MissingPrerequisiteError


def run_git(
    args: list[str],
    cwd: pathlib.Path,
    timeout: int = 300,
    capture_output: bool = True,
) -> subprocess.CompletedProcess:
    """Run git with args in cwd. Raises GitCommandError on nonzero exit or timeout."""
    cmd = ["git"] + args
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=capture_output,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise GitCommandError(cmd, -1, f"Command timed out after {timeout}s")
    except FileNotFoundError:
        raise GitCommandError(cmd, -1, "git executable not found in PATH")

    if result.returncode != 0:
        raise GitCommandError(cmd, result.returncode, result.stderr or "")
    return result


def list_refs(repo_path: pathlib.Path) -> dict[str, str]:
    """Return all refs as {refname: full-SHA}."""
    result = run_git(
        ["for-each-ref", "--format=%(refname) %(objectname)"],
        cwd=repo_path,
    )
    refs: dict[str, str] = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(" ", 1)
        if len(parts) == 2:
            refs[parts[0]] = parts[1]
    return refs


def resolve_ref(repo_path: pathlib.Path, ref: str) -> str:
    """Return the full SHA for a ref. Raises GitCommandError if ref does not exist."""
    result = run_git(["rev-parse", ref], cwd=repo_path)
    return result.stdout.strip()


def is_ancestor(
    repo_path: pathlib.Path,
    ancestor_sha: str,
    descendant_sha: str,
) -> bool:
    """
    Return True if ancestor_sha is a reachable ancestor of descendant_sha.
    Uses git merge-base --is-ancestor: exit 0 = is ancestor, exit 1 = not ancestor.
    Raises GitCommandError on any other exit code.
    """
    cmd = ["merge-base", "--is-ancestor", ancestor_sha, descendant_sha]
    full_cmd = ["git"] + cmd
    try:
        result = subprocess.run(
            full_cmd,
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        raise GitCommandError(full_cmd, -1, "Command timed out after 60s")

    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise GitCommandError(full_cmd, result.returncode, result.stderr or "")


def has_lfs_objects(repo_path: pathlib.Path) -> bool:
    """
    Return True if the repo has any LFS-tracked objects.
    Returns False if git-lfs is not installed (treated as no LFS).
    Never raises.
    """
    if shutil.which("git-lfs") is None:
        return False
    try:
        result = subprocess.run(
            ["git", "lfs", "ls-files", "--all"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=60,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def refs_with_lfs(repo_path: pathlib.Path, refs: list[str]) -> set[str]:
    """Return the subset of refs whose tree contains LFS-tracked files.
    Returns empty set if git-lfs is not installed. Never raises."""
    if shutil.which("git-lfs") is None:
        return set()
    result: set[str] = set()
    for ref in refs:
        try:
            proc = subprocess.run(
                ["git", "lfs", "ls-files", ref],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.stdout.strip():
                result.add(ref)
        except Exception:
            pass
    return result


def lfs_oids_for_refs(repo_path: pathlib.Path, refs: list[str]) -> set[str]:
    """Return all LFS OIDs (64-char SHA-256 hex) referenced by the given refs.
    Returns empty set if git-lfs not installed. Never raises."""
    if shutil.which("git-lfs") is None:
        return set()
    oids: set[str] = set()
    for ref in refs:
        try:
            proc = subprocess.run(
                ["git", "lfs", "ls-files", "--long", ref],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                timeout=60,
            )
            for line in proc.stdout.splitlines():
                parts = line.strip().split(" ", 1)
                if parts and len(parts[0]) == 64:
                    oids.add(parts[0])
        except Exception:
            pass
    return oids


def lfs_object_path(repo_path: pathlib.Path, oid: str) -> pathlib.Path | None:
    """Return the local path to an LFS object file, or None if not present on disk."""
    for prefix in (repo_path / "lfs" / "objects", repo_path / ".git" / "lfs" / "objects"):
        p = prefix / oid[:2] / oid[2:4] / oid
        if p.exists():
            return p
    return None


def find_case_conflicts(refs: dict[str, str]) -> list[tuple[str, str]]:
    """
    Pure function. Return pairs of refnames that differ only by case.
    Problematic on Windows case-insensitive filesystems.
    """
    groups: dict[str, list[str]] = defaultdict(list)
    for ref in refs:
        groups[ref.lower()].append(ref)

    conflicts: list[tuple[str, str]] = []
    for members in groups.values():
        if len(members) >= 2:
            # Emit all pairs in the group
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    conflicts.append((members[i], members[j]))
    return conflicts


def find_hierarchy_conflicts(refs: dict[str, str]) -> list[tuple[str, str]]:
    """Pure function. Return pairs (ref_a, ref_b) where ref_a is a path-component prefix of ref_b.
    e.g. ('refs/heads/bugfix', 'refs/heads/bugfix/a') — bugfix cannot be both a file and a dir."""
    names = sorted(refs.keys())
    conflicts: list[tuple[str, str]] = []
    for i, ref_a in enumerate(names):
        prefix = ref_a + "/"
        for ref_b in names[i + 1 :]:
            if ref_b.startswith(prefix):
                conflicts.append((ref_a, ref_b))
    return conflicts


def create_bundle(
    repo_path: pathlib.Path,
    bundle_path: pathlib.Path,
    refs: list[str],
    since_shas: dict[str, str],
    is_full: bool,
) -> None:
    """
    Create a git bundle at bundle_path.

    If is_full is True: git bundle create <bundle_path> --all
    Otherwise, build ranges: "<sha>..<ref>" for refs that have a since_sha,
    or bare "<ref>" for new refs with no since_sha.
    Raises BundleError if the bundle file is 0 bytes after creation.
    """
    bundle_path.parent.mkdir(parents=True, exist_ok=True)

    if is_full:
        args = ["bundle", "create", str(bundle_path), "--all"]
    else:
        ranges: list[str] = []
        for ref in refs:
            if ref in since_shas:
                ranges.append(f"{since_shas[ref]}..{ref}")
            else:
                ranges.append(ref)
        args = ["bundle", "create", str(bundle_path)] + ranges

    run_git(args, cwd=repo_path)

    if not bundle_path.exists() or bundle_path.stat().st_size == 0:
        raise BundleError(f"Bundle file is empty or missing after creation: {bundle_path}")


def verify_bundle(repo_path: pathlib.Path, bundle_path: pathlib.Path) -> None:
    """
    Verify a git bundle's prerequisites are satisfied by repo_path.
    Raises MissingPrerequisiteError if not. Raises BundleError on other failures.
    """
    try:
        run_git(["bundle", "verify", str(bundle_path)], cwd=repo_path)
    except GitCommandError as exc:
        stderr_lower = exc.stderr.lower()
        if "prerequisite" in stderr_lower or "missing" in stderr_lower:
            raise MissingPrerequisiteError(
                f"Bundle prerequisites not satisfied in {repo_path}: {exc.stderr}"
            ) from exc
        raise BundleError(f"Bundle verification failed: {exc.stderr}") from exc


def fetch_bundle(
    repo_path: pathlib.Path,
    bundle_path: pathlib.Path,
    refspecs: list[str],
    timeout: int = 300,
    max_retries: int = 3,
    retry_delay: float = 2.0,
) -> None:
    """
    Apply a git bundle to repo_path via git fetch.
    Retries on transient errors with exponential backoff.
    """
    args = ["fetch", str(bundle_path)] + refspecs
    last_exc: GitCommandError | None = None

    for attempt in range(max_retries):
        try:
            run_git(args, cwd=repo_path, timeout=timeout)
            return
        except GitCommandError as exc:
            last_exc = exc
            stderr_lower = exc.stderr.lower()
            is_transient = any(
                kw in stderr_lower
                for kw in ("timeout", "connection", "unable to connect", "network")
            )
            if not is_transient or attempt == max_retries - 1:
                raise
            time.sleep(retry_delay * (2**attempt))

    # Should not reach here, but satisfy type checker
    if last_exc:
        raise last_exc


def is_git_repo(path: pathlib.Path) -> bool:
    """Return True if path is a git repo (bare or non-bare)."""
    if not path.exists():
        return False
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0
    except Exception:
        return False


def init_bare_repo(path: pathlib.Path) -> None:
    """Initialize a bare git repository at path."""
    path.mkdir(parents=True, exist_ok=True)
    run_git(["init", "--bare", str(path)], cwd=path.parent)


def clone_to_temp(url: str, tmp_dir: pathlib.Path) -> pathlib.Path:
    """
    Mirror-clone a remote URL into tmp_dir/repo.git.
    Returns the path to the cloned bare repo.
    Raises GitCommandError on failure.
    """
    dest = tmp_dir / "repo.git"
    dest.mkdir(parents=True, exist_ok=True)
    run_git(["clone", "--mirror", url, str(dest)], cwd=tmp_dir, timeout=600)
    return dest
