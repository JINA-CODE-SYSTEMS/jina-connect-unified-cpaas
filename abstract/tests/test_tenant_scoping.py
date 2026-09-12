"""Every tenant-scoped viewset can name the tenant it is scoped to (#326).

``tenant_filter_path`` raising is what stops an impersonated session falling
back to every organisation's rows. That is the right failure, but a 500 in
support is a poor place to discover it, so this module resolves the path for
every ``BaseTenantModelViewSet`` in the project and compiles a filter with it.
A model added without a resolvable tenant path fails here instead.

The compile step matters as much as the resolve step: a path can be present and
still be wrong (a renamed relation, a typo), and Django only notices when the
query is built.

Run:
    DB_NAME=... python3 -m pytest abstract/tests/test_tenant_scoping.py
"""

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from abstract.tenant_scoping import SELF_PATH, tenant_filter_path
from abstract.viewsets.base import BaseTenantModelViewSet


def _all_subclasses(cls):
    for sub in cls.__subclasses__():
        yield sub
        yield from _all_subclasses(sub)


def _tenant_scoped_viewsets():
    """Every concrete tenant-scoped viewset, with the model it serves.

    Importing the URL conf is what guarantees every viewset module has been
    imported — ``__subclasses__`` only knows about classes Python has executed.
    """
    import jina_connect.urls  # noqa: F401  (imported for its side effect)

    for viewset in _all_subclasses(BaseTenantModelViewSet):
        queryset = getattr(viewset, "queryset", None)
        if queryset is None:
            # Builds its queryset inside get_queryset; covered by the
            # impersonation scoping tests rather than from here.
            continue
        yield viewset, queryset


class TenantFilterPathDerivationTests(SimpleTestCase):
    """The rules, on stand-in classes rather than real models."""

    def test_membership_path_yields_the_tenant_prefix(self):
        class Model:
            filter_by_user_tenant_fk = "wa_app__tenant__tenant_users__user"

        self.assertEqual(tenant_filter_path(Model), "wa_app__tenant")

    def test_the_tenant_model_itself_resolves_to_its_own_key(self):
        class Model:
            filter_by_user_tenant_fk = "tenant_users__user"

        self.assertEqual(tenant_filter_path(Model), SELF_PATH)

    def test_an_explicit_declaration_wins_over_derivation(self):
        class Model:
            filter_by_user_tenant_fk = "tenant__tenant_users__user"
            filter_by_tenant_fk = "somewhere__else"

        self.assertEqual(tenant_filter_path(Model), "somewhere__else")

    def test_a_model_with_no_declaration_raises_rather_than_matching_everything(self):
        """The whole reason to prefer raising: the silent alternative is the bug."""

        class Model:
            pass

        with self.assertRaises(ImproperlyConfigured):
            tenant_filter_path(Model)

    def test_a_path_that_is_not_a_membership_path_raises(self):
        """``…__user`` alone says nothing about which tenant owns the row."""

        class Model:
            filter_by_user_tenant_fk = "created_by__user"

        with self.assertRaises(ImproperlyConfigured):
            tenant_filter_path(Model)


class EveryTenantScopedViewSetCanBeScopedTests(SimpleTestCase):
    """A new tenant-scoped model with no tenant path breaks CI, not support."""

    def test_at_least_one_viewset_was_discovered(self):
        """Otherwise the sweep below passes by finding nothing."""
        self.assertGreater(len(list(_tenant_scoped_viewsets())), 20)

    def test_every_viewset_model_resolves_a_tenant_path(self):
        unresolved = []
        for viewset, _queryset in _tenant_scoped_viewsets():
            try:
                tenant_filter_path(viewset.queryset.model)
            except ImproperlyConfigured as exc:
                unresolved.append(f"{viewset.__module__}.{viewset.__name__}: {exc}")

        self.assertEqual(unresolved, [], "Tenant-scoped viewsets with no tenant path:\n" + "\n".join(unresolved))

    def test_every_resolved_path_actually_filters(self):
        """Compiles the SQL, so a stale or misspelt path fails here."""
        broken = []
        for viewset, queryset in _tenant_scoped_viewsets():
            path = tenant_filter_path(queryset.model)
            try:
                str(queryset.filter(**{path: 1}).query)
            except Exception as exc:  # noqa: BLE001 — reporting, not handling
                broken.append(f"{viewset.__module__}.{viewset.__name__} path {path!r}: {exc}")

        self.assertEqual(broken, [], "Tenant paths that do not resolve against the model:\n" + "\n".join(broken))
