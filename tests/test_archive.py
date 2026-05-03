from __future__ import annotations

import json
import pathlib
import tarfile

import pytest

from sync_tools.archive import create_archive, extract_archive
from sync_tools.bundle import BundleResult
from sync_tools.errors import BundleError, StateFileError
from sync_tools.state import RepoConfig


def _make_fake_bundle(tmp_path: pathlib.Path, name: str = "bundle.git") -> pathlib.Path:
    """Create a minimal fake bundle file (non-empty content, not a real git bundle for these tests)."""
    p = tmp_path / name
    p.write_bytes(b"# fake bundle content\x00\x01\x02")
    return p


def _make_bundle_result(
    tmp_path: pathlib.Path,
    repo_id: str = "org/repo",
) -> BundleResult:
    repo = RepoConfig(
        id=repo_id,
        source_url="https://example.com/repo.git",
        dest_path="/dest/repo.git",
    )
    bundle_path = _make_fake_bundle(tmp_path, f"{repo_id.replace('/', '__')}.git")
    return BundleResult(
        repo=repo,
        bundle_path=bundle_path,
        exported_refs={"refs/heads/main": "a" * 40, "refs/tags/v1.0": "b" * 40},
        warnings=[],
    )


class TestCreateAndExtract:
    def test_roundtrip(self, tmp_path: pathlib.Path) -> None:
        bundle_tmp = tmp_path / "bundle_src"
        bundle_tmp.mkdir()
        result = _make_bundle_result(bundle_tmp)

        archive_path = tmp_path / "out.tar.gz"
        archive_tmp = tmp_path / "archive_tmp"
        archive_tmp.mkdir()
        create_archive([result], archive_path, archive_tmp)

        assert archive_path.exists()
        assert archive_path.stat().st_size > 0

        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        manifest = extract_archive(archive_path, extract_dir)

        assert manifest.version == "1"
        assert len(manifest.entries) == 1
        entry = manifest.entries[0]
        assert entry.repo_id == "org/repo"
        assert entry.source_url == "https://example.com/repo.git"
        assert entry.dest_path == "/dest/repo.git"
        assert entry.exported_refs["refs/heads/main"] == "a" * 40
        assert entry.bundle_filename == "bundles/org__repo/bundle.git"

        # Verify the bundle file was actually extracted
        assert (extract_dir / entry.bundle_filename).exists()

    def test_multiple_repos(self, tmp_path: pathlib.Path) -> None:
        bundle_tmp = tmp_path / "bundle_src"
        bundle_tmp.mkdir()
        results = [
            _make_bundle_result(bundle_tmp, "org/repo1"),
            _make_bundle_result(bundle_tmp, "org/repo2"),
        ]

        archive_path = tmp_path / "out.tar.gz"
        archive_tmp = tmp_path / "archive_tmp"
        archive_tmp.mkdir()
        create_archive(results, archive_path, archive_tmp)

        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        manifest = extract_archive(archive_path, extract_dir)
        assert len(manifest.entries) == 2
        ids = {e.repo_id for e in manifest.entries}
        assert ids == {"org/repo1", "org/repo2"}


class TestPathTraversal:
    def test_traversal_blocked(self, tmp_path: pathlib.Path) -> None:
        # Build a tar.gz that contains a path-traversal member
        malicious_tar = tmp_path / "evil.tar.gz"
        with tarfile.open(malicious_tar, "w:gz") as tar:
            # Create a temp file to add
            victim = tmp_path / "victim.txt"
            victim.write_text("evil content", encoding="utf-8")
            # Add it with a traversal path
            info = tarfile.TarInfo(name="../outside.txt")
            info.size = victim.stat().st_size
            with victim.open("rb") as f:
                tar.addfile(info, f)

        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        with pytest.raises(BundleError, match="traversal"):
            extract_archive(malicious_tar, extract_dir)


class TestBadArchive:
    def test_corrupted_tar(self, tmp_path: pathlib.Path) -> None:
        bad = tmp_path / "bad.tar.gz"
        bad.write_bytes(b"this is not a valid tar.gz file at all")
        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        with pytest.raises(BundleError):
            extract_archive(bad, extract_dir)

    def test_missing_manifest(self, tmp_path: pathlib.Path) -> None:
        # A valid tar.gz but without manifest.json
        archive = tmp_path / "no_manifest.tar.gz"
        dummy_file = tmp_path / "dummy.txt"
        dummy_file.write_text("hello", encoding="utf-8")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(str(dummy_file), arcname="dummy.txt")

        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        with pytest.raises(StateFileError, match="manifest"):
            extract_archive(archive, extract_dir)

    def test_bad_manifest_json(self, tmp_path: pathlib.Path) -> None:
        archive = tmp_path / "bad_manifest.tar.gz"
        manifest = tmp_path / "manifest.json"
        manifest.write_text("{not json", encoding="utf-8")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(str(manifest), arcname="manifest.json")

        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        with pytest.raises(StateFileError, match="not valid JSON"):
            extract_archive(archive, extract_dir)

    def test_wrong_version_manifest(self, tmp_path: pathlib.Path) -> None:
        archive = tmp_path / "wrong_ver.tar.gz"
        manifest = tmp_path / "manifest.json"
        manifest.write_text(json.dumps({"version": "99", "entries": []}), encoding="utf-8")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(str(manifest), arcname="manifest.json")

        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        with pytest.raises(StateFileError, match="Unsupported manifest version"):
            extract_archive(archive, extract_dir)
