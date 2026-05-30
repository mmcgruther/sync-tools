"""
Test infrastructure for pipeline_gen.

The central fixture is ``subprocess_recorder``.  It replaces ``subprocess.run``
for the duration of each test and records every call in order.  Tests use it to:

  - Assert the exact command sequence (assert_sequence)
  - Assert an ordered subset appeared (assert_contains_in_order)
  - Simulate failures for specific tools (configure)

Example::

    def test_deploy_never_runs_after_test_failure(repos_ini, subprocess_recorder):
        subprocess_recorder.configure(["pytest"], returncode=1)
        config = load_config(repos_ini)

        with pytest.raises(StageError):
            run_pipeline(config, "auth-service")

        assert not any(c.argv[0] == "helm" for c in subprocess_recorder.calls)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# SubprocessRecorder — the core test double
# ---------------------------------------------------------------------------


@dataclass
class RecordedCall:
    argv: list[str]
    returncode: int


class SubprocessRecorder:
    """
    Drop-in replacement for ``subprocess.run`` that records every invocation.

    Configure per-command exit codes before the code under test runs::

        recorder.configure(["docker"], returncode=1)   # all docker calls fail
        recorder.configure(["pytest", "tests/", "-v"], returncode=0)  # exact match

    Rules are matched by argv prefix; first match wins.  Unconfigured calls
    return 0 by default.

    Assertion helpers::

        recorder.assert_sequence(argv1, argv2, ...)        # exact ordered match
        recorder.assert_contains_in_order(argv1, argv2)    # subsequence match
    """

    def __init__(self) -> None:
        self._calls: list[RecordedCall] = []
        # Each rule: (prefix, returncode).  First matching prefix wins.
        self._rules: list[tuple[list[str], int]] = []

    # ------------------------------------------------------------------
    # Configuration API
    # ------------------------------------------------------------------

    def configure(self, argv_prefix: list[str], *, returncode: int) -> None:
        """Register a return-code rule matched by argv prefix (first match wins)."""
        self._rules.append((list(argv_prefix), returncode))

    # ------------------------------------------------------------------
    # subprocess.run replacement
    # ------------------------------------------------------------------

    def __call__(self, argv: list[str], **_kwargs) -> MagicMock:
        rc = self._resolve_returncode(list(argv))
        self._calls.append(RecordedCall(argv=list(argv), returncode=rc))
        mock_result = MagicMock()
        mock_result.returncode = rc
        return mock_result

    def _resolve_returncode(self, argv: list[str]) -> int:
        for prefix, rc in self._rules:
            if argv[: len(prefix)] == prefix:
                return rc
        return 0

    # ------------------------------------------------------------------
    # Inspection properties
    # ------------------------------------------------------------------

    @property
    def calls(self) -> list[RecordedCall]:
        """All recorded calls in invocation order."""
        return list(self._calls)

    @property
    def commands(self) -> list[list[str]]:
        """Shorthand: just the argv lists in order."""
        return [c.argv for c in self._calls]

    # ------------------------------------------------------------------
    # Assertion helpers
    # ------------------------------------------------------------------

    def assert_sequence(self, *expected: list[str]) -> None:
        """Assert calls match this exact sequence — no more, no fewer."""
        actual = self.commands
        expected_list = [list(e) for e in expected]
        if actual != expected_list:
            exp_lines = "\n".join(f"  [{i}] {e}" for i, e in enumerate(expected_list))
            act_lines = "\n".join(f"  [{i}] {a}" for i, a in enumerate(actual))
            raise AssertionError(
                f"Command sequence mismatch.\n"
                f"Expected ({len(expected_list)} calls):\n{exp_lines}\n"
                f"Actual   ({len(actual)} calls):\n{act_lines}"
            )

    def assert_contains_in_order(self, *expected: list[str]) -> None:
        """
        Assert that ``expected`` commands appear as an ordered subsequence of
        actual calls.  Extra calls between or around them are allowed.
        """
        actual = self.commands
        pos = 0
        for cmd in expected:
            cmd = list(cmd)
            start = pos
            while pos < len(actual) and actual[pos] != cmd:
                pos += 1
            if pos >= len(actual):
                act_lines = "\n".join(f"  [{i}] {a}" for i, a in enumerate(actual))
                raise AssertionError(
                    f"Command {cmd!r} not found after position {start}.\n"
                    f"All actual calls:\n{act_lines}"
                )
            pos += 1  # advance past the matched call

    def reset(self) -> None:
        """Clear recorded calls and configured rules."""
        self._calls.clear()
        self._rules.clear()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def subprocess_recorder(monkeypatch) -> SubprocessRecorder:
    """
    Patch ``subprocess.run`` with a ``SubprocessRecorder`` for this test.

    The recorder is also injected into ``pipeline_gen.runner`` directly so
    patching works regardless of import style.
    """
    recorder = SubprocessRecorder()
    import subprocess as _subprocess
    monkeypatch.setattr(_subprocess, "run", recorder)
    return recorder


@pytest.fixture
def repos_ini(tmp_path: Path) -> Path:
    """
    Write a minimal two-repo INI to a temp file and return its path.

    Repos: ``auth-service`` (docker/pytest/helm) and ``data-worker`` (docker/pytest/kubectl).
    Both have all three stages so tests can verify full pipeline ordering.
    """
    content = dedent("""\
        [DEFAULT]
        registry = registry.internal
        version = dev

        [auth-service]
        source = git@git.internal:auth-service.git
        build  = docker build -t %(registry)s/auth-service:%(version)s .
        test   = pytest tests/ -v
        deploy = helm upgrade --install auth-service ./chart --set image.tag=%(version)s

        [data-worker]
        source = git@git.internal:data-worker.git
        build  = docker build -t %(registry)s/data-worker:%(version)s .
        test   = pytest tests/ -x
        deploy = kubectl set image deployment/data-worker worker=%(registry)s/data-worker:%(version)s
    """)
    p = tmp_path / "repos.ini"
    p.write_text(content)
    return p
