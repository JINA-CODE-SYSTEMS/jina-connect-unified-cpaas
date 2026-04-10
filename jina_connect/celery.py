import os

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_ready
from django.conf import settings

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "jina_connect.settings")

app = Celery("jina_connect")

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object(f"django.conf:settings", namespace="CELERY")

# Load task modules from all registered Django apps.
app.autodiscover_tasks()


app.conf.beat_schedule = {
    # ── Order payment lifecycle (BE-19) ──
    "check-stuck-payments": {
        "task": "wa.tasks.check_stuck_payments",
        "schedule": crontab(minute="*/5"),  # every 5 minutes
    },
}


@worker_ready.connect
def on_worker_ready(**kwargs):
    """
    Ensure Django signals are loaded when Celery worker starts.
    This is needed because AppConfig.ready() might not run in worker context.
    """
    # Import signals to ensure they're registered
    import team_inbox.signals  # noqa: F401
    print("📡 Celery worker: team_inbox signals loaded")


# @app.task(bind=True)
# def debug_task(self):
