"""
/version/ must report what is actually running (#222).

Run with: python manage.py test jina_connect.tests.test_version

Both hosts previously reported git_commit "unknown" with an identical build
date while running different code, so the endpoint could not answer the one
question it exists for.
"""

import subprocess
from unittest import mock

from django.test import SimpleTestCase

from jina_connect import version


class VersionResolutionTestCase(SimpleTestCase):
    def setUp(self):
        version._resolve.cache_clear()
        self.addCleanup(version._resolve.cache_clear)

    def test_environment_wins(self):
        """CI or a deploy step is authoritative — it also works without a .git dir."""
        with mock.patch.dict("os.environ", {"GIT_COMMIT": "deadbee", "BUILD_DATE": "2026-01-02"}):
            info = version.get_full_version()

        self.assertEqual(info["git_commit"], "deadbee")
        self.assertEqual(info["build_date"], "2026-01-02")

    def test_falls_back_to_git(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(version, "_git", side_effect=["abc1234", "2026-03-04", ""]):
                info = version.get_full_version()

        self.assertEqual(info["git_commit"], "abc1234")
        self.assertEqual(info["build_date"], "2026-03-04")

    def test_falls_back_to_static_when_git_is_unavailable(self):
        """A source install with no repository still gets a valid response."""
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(version, "_git", return_value=None):
                info = version.get_full_version()

        self.assertEqual(info["git_commit"], version.GIT_COMMIT)
        self.assertEqual(info["build_date"], version.BUILD_DATE)
        self.assertIsNone(info["git_dirty"])

    def test_dirty_tree_is_reported(self):
        """Production has been hot-patched before — a bare hash hides that."""
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(version, "_git", side_effect=["abc1234", "2026-03-04", " M file.py"]):
                self.assertTrue(version.get_full_version()["git_dirty"])

        version._resolve.cache_clear()
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(version, "_git", side_effect=["abc1234", "2026-03-04", ""]):
                self.assertFalse(version.get_full_version()["git_dirty"])

    def test_the_result_is_cached(self):
        """The endpoint must not shell out on every request."""
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(version, "_git", return_value="abc1234") as git:
                version.get_full_version()
                version.get_full_version()
                version.get_full_version()

        self.assertEqual(git.call_count, 3)  # one resolve = 3 git calls, not 9


class GitHelperTestCase(SimpleTestCase):
    """_git must never raise — a version endpoint cannot take down a deploy."""

    def test_missing_git_binary_returns_none(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            self.assertIsNone(version._git("rev-parse", "HEAD"))

    def test_timeout_returns_none(self):
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 2)):
            self.assertIsNone(version._git("rev-parse", "HEAD"))

    def test_non_zero_exit_returns_none(self):
        completed = subprocess.CompletedProcess(args=[], returncode=128, stdout="", stderr="not a repo")
        with mock.patch("subprocess.run", return_value=completed):
            self.assertIsNone(version._git("rev-parse", "HEAD"))

    def test_empty_output_returns_none(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="\n", stderr="")
        with mock.patch("subprocess.run", return_value=completed):
            self.assertIsNone(version._git("status", "--porcelain"))
