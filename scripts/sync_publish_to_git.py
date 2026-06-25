#!/usr/bin/env python3
"""sync_publish_to_git.py — commit coverage "publish drift" to the fork.

The admin LLM publish flow (cortex theme_generation_service.publish_job) writes
``themes/*.md`` and ``Pilot_Reports/**`` wikilinks plus rows in the Postgres
``coverage_theme_membership`` table, but it never ``git commit``s. The git
source therefore drifts from the live DB (this is how orphan themes appear that
exist only in the working tree + DB and vanish on reset/reclone).

This host-side reconciliation utility detects that drift under ``themes/`` and
``Pilot_Reports/`` and commits it to the fork, optionally pushing.

Run it AFTER a publish API call returns, not concurrently with one.

Exit codes:
  0  clean tree, or --commit / --push succeeded
  2  drift detected in --dry-run (no changes made)  [cron-friendly]
  1  usage / git error
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# Single source of truth — do not duplicate these values inline.
# Only these subtrees are ever staged. The generated ``themes/README.md`` lives
# inside ``themes/`` and is intentionally included; the repo-root ``README.md``,
# ``network/`` and other ``scripts/`` are outside these prefixes and never touched.
TRACKED_PREFIXES: tuple[str, ...] = ("themes", "Pilot_Reports")
INTEGRATION_BRANCH = "master"  # fork branch the ETL reads
READONLY_REMOTE = "upstream"  # Timeverse upstream — must never be pushed to
DEFAULT_PUSH_REMOTE = "origin"  # the rdaoshiken fork


class SyncError(RuntimeError):
    """Recoverable error that maps to exit code 1 with a clean message."""


def _run(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        # e.g. git not installed / not on PATH — fail cleanly, no traceback.
        raise SyncError(f"failed to run '{args[0]}': {exc}") from exc
    if check and result.returncode != 0:
        message = (result.stderr or "").strip() or f"command failed: {' '.join(args)}"
        raise SyncError(message)
    return result


def _repo_root(start: Path) -> Path:
    result = _run(["git", "rev-parse", "--show-toplevel"], cwd=start)
    return Path(result.stdout.strip())


def _current_branch(root: Path) -> str:
    """Return the current branch name, or "" when on a detached HEAD."""
    result = _run(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], cwd=root, check=False)
    return result.stdout.strip()


def _drifted_paths(root: Path) -> list[str]:
    """Repo-relative paths with uncommitted changes under the tracked prefixes.

    For renames/copies only the destination path is returned (the origin token
    is consumed and excluded); callers must not assume rename origins appear.
    """
    result = _run(
        ["git", "status", "--porcelain", "-z", "--", *TRACKED_PREFIXES],
        cwd=root,
    )
    paths: list[str] = []
    # -z output: each entry is "XY <path>\0"; renames add a second "\0<orig>".
    tokens = result.stdout.split("\0")
    index = 0
    while index < len(tokens):
        entry = tokens[index]
        if not entry:
            index += 1
            continue
        status = entry[:2]
        path = entry[3:]
        paths.append(path)
        # A rename/copy carries an extra NUL-separated origin token whenever
        # EITHER status column is "R"/"C" (index-side or worktree-side); check
        # both columns, else the origin token is mis-parsed as the next entry.
        if "R" in status or "C" in status:
            index += 2
        else:
            index += 1
    return sorted(set(paths))


def _summarize(paths: list[str]) -> tuple[list[str], int]:
    """Return (affected theme names, affected Pilot_Reports file count)."""
    themes: set[str] = set()
    reports = 0
    for path in paths:
        if path.startswith("themes/") and path.endswith(".md"):
            name = Path(path).stem
            if name != "README":
                themes.add(name)
        elif path.startswith("Pilot_Reports/"):
            reports += 1
    return sorted(themes), reports


def _newest_mtime(root: Path, paths: list[str]) -> float:
    newest = 0.0
    for path in paths:
        absolute = root / path
        try:
            newest = max(newest, absolute.stat().st_mtime)
        except FileNotFoundError:
            # Deleted/renamed — no longer on disk, so it cannot be actively
            # written to; it must not count as a fresh write (else a drift that
            # includes a deletion would block the quiesce guard forever).
            continue
        except OSError:
            # Other OS errors — treat as freshly changing (conservative).
            newest = max(newest, time.time())
    return newest


def _assert_in_scope(root: Path, staged: list[str]) -> None:
    for path in staged:
        if not any(path == prefix or path.startswith(prefix + "/") for prefix in TRACKED_PREFIXES):
            raise SyncError(
                f"refusing to commit: staged path outside {TRACKED_PREFIXES}: {path}"
            )


def _commit_message(themes: list[str], reports: int) -> str:
    theme_line = ", ".join(themes) if themes else "(none)"
    return (
        "chore(sync): commit publish drift to git\n\n"
        "Reconcile git source with live coverage_theme_membership: the admin\n"
        "publish flow writes theme pages + Pilot_Reports wikilinks + DB rows but\n"
        "does not git-commit. This captures that drift so a reset/reclone does\n"
        "not lose data the ETL still reads from disk.\n\n"
        f"Themes: {theme_line}\n"
        f"Pilot_Reports touched: {reports}\n"
    )


def _do_commit(root: Path, themes: list[str], reports: int) -> None:
    _run(["git", "add", "--", *TRACKED_PREFIXES], cwd=root)
    # Scope the staged view to the tracked subtrees so any pre-existing foreign
    # staged path neither trips the scope guard nor leaks into the commit. The
    # pathspec on ``git commit`` below additionally guarantees only these paths
    # are committed.
    staged = _run(
        ["git", "diff", "--cached", "--name-only", "-z", "--", *TRACKED_PREFIXES], cwd=root
    ).stdout.split("\0")
    staged = [p for p in staged if p]
    if not staged:
        # Defensive: unreachable in practice because the drift detection and this
        # `git add` use the same pathspec and both honour .gitignore, so a path
        # seen as drift will stage. Guard kept so a future divergence fails loud.
        raise SyncError("nothing staged after git add; aborting")
    _assert_in_scope(root, staged)
    _run(
        ["git", "commit", "-m", _commit_message(themes, reports), "--", *TRACKED_PREFIXES],
        cwd=root,
    )


def _do_push(root: Path, branch: str, remote: str) -> None:
    # Best-effort guard (NOT airtight): it matches the literal "timeverse"
    # substring. Git URL rewrites ([url] insteadOf) and opaque aliases that
    # resolve to the upstream at transport time are out of scope; this only
    # blocks obvious misuse, it is not a security boundary.
    #
    # ``remote`` may be a named remote OR a bare URL (git push accepts both), so
    # guard the argument itself first — a direct Timeverse URL is not a named
    # remote and would otherwise slip past the URL check below.
    if remote == READONLY_REMOTE or "timeverse" in remote.lower():
        raise SyncError(f"refusing to push to read-only target '{remote}'")
    # Then resolve the named remote's URL (only if it IS a named remote) and
    # refuse an upstream aliased under any other name.
    url_res = _run(["git", "remote", "get-url", remote], cwd=root, check=False)
    if url_res.returncode == 0 and "timeverse" in url_res.stdout.strip().lower():
        raise SyncError(
            f"refusing to push: remote '{remote}' -> {url_res.stdout.strip()} "
            "is the read-only upstream."
        )
    _run(["git", "push", remote, branch], cwd=root)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Commit coverage publish drift (themes/ + Pilot_Reports/) to the fork.",
    )
    parser.add_argument(
        "--commit", action="store_true",
        help="stage and commit the drift (default is dry-run).",
    )
    parser.add_argument(
        "--push", action="store_true",
        help="after committing, push the current branch to the fork remote.",
    )
    parser.add_argument(
        "--remote", default=DEFAULT_PUSH_REMOTE,
        help=f"remote to push to (default: {DEFAULT_PUSH_REMOTE}; '{READONLY_REMOTE}' is rejected).",
    )
    parser.add_argument(
        "--min-quiesce-seconds", type=int, default=30,
        help="abort --commit if any tracked file changed within this many seconds "
             "(guards against committing a half-written publish; default: 30).",
    )
    parser.add_argument(
        "--no-quiesce-check", action="store_true",
        help="skip the quiesce guard (only when you are sure no publish is in flight).",
    )
    parser.add_argument(
        "--allow-nonmaster", action="store_true",
        help=f"allow --commit/--push on a branch other than '{INTEGRATION_BRANCH}'.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    try:
        if args.push and not args.commit:
            raise SyncError("--push requires --commit (try: --commit --push).")
        root = _repo_root(Path(os.getcwd()))
        paths = _drifted_paths(root)
        themes, reports = _summarize(paths)

        if not paths:
            print("nothing to sync: themes/ and Pilot_Reports/ are clean.")
            return 0

        print(f"drift detected: {len(themes)} theme(s), {reports} Pilot_Reports file(s).")
        if themes:
            print("  themes: " + ", ".join(themes))

        if not args.commit:
            print("dry-run: no changes made (pass --commit to apply).")
            return 2

        branch = _current_branch(root)
        if not branch:
            raise SyncError("detached HEAD: checkout a branch before --commit.")
        if branch != INTEGRATION_BRANCH and not args.allow_nonmaster:
            raise SyncError(
                f"on branch '{branch}', not '{INTEGRATION_BRANCH}'. The ETL reads "
                f"'{INTEGRATION_BRANCH}'; pass --allow-nonmaster to commit here anyway."
            )

        if not args.no_quiesce_check:
            quiet_for = time.time() - _newest_mtime(root, paths)
            if quiet_for < args.min_quiesce_seconds:
                raise SyncError(
                    f"a tracked file changed {quiet_for:.0f}s ago "
                    f"(< {args.min_quiesce_seconds}s): a publish may still be writing. "
                    "Wait and retry, or pass --no-quiesce-check."
                )

        _do_commit(root, themes, reports)
        print(f"committed drift on '{branch}'.")

        if args.push:
            _do_push(root, branch, args.remote)
            print(f"pushed '{branch}' to '{args.remote}'.")
        return 0
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
