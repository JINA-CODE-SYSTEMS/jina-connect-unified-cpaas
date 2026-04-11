from django.conf import settings
from pydantic import BaseModel


class PartnerToken(BaseModel):
    email: str = settings.GUPSHUP_EMAIL
    password: str = settings.GUPSHUP_PASSWORD
