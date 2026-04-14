from types import SimpleNamespace

from sms.providers.fast2sms_provider import Fast2SMSProvider


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _app(credentials=None):
    return SimpleNamespace(
        provider_credentials=credentials or {},
        sender_id="JINA",
    )


def test_send_sms_missing_api_key_returns_failure():
    provider = Fast2SMSProvider(_app({}))

    result = provider.send_sms(to="+14155551111", body="hello")

    assert result.success is False
    assert "api_key" in (result.error_message or "")


def test_send_sms_success(monkeypatch):
    def _post(url, json, headers, timeout):
        assert headers["authorization"] == "k1"
        return _Resp(200, {"return": True, "request_id": "REQ123"})

    monkeypatch.setattr("sms.providers.fast2sms_provider.requests.post", _post)

    provider = Fast2SMSProvider(_app({"api_key": "k1", "route": "dlt"}))
    result = provider.send_sms(to="+14155551111", body="hello")

    assert result.success is True
    assert result.message_id == "REQ123"


def test_send_sms_error(monkeypatch):
    def _post(url, json, headers, timeout):
        return _Resp(400, {"return": False, "message": "bad request"})

    monkeypatch.setattr("sms.providers.fast2sms_provider.requests.post", _post)

    provider = Fast2SMSProvider(_app({"api_key": "k1"}))
    result = provider.send_sms(to="+14155551111", body="hello")

    assert result.success is False


def test_parse_dlr_webhook_status_map():
    provider = Fast2SMSProvider(_app())

    delivered = provider.parse_dlr_webhook({"request_id": "R1", "status": "DELIVERED"})
    undelivered = provider.parse_dlr_webhook({"request_id": "R2", "status": "UNDELIVERED"})

    assert delivered.status == "DELIVERED"
    assert undelivered.status == "UNDELIVERED"


def test_validate_webhook_signature_no_secret_returns_false():
    """When no webhook_secret is configured the provider must fail closed."""
    provider = Fast2SMSProvider(_app({}))
    req = SimpleNamespace(headers={})
    assert provider.validate_webhook_signature(req) is False


def test_validate_webhook_signature_correct_header():
    provider = Fast2SMSProvider(_app({"webhook_secret": "s1"}))
    req = SimpleNamespace(headers={"X-Webhook-Secret": "s1"})

    assert provider.validate_webhook_signature(req) is True


def test_validate_webhook_signature_wrong_header():
    provider = Fast2SMSProvider(_app({"webhook_secret": "s1"}))
    req = SimpleNamespace(headers={"X-Webhook-Secret": "wrong"})

    assert provider.validate_webhook_signature(req) is False
