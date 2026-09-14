"""What this deployment calls itself, for code that is not a serializer (#252).

``BrandingSettings.effective_product_name`` has been the answer since #226, and
the API and the web app both use it. Everything that sends an *email* spelled it
out instead — twenty-one occurrences of a literal across five services — so a
white-label deployment greeted its customers' staff by our product's name. On a
partner deployment that is not a cosmetic slip: the recipient has no relationship
with us, and a verification mail from a company they have never heard of reads as
phishing.

Kept deliberately small. It answers one question, from the one place that already
knows, so a template does not have to reach for the model itself and a second
answer cannot appear.
"""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def product_name() -> str:
    """The deployment's product name, or the configured default.

    Falls back rather than raising, and the reason is the caller: this is used
    while composing a verification or password-reset email, and a branding table
    that cannot be read is not a good reason to fail somebody's sign-up. The
    default is what the deployment already configured through
    ``DEFAULT_PRODUCT_NAME``, so the fallback is a worse *name*, never a wrong
    one.

    The failure is logged, because a deployment quietly sending mail under the
    default name is exactly the state this function exists to end.
    """
    default = getattr(settings, "DEFAULT_PRODUCT_NAME", "JinaConnect")

    try:
        from tenants.models import BrandingSettings

        return BrandingSettings.get_instance().effective_product_name or default
    except Exception as exc:  # noqa: BLE001 — see the docstring: mail must still go
        logger.warning("[branding] could not read the product name (%s) — using %r", exc, default)
        return default
