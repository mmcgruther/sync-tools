from __future__ import annotations

from dataclasses import dataclass, field


class SyncToolsError(Exception):
    """Base for all sync-tools errors."""


class GitCommandError(SyncToolsError):
    """A git subprocess call returned non-zero or timed out."""

    def __init__(self, cmd: list[str], returncode: int, stderr: str) -> None:
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"git command failed (exit {returncode}): {' '.join(cmd)}\n{stderr}".strip()
        )


class StateFileError(SyncToolsError):
    """State file is missing, unreadable, or malformed."""


class BundleError(SyncToolsError):
    """Bundle creation or verification failed."""


class LFSDetectedError(SyncToolsError):
    """Repository uses Git LFS; bundle would be incomplete (no LFS object data)."""


class TagMovedError(SyncToolsError):
    """A tag ref changed to a non-descendant commit (tag was force-moved)."""


class RebaseDetectedError(SyncToolsError):
    """A branch was force-pushed or rebased since last sync."""


class CaseConflictError(SyncToolsError):
    """Two or more refs differ only by case (problematic on case-insensitive filesystems)."""


class HierarchyConflictError(SyncToolsError):
    """A ref name is a path-component prefix of another ref (e.g. 'bugfix' and 'bugfix/a').
    Cannot coexist on a filesystem because one would need to be both a file and a directory."""


class MissingPrerequisiteError(SyncToolsError):
    """git bundle verify failed: destination lacks prerequisite objects."""


class MissingDestRepoError(SyncToolsError):
    """Destination repository path does not exist or is not a git repo."""


@dataclass
class RepoResult:
    """Holds per-repo outcome for parallel execution. Workers always return this, never re-raise."""

    repo_id: str
    success: bool
    error: SyncToolsError | None = None
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    bundle_result: object = None  # BundleResult when success=True and bundle was created
