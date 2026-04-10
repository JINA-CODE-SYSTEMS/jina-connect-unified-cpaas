from django.apps import apps
# Register your models here.
# Register your models here.
from django.contrib import admin

app_models = apps.get_app_config("message_templates").get_models()

for model in app_models:
    
    class GenericAdmin(admin.ModelAdmin):
        list_display = [field.name for field in model._meta.fields]

    try:
        admin.site.register(model, GenericAdmin)
    except admin.sites.AlreadyRegistered:
        pass