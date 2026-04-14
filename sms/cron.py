"""SMS cron jobs."""


def reset_daily_sms_counters():
    """Reset messages_sent_today for all active SMS apps."""
    from sms.models import SMSApp

    SMSApp.objects.filter(is_active=True).update(messages_sent_today=0)
