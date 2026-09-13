"""No write endpoint may take its organisation from the request body (#346).

This file is the audit #346 asked for, kept as a test rather than as prose, so a
second endpoint with the same shape fails CI instead of being rediscovered as a
second P0 some months later. ``test_tenant_scoping.py`` does the same thing for
the read direction (#326); this is its write-side twin.

─────────────────────────────────────────────────────────────────────────────
The audit, as found on f2c8fb5
─────────────────────────────────────────────────────────────────────────────

**39** viewsets inherit ``BaseTenantModelViewSet``; **23** serializers across
**12** files expose a writable ``tenant``. Crossing the two — which serializer
does a routed write action actually use, and does the model have a tenant column
of its own for that value to land in — gives the endpoints that were genuinely
exploitable, as opposed to merely untidy:

*Exploitable: a member of one organisation could create or move a row in
another.*

* ``POST``/``PATCH /wa/v2/apps/`` — ``TenantWAApp``. The reported defect.
* ``POST``/``PATCH /tenants/tenant-gupshup/`` — **the same model** through a
  second viewset and a second serializer. Equally severe and not mentioned in
  the ticket; fixing only the reported endpoint would have left it open.
* ``POST``/``PATCH /tenants/tenant-tags/``, ``/tenants/tenant-media/``,
  ``/tenants/tenant-users/`` — ``fields = "__all__"`` over a tenant column. The
  last is the worst of the three: a ``TenantUser`` row *is* a membership.
* ``POST``/``PATCH`` on every broadcast viewset — ``/broadcast/``,
  ``/mobile/broadcast/``, ``/wa/...``, ``/mobile/wa/...``. Four of the seven
  broadcast viewsets already forced the tenant in ``perform_create``; the other
  three did not, over the same model and the same serializer.
* ``POST``/``PATCH /chat-flow/`` — ``ChatFlow``.
* ``PATCH`` on ``/team-inbox/`` messages — create used a serializer without
  ``tenant``, update did not.
* ``POST``/``PATCH`` on the SMS and RCS outbound message viewsets.
* ``POST``/``PATCH /razorpay/razor-pay/`` — ``RazorPayOrder``. **Outside**
  ``BaseTenantModelViewSet``: it is a direct ``BaseModelViewSet`` subclass that
  scopes its reads by hand (#255). This is why the control was put on
  ``BaseModelViewSet`` and not one class lower.

*Untidy but not exploitable, and why:*

* ``/transaction/`` — ``TenantTransactionSerializer`` has a writable ``tenant``
  and the router registers create and update, but ``http_method_names = ["get"]``
  answers both with 405. Closed by the §07 wallet fix, not by anything here.
* Eleven of the 39 viewsets serve models with **no tenant column of their own**
  (``WAMessage``, ``WASubscription``, ``WAWebhookEvent``, ``WABAInfo``,
  ``BroadcastMessage``, ``ChatFlowNode``, ``ChatFlowEdge`` …). They reach their
  organisation through a parent, so there is nothing on the row for a body to
  aim at; the control that matters for them is the one on the parent.
* ``TenantContact``, ``WAContacts`` and ``TenantUser``-via-``MemberSerializer``
  already had ``tenant`` read-only, and ``WATemplateV2Serializer``,
  ``NotificationSerializer``, ``WAOrderListSerializer``, ``TenantSerializer``,
  ``CreateRoleSerializer`` and ``WAMessageCreateSerializer`` never exposed it.
* The ``SMSApp``, ``RCSApp``, ``TelegramBotApp`` and voice viewsets are plain
  ``viewsets.ModelViewSet`` — outside both base classes — but their serializers
  do not expose ``tenant`` and their ``perform_create`` overrides force it, so
  they are covered by their own code rather than by this control. Those
  overrides are **not** made redundant by #346 and must not be removed.

HOW TO RUN:
    DB_NAME=... python -m pytest abstract/tests/test_tenant_write_scoping.py -v
"""

from __future__ import annotations

import itertools
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.urls import get_resolver
from rest_framework import viewsets as drf_viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIRequestFactory

from abstract.tenant_scoping import tenant_write_field
from abstract.viewsets.base import BaseModelViewSet

User = get_user_model()

_mobile_seq = itertools.count(1)

WRITE_METHODS = frozenset({"post", "patch", "put"})


# ─────────────────────────────────────────────────────────────────────────────
# Discovery
# ─────────────────────────────────────────────────────────────────────────────


