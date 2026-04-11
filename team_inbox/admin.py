from django.apps import apps
from django.contrib import admin

# Register your models here.

app_models = apps.get_app_config("team_inbox").get_models()

for model in app_models:

    class GenericAdmin(admin.ModelAdmin):
        list_display = [field.name for field in model._meta.fields]

    try:
        admin.site.register(model, GenericAdmin)
    except admin.sites.AlreadyRegistered:
        pass
