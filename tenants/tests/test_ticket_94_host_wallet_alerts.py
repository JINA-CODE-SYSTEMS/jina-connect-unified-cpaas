import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_host_wallet_alerts_no_tenant_does_not_500():
    user = get_user_model().objects.create_superuser(
        username="ticket94_admin",
        email="ticket94_admin@example.com",
        mobile="+919100000199",
        password="pass12345",
    )
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/tenants/host-wallet/alerts/")

    assert response.status_code == 200
    assert response.data["summary"]["total"] == 0
    assert response.data["alerts"] == []
