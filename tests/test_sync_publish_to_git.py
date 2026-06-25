"""Tests for scripts/sync_publish_to_git.py.

The coverage repo has no pre-existing pytest harness; these are self-contained
and build real temporary git repos (the script shells out to git, so mocking
git would prove nothing). Run with: python3 -m pytest tests/ -q
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import time
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sync_publish_to_git.py"
_spec = importlib.util.spec_from_file_location("sync_publish_to_git", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(repo), check=True, text=True, capture_output=True
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "cov"
    r.mkdir()
    _git(r, "init", "-q", "-b", "master")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "tester")
    (r / "themes").mkdir()
    (r / "Pilot_Reports" / "半導體").mkdir(parents=True)  # CJK path on purpose
    (r / "themes" / "AI.md").write_text("# AI\n", encoding="utf-8")
    (r / "README.md").write_text("root readme\n", encoding="utf-8")
    (r / "Pilot_Reports" / "半導體" / "2330_台積電.md").write_text("[[AI]]\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "init")
    return r


# Must exceed the script's default --min-quiesce-seconds so an aged file passes
# the quiesce guard; derived from the script's own default rather than hardcoded.
QUIESCE_MARGIN_SECONDS = mod._parse_args([]).min_quiesce_seconds * 4


def _age(path: Path, seconds: int = QUIESCE_MARGIN_SECONDS) -> None:
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_clean_tree_exits_0(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(repo)
    assert mod.main([]) == 0


def test_drift_dry_run_exits_2(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    (repo / "themes" / "NEW.md").write_text("# NEW\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    assert mod.main([]) == 2
    assert "NEW" in capsys.readouterr().out


def test_rename_parsing_not_corrupted(repo: Path) -> None:
    # Worktree-side rename of a committed theme + an untracked CJK report.
    _git(repo, "mv", "themes/AI.md", "themes/AI2.md")
    (repo / "Pilot_Reports" / "半導體" / "2454_聯發科.md").write_text("x\n", encoding="utf-8")
    paths = mod._drifted_paths(repo)
    assert "themes/AI2.md" in paths
    assert "Pilot_Reports/半導體/2454_聯發科.md" in paths
    # No entry should be corrupted into an out-of-scope/origin path.
    assert all(p.startswith(("themes/", "Pilot_Reports/")) for p in paths)


def test_quiesce_guard_blocks_fresh_writes(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (repo / "themes" / "NEW.md").write_text("# NEW\n", encoding="utf-8")  # fresh mtime
    monkeypatch.chdir(repo)
    assert mod.main(["--commit", "--allow-nonmaster"]) == 1


def test_commit_succeeds_and_excludes_root_readme(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    new = repo / "themes" / "NEW.md"
    new.write_text("# NEW\n", encoding="utf-8")
    _age(new)  # let quiesce pass
    (repo / "README.md").write_text("root readme DIRTY\n", encoding="utf-8")  # out of scope
    monkeypatch.chdir(repo)
    assert mod.main(["--commit", "--allow-nonmaster"]) == 0
    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert "themes/NEW.md" in committed
    assert "README.md" not in committed  # scope guard held
    assert _git(repo, "status", "--porcelain", "--", "README.md").stdout  # still dirty, untouched


def test_branch_guard_blocks_nonmaster(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _git(repo, "checkout", "-q", "-b", "side")
    (repo / "themes" / "NEW.md").write_text("x\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    assert mod.main(["--commit", "--no-quiesce-check"]) == 1


def test_detached_head_blocks(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "-q", head)
    (repo / "themes" / "NEW.md").write_text("x\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    assert mod.main(["--commit", "--allow-nonmaster", "--no-quiesce-check"]) == 1


def test_push_requires_commit(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(repo)
    assert mod.main(["--push"]) == 1


def test_push_to_upstream_name_rejected(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare = tmp_path / "up.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    _git(repo, "remote", "add", "upstream", str(bare))
    new = repo / "themes" / "NEW.md"
    new.write_text("x\n", encoding="utf-8")
    _age(new)
    monkeypatch.chdir(repo)
    # Commits, then refuses to push to a remote named 'upstream'.
    assert mod.main(["--commit", "--push", "--allow-nonmaster", "--remote", "upstream"]) == 1
    # The commit must have landed before the push was blocked.
    assert "themes/NEW.md" in _git(repo, "show", "--name-only", "--format=", "HEAD").stdout


def test_push_to_timeverse_url_rejected(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Remote NOT named 'upstream' but whose URL points at Timeverse: only the
    # URL guard (not the name guard) can catch this.
    bare = tmp_path / "timeverse-mirror.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    _git(repo, "remote", "add", "myfork", str(bare))
    new = repo / "themes" / "NEW.md"
    new.write_text("x\n", encoding="utf-8")
    _age(new)
    monkeypatch.chdir(repo)
    assert mod.main(["--commit", "--push", "--allow-nonmaster", "--remote", "myfork"]) == 1
