"""RCS cron jobs."""


def reset_daily_rcs_counters():
    """Reset messages_sent_today for all active RCS apps."""
    from rcs.models import RCSApp

    RCSApp.objects.filter(is_active=True).update(messages_sent_today=0)
