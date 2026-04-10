from django.apps import AppConfig


class BroadcastConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'broadcast'
    verbose_name = 'Broadcast Management'
    
    def ready(self):
        """
        Initialize broadcast services when Django starts
        """
        # Import signal handlers to ensure they're connected
        import broadcast.signals
        
        # Import url_tracker models so Django discovers them for migrations
        import broadcast.url_tracker.models  # noqa: F401
        
        # Import url_tracker admin for richer admin views
        import broadcast.url_tracker.admin  # noqa: F401
