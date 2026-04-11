import uuid

from django.db import models

from abstract.models import BaseModel, BaseTenantModelForFilterUser
from tenants.models import TenantTags


class BaseTemplateMessages(BaseTenantModelForFilterUser):
    tag = models.ManyToManyField(TenantTags, related_name="template_tags")
    content = models.TextField(blank=True, null=True, help_text="Template content")

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.name}"


class TemplateNumber(BaseModel):
    number = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    def __str__(self):
        return f"{self.number} ({'Active' if self.is_active else 'Inactive'})"
