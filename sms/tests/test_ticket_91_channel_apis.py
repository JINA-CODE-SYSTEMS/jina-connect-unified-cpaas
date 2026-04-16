import pytest
from rest_framework.test import APIClient


@pytest.mark.django_db
class TestSMSTicket91ChannelAPIs:
    def test_templates_api_exists(self):
        response = APIClient().get("/sms/v1/templates/")
        assert response.status_code == 401

    def test_broadcast_api_exists(self):
        response = APIClient().get("/sms/v1/broadcast/")
        assert response.status_code == 401

    def test_contacts_api_exists(self):
        response = APIClient().get("/sms/v1/contacts/")
        assert response.status_code == 401
