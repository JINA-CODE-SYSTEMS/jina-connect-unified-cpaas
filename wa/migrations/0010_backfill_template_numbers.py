"""
Data migration: backfill TemplateNumber for existing WATemplates that have number=NULL.
"""
from django.db import migrations


def backfill_template_numbers(apps, schema_editor):
    """Create a TemplateNumber for every WATemplate that lacks one."""
    WATemplate = apps.get_model('wa', 'WATemplate')
    TemplateNumber = apps.get_model('message_templates', 'TemplateNumber')

    templates_without_number = WATemplate.objects.filter(number__isnull=True)
    count = 0
    for tpl in templates_without_number:
        tn = TemplateNumber.objects.create(name=tpl.element_name or tpl.name or "")
        tpl.number = tn
        tpl.save(update_fields=['number'])
        count += 1
    if count:
        print(f"\n  Backfilled {count} WATemplate(s) with TemplateNumber records.")


def reverse_backfill(apps, schema_editor):
    """No-op reverse — we don't delete TemplateNumbers."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('wa', '0009_lto_has_expiration'),
        ('message_templates', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(backfill_template_numbers, reverse_backfill),
    ]
