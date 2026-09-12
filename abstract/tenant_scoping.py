"""Resolving "how do I filter this model down to one organisation?" (#326).

The project already has a per-model declaration for tenant filtering,
``filter_by_user_tenant_fk``, but it does not answer this question. It is a
path ending in ``tenant_users__user`` and it filters by **membership**: "rows
belonging to an organisation *this user is a member of*". That is the right
question for an ordinary request and the wrong one for an impersonated
session, where the actor is a member of nothing in the organisation being
viewed — the tenant is named by the token, not discovered from the user.

So this module answers the tenant question instead, and answers it from the
declaration the project already has wherever it can:

``filter_by_user_tenant_fk = "wa_app__tenant__tenant_users__user"``
    → tenant path ``"wa_app__tenant"``

``filter_by_user_tenant_fk = "tenant_users__user"`` (the ``Tenant`` model)
    → tenant path ``"pk"`` — the row *is* the organisation

Why derive rather than declare a second string on all ~45 models: the two
strings would be the same path written twice, and the copy that is not used on
every request is the copy that silently rots. Deriving keeps one source of
truth, so a model whose membership path changes cannot end up with a stale
tenant path.

Why it is still safe: derivation is not a fallback. A model whose declaration
does not end in the membership suffix raises ``ImproperlyConfigured`` — it does
not quietly return "no filter". A model that genuinely does not follow the
convention says so by declaring ``filter_by_tenant_fk`` explicitly (see
``wa.models.WASubscription``). Either way a tenant-scoped viewset whose model
has no resolvable tenant path fails loudly rather than serving every
organisation's rows under a banner naming one, which is the bug #326 is about.
``abstract/tests/test_tenant_scoping.py`` turns that loudness into a CI
failure rather than a production 500, by resolving and compiling the path for
every tenant-scoped viewset in the project.
"""

from django.core.exceptions import ImproperlyConfigured

# The tail every ``filter_by_user_tenant_fk`` uses to cross from a tenant to
# its members. Everything before it is the path to the tenant itself.
MEMBERSHIP_SUFFIX = "tenant_users__user"

# Opt-out: a model that cannot be derived from declares its tenant path here.
TENANT_PATH_ATTR = "filter_by_tenant_fk"

# What ``Tenant`` itself gets: the row is the organisation, so the "path to the
# tenant" is the row's own primary key.
SELF_PATH = "pk"


def tenant_filter_path(model) -> str:
    """The ORM path from ``model`` to its owning ``Tenant``, as a filter key.

    Usable directly as ``model.objects.filter(**{path: tenant_id})``.

    Raises ``ImproperlyConfigured`` when neither an explicit
    ``filter_by_tenant_fk`` nor a derivable ``filter_by_user_tenant_fk`` says
    how this model relates to a tenant. Raising is the point: the alternative
    is an unfiltered queryset, which is the defect this exists to close.
    """
    explicit = getattr(model, TENANT_PATH_ATTR, None)
    if explicit:
        return explicit

    membership = getattr(model, "filter_by_user_tenant_fk", None) or ""

    if membership == MEMBERSHIP_SUFFIX:
        return SELF_PATH

    tail = f"__{MEMBERSHIP_SUFFIX}"
    if membership.endswith(tail):
        return membership[: -len(tail)]

    raise ImproperlyConfigured(
        f"{model.__module__}.{model.__name__} has no tenant path, so a request "
        f"scoped to one organisation cannot be filtered to it. Its "
        f"filter_by_user_tenant_fk is {membership or 'unset'!r}, which does not "
        f"end in {MEMBERSHIP_SUFFIX!r} and so says nothing about which tenant a "
        f"row belongs to. Declare {TENANT_PATH_ATTR} on the model — the ORM path "
        f"from it to Tenant, e.g. 'wa_app__tenant', or 'pk' if the model is the "
        f"Tenant."
    )
