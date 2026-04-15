"""Tests for tenant filter_by_product RCS support."""

import pytest

from rcs.models import RCSApp
from tenants.filters import TenantFilter
from tenants.models import Tenant


@pytest.fixture
def tenant_with_rcs(db):
    tenant = Tenant.objects.create(name="RCS Tenant")
    RCSApp.objects.create(
        tenant=tenant,
        provider="GOOGLE_RBM",
        agent_id="filter-agent@rbm.goog",
        is_active=True,
    )
    return tenant


@pytest.fixture
def tenant_no_rcs(db):
    return Tenant.objects.create(name="No RCS Tenant")


@pytest.mark.django_db
class TestTenantFilterByProductRCS:
    def test_filter_rcs_includes_tenant_with_active_rcs_app(self, tenant_with_rcs, tenant_no_rcs):
        f = TenantFilter(data={"product": "rcs"}, queryset=Tenant.objects.all())
        result = f.qs
        pks = list(result.values_list("pk", flat=True))
        assert tenant_with_rcs.pk in pks
        assert tenant_no_rcs.pk not in pks

    def test_filter_all_includes_tenant_with_rcs(self, tenant_with_rcs, tenant_no_rcs):
        f = TenantFilter(data={"product": "all"}, queryset=Tenant.objects.all())
        result = f.qs
        pks = list(result.values_list("pk", flat=True))
        assert tenant_with_rcs.pk in pks

    def test_filter_all_excludes_tenant_with_no_channel(self, tenant_no_rcs, db):
        # Ensure tenant_no_rcs has no channel apps
        f = TenantFilter(data={"product": "all"}, queryset=Tenant.objects.filter(pk=tenant_no_rcs.pk))
        result = f.qs
        pks = list(result.values_list("pk", flat=True))
        assert tenant_no_rcs.pk not in pks

    def test_filter_rcs_inactive_app_excluded(self, tenant_with_rcs):
        RCSApp.objects.filter(tenant=tenant_with_rcs).update(is_active=False)
        f = TenantFilter(data={"product": "rcs"}, queryset=Tenant.objects.all())
        result = f.qs
        pks = list(result.values_list("pk", flat=True))
        assert tenant_with_rcs.pk not in pks

    def test_filter_invalid_product_returns_empty(self, tenant_with_rcs):
        f = TenantFilter(data={"product": "fax"}, queryset=Tenant.objects.all())
        assert f.qs.count() == 0
