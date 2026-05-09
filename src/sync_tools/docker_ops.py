from __future__ import annotations

import pathlib
import subprocess

from .errors import DockerCommandError, DockerNotInstalledError


def is_docker_available() -> bool:
    """Return True if Docker daemon is reachable."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def docker_pull(ref: str, timeout: int = 600) -> None:
    """Pull an image for linux/amd64 platform."""
    _run(["docker", "pull", "--platform", "linux/amd64", ref], timeout=timeout)


def get_image_digest(ref: str) -> str:
    """Return the image config SHA256 (stable content hash) for ref."""
    result = _run(["docker", "inspect", ref, "--format", "{{.Id}}"])
    return result.stdout.strip()


def docker_save(refs: list[str], output_path: pathlib.Path, timeout: int = 300) -> None:
    """Save one or more image refs to a tar file."""
    _run(["docker", "save", "-o", str(output_path)] + refs, timeout=timeout)


def docker_load(tar_path: pathlib.Path, timeout: int = 300) -> str:
    """Load a docker save tar. Returns the loaded image ref or ID.

    docker load output forms:
      "Loaded image: busybox:1.36"      → return "busybox:1.36"
      "Loaded image ID: sha256:abc..."  → return "sha256:abc..."
    """
    result = _run(["docker", "load", "-i", str(tar_path)], timeout=timeout)
    for line in result.stdout.splitlines():
        if line.startswith("Loaded image ID:"):
            return line[len("Loaded image ID:"):].strip()
        if line.startswith("Loaded image:"):
            return line[len("Loaded image:"):].strip()
    return result.stdout.strip()


def docker_tag(image_id: str, dest_ref: str) -> None:
    """Tag an image."""
    _run(["docker", "tag", image_id, dest_ref])


def docker_push(ref: str, timeout: int = 300) -> None:
    """Push an image ref to its registry."""
    _run(["docker", "push", ref], timeout=timeout)


def docker_rmi(ref: str) -> None:
    """Remove a local image; does not raise on failure."""
    try:
        subprocess.run(
            ["docker", "rmi", ref],
            capture_output=True,
            timeout=30,
        )
    except Exception:
        pass


def _run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise DockerNotInstalledError("docker CLI not found in PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise DockerCommandError(cmd, -1, f"timed out after {timeout}s") from exc

    if result.returncode != 0:
        raise DockerCommandError(cmd, result.returncode, result.stderr)

    return result
