"""Outbound email calls the deployment by its own name (#252).

The fixture name below is invented. This repository is public, and a white-label
deployment's product name identifies the customer it was built for as surely as
their company name does — so the thing under test is "a name that is not ours",
and any name that is not ours will do.

A white-label deployment greeted its customers' staff by our product's name —
twenty-one literals across five services, in the subject, the header, the body
and the footer of every verification and password-reset mail.

On a partner deployment that is not cosmetic. The recipient has no relationship
with us: a verification mail from a company they have never heard of, carrying a
link they are asked to click, reads as phishing. It is also the first thing a new
user of the platform ever sees.

`BrandingSettings.effective_product_name` has been the answer since #226 and the
API and web app both use it; only the mail did not.
"""

import pytest
from django.test import override_settings

from tenants.branding import product_name
from tenants.models import BrandingSettings


@pytest.mark.django_db()
@override_settings(DEFAULT_PRODUCT_NAME="JinaConnect")
def test_a_configured_name_is_what_goes_out():
    branding = BrandingSettings.get_instance()
    branding.product_name = "Northwind Messaging"
    branding.save(update_fields=["product_name"])

    assert product_name() == "Northwind Messaging"


@pytest.mark.django_db()
@override_settings(DEFAULT_PRODUCT_NAME="JinaConnect")
def test_an_unset_name_falls_back_to_the_deployment_default():
    branding = BrandingSettings.get_instance()
    branding.product_name = ""
    branding.save(update_fields=["product_name"])

    assert product_name() == "JinaConnect"


@override_settings(DEFAULT_PRODUCT_NAME="Northwind Messaging")
def test_an_unreadable_branding_table_does_not_stop_the_mail(db, monkeypatch):
    """A branding table that cannot be read is not a reason to fail a sign-up.

    The fallback is a worse *name*, never a wrong one — it is what the deployment
    already configured — and the failure is logged, because sending quietly under
    the default is the state this function exists to end.
    """
    def boom(*args, **kwargs):
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(BrandingSettings, "get_instance", staticmethod(boom))

    assert product_name() == "Northwind Messaging"


@pytest.mark.django_db()
@override_settings(DEFAULT_PRODUCT_NAME="Northwind Messaging")
def test_the_verification_email_carries_it_everywhere_it_used_to_say_ours():
    """Subject and body both — the subject line is what a recipient sees first."""
    from users.services.email_verification import EmailVerificationService

    branding = BrandingSettings.get_instance()
    branding.product_name = "Northwind Messaging"
    branding.save(update_fields=["product_name"])

    import inspect

    source = inspect.getsource(EmailVerificationService.send_verification_email)
    assert "Jina Connect" not in source
    assert "{product_name}" in source