def _routed_viewsets():
    """Every viewset class the URL conf actually routes, with its action map.

    Reading the resolver rather than ``__subclasses__`` alone is what makes the
    sweep about *endpoints*: a viewset nobody registered cannot be posted to, and
    a registered one is reachable whether or not anybody remembered it exists.
    """
    import jina_connect.urls  # noqa: F401  (imported for its side effect)

    def walk(patterns):
        for pattern in patterns:
            if hasattr(pattern, "url_patterns"):
                yield from walk(pattern.url_patterns)
                continue
            callback = getattr(pattern, "callback", None)
            cls = getattr(callback, "cls", None)
            if cls is not None:
                yield cls, getattr(callback, "actions", None) or {}

    routed = {}
    for cls, actions in walk(get_resolver().url_patterns):
        routed.setdefault(cls, set()).update(
            action for method, action in actions.items() if method.lower() in WRITE_METHODS
        )
    return routed


def _supports_writes(viewset_cls) -> bool:
    """Whether the class answers write methods at all, rather than 405-ing them.

    The router registers create and update from the mixins regardless of
    ``http_method_names``, which is how ``/transaction/`` looks writable and is
    not — see the audit above.
    """
    supported = {m.lower() for m in getattr(viewset_cls, "http_method_names", [])}
    return bool(supported & WRITE_METHODS)


def _write_endpoints_that_can_name_a_tenant(request, user):
    """Every routed write whose body could choose the row's organisation.

    Yields ``(viewset_cls, action, serializer_cls, field_name)``. Membership in
    this set is decided by the same question the control asks — does the model
    have a tenant column, and does this action's serializer expose it writably —
    so the sweep cannot drift away from what it is sweeping.
    """
    for viewset_cls, write_actions in sorted(_routed_viewsets().items(), key=lambda kv: kv[0].__name__):
        if not write_actions or not _supports_writes(viewset_cls):
            continue
        if viewset_cls.__module__.startswith("rest_framework"):
            continue
        for action in sorted(write_actions):
            view = viewset_cls()
            view.action = action
            view.format_kwarg = None
            view.request = request
            view.request.user = user
            try:
                serializer_cls = view.get_serializer_class()
                serializer = serializer_cls()
                model = getattr(getattr(serializer, "Meta", None), "model", None)
            except Exception:
                # A viewset whose serializer choice needs more of a request than
                # this harness builds. Reported by the count test below rather
                # than skipped silently.
                continue
            if model is None:
                continue
            name = tenant_write_field(model)
            if name is None:
                continue
            field = serializer.fields.get(name)
            if field is None or field.read_only:
                continue
            yield viewset_cls, action, serializer_cls, name


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _two_organisations_and_a_member_of_the_first():
    from tenants.models import Tenant, TenantRole, TenantUser

    mine = Tenant.objects.create(name=f"sweep-mine-{uuid.uuid4().hex[:6]}", is_active=True)
    theirs = Tenant.objects.create(name=f"sweep-theirs-{uuid.uuid4().hex[:6]}", is_active=True)
    user = User.objects.create_user(
        username=f"sweep_{uuid.uuid4().hex[:8]}",
        email=f"sweep_{uuid.uuid4().hex[:8]}@test.com",
        mobile=f"+9190008{next(_mobile_seq):05d}",
        password="testpass123",  # noqa: S106 — throwaway test credential
    )
    TenantUser.objects.create(tenant=mine, user=user, role=TenantRole.objects.get(tenant=mine, slug="owner"))
    return mine, theirs, user


# ─────────────────────────────────────────────────────────────────────────────
# The sweep
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_the_sweep_finds_the_endpoints_it_is_meant_to_sweep():
    """Otherwise every assertion below passes by discovering nothing.

    The audit found 23 serializers with a writable ``tenant``; the actions that
    reach them number well over that. The floor is deliberately far below the
    count found on f2c8fb5 so that removing one endpoint does not fail this
    test — the thing being guarded is "the sweep is running at all".
    """
    _mine, _theirs, user = _two_organisations_and_a_member_of_the_first()
    request = APIRequestFactory().post("/", {}, format="json")
    found = list(_write_endpoints_that_can_name_a_tenant(request, user))

    assert len(found) > 20, f"Only {len(found)} write endpoints discovered; the sweep has stopped working."
    names = {f"{vs.__module__}.{vs.__name__}" for vs, _a, _s, _f in found}
    # The reported endpoint and the second route to the same model must both be
    # in the swept set, or the sweep is not covering #346.
    assert "wa.viewsets.wa_app.WAAppViewSet" in names
    assert "tenants.viewsets.tenant_gupshup.TenantGupshupAppsViewSet" in names


