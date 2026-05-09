"""Shared fixtures and helpers for sync-tools tests.

All git operations use real subprocess calls on actual temp repositories.
No mocking of git.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from collections.abc import Generator
from typing import NamedTuple

import pytest


class GitRepo(NamedTuple):
    path: pathlib.Path  # non-bare source repo
    bare_path: pathlib.Path  # bare clone (used as destination)


# ---------------------------------------------------------------------------
# Low-level git helper (not a fixture)
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: pathlib.Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=check,
    )


def git_config(repo: pathlib.Path) -> None:
    """Set local user config so commits work in isolated repos."""
    _git(["config", "user.email", "test@example.com"], repo)
    _git(["config", "user.name", "Test User"], repo)


# ---------------------------------------------------------------------------
# Repo manipulation helpers (plain functions, usable from tests directly)
# ---------------------------------------------------------------------------


def make_commit(
    repo_path: pathlib.Path,
    message: str = "a commit",
    filename: str = "change.txt",
    content: str | None = None,
) -> str:
    """Add a file and commit. Returns the new HEAD SHA."""
    f = repo_path / filename
    f.write_text(content or message, encoding="utf-8")
    _git(["add", filename], repo_path)
    _git(["commit", "-m", message], repo_path)
    return _git(["rev-parse", "HEAD"], repo_path).stdout.strip()


def force_rebase(repo_path: pathlib.Path, branch: str = "main") -> str:
    """
    Simulate a rebase by amending HEAD with a tree change, ensuring a new SHA regardless of timing.
    The old HEAD SHA will no longer be an ancestor of the new HEAD.
    Returns the new HEAD SHA.
    """
    marker = repo_path / "_rebase_marker.txt"
    marker.write_text(f"rebase marker for {repo_path}", encoding="utf-8")
    _git(["add", "_rebase_marker.txt"], repo_path)
    _git(["commit", "--amend", "--no-edit"], repo_path)
    return _git(["rev-parse", "HEAD"], repo_path).stdout.strip()


def move_tag(repo_path: pathlib.Path, tag: str, new_target_sha: str) -> None:
    """Force-move a tag to a different commit."""
    _git(["tag", "-f", tag, new_target_sha], repo_path)


def add_branch(repo_path: pathlib.Path, branch: str, from_ref: str = "HEAD") -> None:
    """Create a new branch at from_ref."""
    _git(["branch", branch, from_ref], repo_path)


def current_sha(repo_path: pathlib.Path, ref: str = "HEAD") -> str:
    return _git(["rev-parse", ref], repo_path).stdout.strip()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def git_repo(tmp_path: pathlib.Path) -> GitRepo:
    """
    Create a non-bare source repo with one initial commit, plus a bare clone.
    Returns GitRepo(path=source, bare_path=bare_dest).
    """
    src = tmp_path / "source"
    src.mkdir()
    _git(["init", "-b", "main"], src)
    git_config(src)

    make_commit(src, "initial commit", "README.md", "# sync-tools test repo")

    bare = tmp_path / "dest.git"
    _git(["clone", "--bare", str(src), str(bare)], tmp_path)

    return GitRepo(path=src, bare_path=bare)


@pytest.fixture()
def git_repo_with_tags(git_repo: GitRepo) -> GitRepo:
    """Extends git_repo with an annotated tag and a lightweight tag."""
    src = git_repo.path
    _git(["tag", "-a", "v1.0.0", "-m", "release 1.0.0"], src)
    _git(["tag", "v1.0.0-lw"], src)
    # Update bare to include tags
    _git(["push", "--tags", str(git_repo.bare_path)], src)
    return git_repo


@pytest.fixture()
def lfs_repo(tmp_path: pathlib.Path) -> GitRepo:
    """
    Create a repo with a file tracked by Git LFS.
    Skips if git-lfs is not installed.
    """
    if shutil.which("git-lfs") is None:
        pytest.skip("git-lfs not installed")

    src = tmp_path / "lfs-source"
    src.mkdir()
    _git(["init", "-b", "main"], src)
    git_config(src)
    _git(["lfs", "install", "--local"], src)
    _git(["lfs", "track", "*.bin"], src)
    _git(["add", ".gitattributes"], src)
    _git(["commit", "-m", "init lfs tracking"], src)

    # Write a fake LFS pointer file (git lfs store creates one)
    bin_file = src / "data.bin"
    bin_file.write_bytes(b"\x00" * 1024)
    _git(["add", "data.bin"], src)
    _git(["commit", "-m", "add lfs file"], src)

    bare = tmp_path / "lfs-dest.git"
    _git(["clone", "--bare", str(src), str(bare)], tmp_path)
    return GitRepo(path=src, bare_path=bare)


@pytest.fixture()
def mixed_lfs_repo(tmp_path: pathlib.Path) -> GitRepo:
    """Repo with LFS on 'main' and a clean 'no-lfs' branch without LFS objects."""
    if shutil.which("git-lfs") is None:
        pytest.skip("git-lfs not installed")

    src = tmp_path / "mixed-lfs-source"
    src.mkdir()
    _git(["init", "-b", "main"], src)
    git_config(src)
    _git(["lfs", "install", "--local"], src)
    _git(["lfs", "track", "*.bin"], src)
    _git(["add", ".gitattributes"], src)
    _git(["commit", "-m", "init lfs tracking"], src)

    # Branch no-lfs before any LFS files are added so its tree has no LFS objects
    _git(["checkout", "-b", "no-lfs"], src)
    make_commit(src, "normal file", "readme.txt", "hello")
    _git(["checkout", "main"], src)

    bin_file = src / "data.bin"
    bin_file.write_bytes(b"\x00" * 1024)
    _git(["add", "data.bin"], src)
    _git(["commit", "-m", "add lfs file"], src)

    bare = tmp_path / "mixed-lfs-dest.git"
    _git(["clone", "--bare", str(src), str(bare)], tmp_path)
    return GitRepo(path=src, bare_path=bare)


@pytest.fixture(scope="session")
def docker_available() -> None:
    """Skip test if Docker daemon is not reachable."""
    from sync_tools.docker_ops import is_docker_available

    if not is_docker_available():
        pytest.skip("Docker daemon not available")


@pytest.fixture()
def docker_image(tmp_path: pathlib.Path, docker_available: None) -> Generator[str, None, None]:
    """Build a minimal linux/amd64 test image. Yields image tag; removes on teardown."""
    import subprocess
    import uuid

    tag = f"sync-tools-test:{uuid.uuid4().hex[:8]}"
    dockerfile = tmp_path / "Dockerfile"
    # FROM scratch is not pullable/inspectable so use busybox (tiny)
    dockerfile.write_text("FROM busybox:1.36\nRUN echo hello > /greeting.txt\n", encoding="utf-8")
    subprocess.run(
        ["docker", "build", "--platform", "linux/amd64", "-t", tag, "."],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )
    yield tag
    subprocess.run(["docker", "rmi", "-f", tag], capture_output=True)


@pytest.fixture()
def docker_image_v2(tmp_path: pathlib.Path, docker_available: None, docker_image: str) -> Generator[str, None, None]:
    """Build a second image layered on docker_image. Yields tag; removes on teardown."""
    import subprocess
    import uuid

    tag = f"sync-tools-test-v2:{uuid.uuid4().hex[:8]}"
    dockerfile = tmp_path / "Dockerfile.v2"
    dockerfile.write_text(
        f"FROM {docker_image}\nRUN echo world > /greeting2.txt\n", encoding="utf-8"
    )
    subprocess.run(
        ["docker", "build", "--platform", "linux/amd64", "-t", tag, "-f", "Dockerfile.v2", "."],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )
    yield tag
    subprocess.run(["docker", "rmi", "-f", tag], capture_output=True)


@pytest.fixture()
def state_file(tmp_path: pathlib.Path, git_repo: GitRepo) -> pathlib.Path:
    """Write a minimal state JSON for git_repo with no last_sync (first-time export)."""
    import json

    state = {
        "version": "1",
        "repos": [
            {
                "id": "test/repo",
                "source_url": f"file://{git_repo.path}",
                "source_local_path": str(git_repo.path),
                "dest_path": str(git_repo.bare_path),
            }
        ],
    }
    p = tmp_path / "state.json"
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return p
