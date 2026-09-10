"""
Runtime state must not be written into the code tree (#225).

Run with: python manage.py test jina_connect.tests.test_settings_paths

django-silk defaults SILKY_PYTHON_PROFILER_RESULT_PATH to MEDIA_ROOT. With
SILKY_PYTHON_PROFILER_BINARY on, that wrote a .prof file per request into the
uploads directory — served publicly, since nginx aliases /media/ — and
SILKY_MAX_RECORDED_REQUESTS purges silk's database rows but never the files.
One box reached 65,613 files and 7.2 GB before anyone looked.

Silk is configured only when DEBUG is on, and Django's test runner forces
DEBUG off, so asserting against the imported settings would silently skip
every meaningful check. These load the real settings module in a subprocess
with DEBUG=True instead, which is the configuration a developer actually runs.
"""

import json
import os
import subprocess
import sys

from django.conf import settings
from django.test import SimpleTestCase

_PROBE = """
import json, os
from django.conf import settings
print(json.dumps({
    "debug": settings.DEBUG,
    "silk_path": getattr(settings, "SILKY_PYTHON_PROFILER_RESULT_PATH", None),
    "media_root": str(settings.MEDIA_ROOT),
    "base_dir": str(settings.BASE_DIR),
    "silk_installed": "silk" in settings.INSTALLED_APPS,
}))
"""


def _settings_with_debug_on():
    env = {**os.environ, "DEBUG": "True", "DJANGO_SETTINGS_MODULE": "jina_connect.settings"}
    result = subprocess.run(
        [sys.executable, "-c", f"import django; django.setup()\n{_PROBE}"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(settings.BASE_DIR),
        timeout=90,
    )
    if result.returncode != 0:
        raise AssertionError(f"probe failed: {result.stderr[-800:]}")
    return json.loads(result.stdout.strip().splitlines()[-1])


class SilkProfilerPathTestCase(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cfg = _settings_with_debug_on()

    def test_the_probe_really_ran_with_silk_enabled(self):
        """Guards the rest: without this the assertions below prove nothing."""
        self.assertTrue(self.cfg["debug"])
        self.assertTrue(self.cfg["silk_installed"])

    def test_the_profiler_path_is_set(self):
        """Unset means django-silk falls back to MEDIA_ROOT, which is the bug."""
        self.assertTrue(self.cfg["silk_path"])

    def test_profiles_do_not_go_to_the_uploads_directory(self):
        self.assertNotEqual(
            os.path.realpath(self.cfg["silk_path"]),
            os.path.realpath(self.cfg["media_root"]),
        )

    def test_profiles_do_not_go_inside_the_code_tree(self):
        """A deploy-time `git clean -xfd` must not be able to reach these."""
        path = os.path.realpath(self.cfg["silk_path"])
        self.assertFalse(path.startswith(os.path.realpath(self.cfg["base_dir"]) + os.sep))

    def test_the_profiler_directory_exists(self):
        """CI has no pre-existing directory, and silk does not create one."""
        self.assertTrue(os.path.isdir(self.cfg["silk_path"]))
