from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

from .errors import PyPICommandError, PyPINotInstalledError


def is_pip_available() -> bool:
    """Return True if pip is callable in the current Python environment."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def pip_download(
    packages: list[str],
    dest_dir: pathlib.Path,
    index_url: str,
    python_version: str | None = None,
    platform: str | None = None,
    include_deps: bool = False,
    extra_args: list[str] | None = None,
    timeout: int = 300,
) -> None:
    """Download packages into dest_dir via pip download.

    Each package spec is downloaded in a separate pip invocation so that
    pip's resolver does not treat multiple versions of the same package as
    a conflict (e.g. ``numpy==1.26.0`` and ``numpy==1.26.1``).
    """
    base_cmd = [sys.executable, "-m", "pip", "download", "--dest", str(dest_dir)]
    base_cmd += ["--index-url", index_url]

    if python_version:
        base_cmd += ["--python-version", python_version]
    if platform:
        # Cross-platform downloads require --only-binary; source builds don't cross-compile
        base_cmd += ["--platform", platform, "--only-binary", ":all:"]
    if not include_deps:
        base_cmd += ["--no-deps"]
    if extra_args:
        base_cmd += extra_args

    for pkg in packages:
        _run(base_cmd + [pkg], timeout=timeout)


def compute_file_sha256(path: pathlib.Path) -> str:
    """Return the SHA256 hex digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise PyPINotInstalledError("pip not found in PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise PyPICommandError(cmd, -1, f"timed out after {timeout}s") from exc

    if result.returncode != 0:
        raise PyPICommandError(cmd, result.returncode, result.stderr)

    return result
