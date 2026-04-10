from django.contrib import admin
from django.apps import apps

app_models = apps.get_app_config("transaction").get_models()

for model in app_models:
    
    class GenericAdmin(admin.ModelAdmin):
        list_display = [field.name for field in model._meta.fields]

    try:
        admin.site.register(model, GenericAdmin)
    except admin.sites.AlreadyRegistered:
        pass