@pytest.mark.django_db
def test_every_such_endpoint_refuses_an_organisation_the_caller_is_not_in():
    """The audit as a behavioural sweep, not a structural one.

    Each endpoint is asked for an input serializer naming an organisation the
    caller has no membership in, exactly as ``create`` and ``partial_update`` do,
    and must refuse. Driving the real ``get_serializer`` is what makes this catch
    an override that forgets ``super()`` — ``WATemplateV2ViewSet``'s channel
    subclasses override it, and a structural check on ``perform_create`` would
    have missed all of them anyway, since two viewsets call ``serializer.save()``
    directly and never reach it.
    """
    mine, theirs, user = _two_organisations_and_a_member_of_the_first()
    request = APIRequestFactory().post("/", {}, format="json")
    request.user = user

    endpoints = list(_write_endpoints_that_can_name_a_tenant(request, user))
    accepted = []
    for viewset_cls, action, _serializer_cls, name in endpoints:
        view = viewset_cls()
        view.action = action
        view.format_kwarg = None
        view.request = request
        try:
            view.get_serializer(data={name: theirs.id})
        except PermissionDenied:
            continue
        accepted.append(f"{viewset_cls.__module__}.{viewset_cls.__name__}.{action} (field {name!r})")

    assert accepted == [], (
        "These write endpoints accepted an organisation the caller is not a member of:\n  " + "\n  ".join(accepted)
    )
    # And the caller's own organisation is still accepted, or the sweep above
    # would pass just as well against a control that refused everything. Every
    # endpoint, not a sample: a control that refused one organisation too many on
    # a single viewset is the kind of regression a sample misses.
    over_refused = []
    for viewset_cls, action, _serializer_cls, name in endpoints:
        view = viewset_cls()
        view.action = action
        view.format_kwarg = None
        view.request = request
        try:
            view.get_serializer(data={name: mine.id})
        except PermissionDenied:
            over_refused.append(f"{viewset_cls.__module__}.{viewset_cls.__name__}.{action}")

    assert over_refused == [], "These write endpoints refused the caller's *own* organisation:\n  " + "\n  ".join(
        over_refused
    )


@pytest.mark.django_db
def test_no_write_endpoint_with_a_tenant_column_sits_outside_the_control():
    """A new viewset added outside ``BaseModelViewSet`` fails here and names itself.

    This is the half of the audit that cannot be expressed as "does the control
    work" — ``RazorPayViewSet`` was found only because it was looked for, being a
    direct ``BaseModelViewSet`` subclass rather than a tenant-scoped one. A
    viewset that inherits from neither would have no control at all, and nothing
    else in the suite would say so.
    """
    _mine, _theirs, user = _two_organisations_and_a_member_of_the_first()
    request = APIRequestFactory().post("/", {}, format="json")
    request.user = user

    outside = []
    for viewset_cls, write_actions in _routed_viewsets().items():
        if not write_actions or not _supports_writes(viewset_cls):
            continue
        if viewset_cls.__module__.startswith("rest_framework"):
            continue
        if not issubclass(viewset_cls, drf_viewsets.GenericViewSet):
            continue
        if issubclass(viewset_cls, BaseModelViewSet):
            continue
        for action in sorted(write_actions):
            view = viewset_cls()
            view.action = action
            view.format_kwarg = None
            view.request = request
            try:
                serializer = view.get_serializer_class()()
                model = getattr(getattr(serializer, "Meta", None), "model", None)
            except Exception:
                continue
            if model is None:
                continue
            name = tenant_write_field(model)
            if name is None:
                continue
            field = serializer.fields.get(name)
            if field is None or field.read_only:
                continue
            outside.append(f"{viewset_cls.__module__}.{viewset_cls.__name__}.{action} (field {name!r})")

    assert outside == [], (
        "These write endpoints expose a writable tenant but inherit no base class "
        "that scopes writes, so nothing stops a body naming another organisation. "
        "Either inherit BaseModelViewSet or force the tenant in perform_create:\n  " + "\n  ".join(outside)
    )


# ─────────────────────────────────────────────────────────────────────────────
# The helper the whole control rests on
# ─────────────────────────────────────────────────────────────────────────────


def test_tenant_write_field_finds_a_models_own_column():
    from tenants.models import TenantWAApp

    assert tenant_write_field(TenantWAApp) == "tenant"


def test_tenant_write_field_declines_a_model_that_reaches_its_tenant_through_a_parent():
    """The no-op requirement, stated directly. ``WAMessage`` is filtered for reads
    by ``wa_app__tenant``; there is no column on the row for a write to set, and
    saying so is the right answer rather than an error."""
    from wa.models import WAMessage

    assert tenant_write_field(WAMessage) is None


def test_tenant_write_field_declines_the_tenant_model_itself():
    """``Tenant`` has no foreign key to itself, so creating one is untouched — the
    onboarding endpoint that makes organisations must keep working."""
    from tenants.models import Tenant

    assert tenant_write_field(Tenant) is None


def test_tenant_write_field_never_raises_for_any_model_in_the_project():
    """Unlike ``tenant_filter_path``, which raises by design. Thirty-nine viewsets
    inherit this control on every write; an exception from a model it does not
    recognise would be an outage, not a safeguard."""
    from django.apps import apps as django_apps

    for model in django_apps.get_models():
        tenant_write_field(model)
