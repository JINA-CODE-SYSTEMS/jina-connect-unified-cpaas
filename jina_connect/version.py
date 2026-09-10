"""Build and version information reported by ``/version/``.

The point of this endpoint is to answer "what is actually running on this
box". Static strings cannot do that: before this change both the dev and
production hosts reported ``git_commit: "unknown"`` with an identical build
date, despite running different code.

Resolution order, first hit wins:

1. ``GIT_COMMIT`` / ``BUILD_DATE`` environment variables — set these from CI
   or a deploy step. Authoritative, and the only option that works for a
   container built without a ``.git`` directory.
2. ``git`` itself, read from the source tree. Covers the common case of a
   checkout deployed with ``git pull``, which is how this platform is
   deployed today.
3. The static values below, for source installs with neither.

Resolved once and cached, so ``/version/`` does not shell out per request.
"""

import subprocess
from functools import lru_cache
from os import environ
from pathlib import Path

# Semantic version
VERSION = "1.0.0"

# Build number - increment on each deployment
# Format: YYYYMMDD.BUILD_NUMBER or just incremental number
BUILD_NUMBER = "1"

# Fallbacks, used only when neither the environment nor git can answer.
GIT_COMMIT = "unknown"
BUILD_DATE = "2026-02-06"

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str) -> str | None:
    """Run a git command in the source tree, or return None.

    Never raises. A version endpoint must not be able to take down a
    deployment because git is missing, the tree is not a repository, or the
    call hangs.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), *args],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


@lru_cache(maxsize=1)
def _resolve() -> dict:
    commit = environ.get("GIT_COMMIT") or _git("rev-parse", "--short", "HEAD") or GIT_COMMIT
    build_date = environ.get("BUILD_DATE") or _git("log", "-1", "--format=%cs") or BUILD_DATE

    # Whether the checkout differs from the commit it claims to be. This
    # matters here: production has been hot-patched in place before, which is
    # exactly the situation a bare commit hash hides.
    status = _git("status", "--porcelain")
    dirty = None if status is None else bool(status)

    return {"git_commit": commit, "build_date": build_date, "git_dirty": dirty}


def get_version_string():
    """Returns version string for display (e.g., 'v1.0.0 (Build #1)')"""
    return f"v{VERSION} (Build #{BUILD_NUMBER})"


def get_full_version():
    """Returns detailed version info"""
    resolved = _resolve()
    return {
        "version": VERSION,
        "build_number": BUILD_NUMBER,
        "git_commit": resolved["git_commit"],
        "build_date": resolved["build_date"],
        "git_dirty": resolved["git_dirty"],
    }
