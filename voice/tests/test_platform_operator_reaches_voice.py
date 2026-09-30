"""The voice gates mean "superuser" and read ``is_staff``, like the host panel did.

Found sweeping for the rest of jain-t/jina-connect-web#691, where three host
endpoints were gated on DRF's ``IsAdminUser`` — ``is_staff`` alone — while
every other part of the product treats ``is_superuser`` as "this caller is the
platform". The grant at ``users/viewsets/platform_admin.py`` sets
``is_superuser`` and nothing else, so an administrator created through the
product failed those gates.

The same two flags are confused here, and the docstring says so out loud:

    A user passes if either:
      * ``request.user.is_staff`` (superuser bypass), or
      ...

Naming the intent — superuser — while reading the other flag. The bypass
exists for exactly the caller it excluded: a platform operator holds no
``TenantUser`` row anywhere, so the role lookup underneath can only fail for
them, and voice provider credentials, rate cards and recordings were closed to
every administrator the product itself had created.

HOW TO RUN:
    .venv/bin/python -m pytest voice/tests/test_platform_operator_reaches_voice.py -v
"""

from __future__ import annotations

import itertools

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory

from tenants.models import Tenant, TenantRole, TenantUser
from voice.permissions import HasVoicePermission, IsVoiceAdmin

pytestmark = pytest.mark.django_db

User = get_user_model()

_seq = itertools.count(1)


def _user(*, superuser=False, staff=False):
    n = next(_seq)
    user = User.objects.create_user(
        username=f"v{n}",
        email=f"v{n}@test.invalid",
        password="x",  # noqa: S106
        mobile=f"+9190002{n:05d}",
    )
    user.is_superuser = superuser
    user.is_staff = staff
    user.save(update_fields=["is_superuser", "is_staff"])
    return user


def _member_without_voice_rights():
    """In an organisation, but holding no voice permission."""
    n = next(_seq)
    tenant = Tenant.objects.create(name=f"VoiceOrg {n}")
    user = _user()
    role, _ = TenantRole.objects.get_or_create(tenant=tenant, slug="agent", defaults={"name": "Agent", "priority": 20})
    TenantUser.objects.create(user=user, tenant=tenant, role=role, is_active=True)
    return user


class _View:
    """Stands in for a viewset that wants one voice permission."""

    action = "create"
    voice_required_permission = "voice.provider.edit"


def _request(user):
    request = APIRequestFactory().post("/voice/providers/")
    request.user = user
    return request


# ─────────────────────────────────────────────────────────────────────────────
# The operator the bypass is for
# ─────────────────────────────────────────────────────────────────────────────


def test_a_platform_operator_can_configure_voice():
    """They hold no role anywhere, so the lookup underneath can only fail."""
    assert IsVoiceAdmin().has_permission(_request(_user(superuser=True)), _View()) is True


def test_a_platform_operator_passes_the_per_action_gate_too():
    assert HasVoicePermission().has_permission(_request(_user(superuser=True)), _View()) is True


def test_a_staff_account_still_passes_both():
    """Preserved: ``createsuperuser`` sets both flags and nothing here narrows that."""
    staff = _user(staff=True)

    assert IsVoiceAdmin().has_permission(_request(staff), _View()) is True
    assert HasVoicePermission().has_permission(_request(staff), _View()) is True


# ─────────────────────────────────────────────────────────────────────────────
# And nobody else
# ─────────────────────────────────────────────────────────────────────────────


def test_a_member_without_the_permission_is_still_refused():
    member = _member_without_voice_rights()

    assert IsVoiceAdmin().has_permission(_request(member), _View()) is False
    assert HasVoicePermission().has_permission(_request(member), _View()) is False


def test_an_account_with_no_organisation_at_all_is_refused():
    assert IsVoiceAdmin().has_permission(_request(_user()), _View()) is False


def test_an_anonymous_caller_is_refused():
    from django.contrib.auth.models import AnonymousUser

    assert IsVoiceAdmin().has_permission(_request(AnonymousUser()), _View()) is False
