"""
Root conftest for pytest.

When no Redis broker is reachable, configures Celery to run tasks
eagerly so tests don't require a broker.  In CI (where Redis IS
running), the env-var ``CELERY_BROKER_URL`` is set and honoured.
"""

import os

from django.conf import settings


def pytest_configure(config):
    """Ensure Celery tasks work even without a broker."""
    # If CI already provides a broker URL via env, respect it.
    broker = os.environ.get("CELERY_BROKER_URL", "")
    if not broker:
        # No broker available — run tasks synchronously in-process
        settings.CELERY_TASK_ALWAYS_EAGER = True
        settings.CELERY_TASK_EAGER_PROPAGATES = True
        settings.CELERY_BROKER_URL = ""


