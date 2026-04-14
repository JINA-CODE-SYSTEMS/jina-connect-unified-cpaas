from types import SimpleNamespace
from unittest.mock import MagicMock

from sms.providers.msg91_provider import MSG91SMSProvider


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
        dlt_template_id="FLOW1",
    )


def test_send_sms_missing_auth_key_returns_failure():
    provider = MSG91SMSProvider(_app({}))

    result = provider.send_sms(to="+14155551111", body="hello")

    assert result.success is False
    assert "auth_key" in (result.error_message or "")


def test_send_sms_success(monkeypatch):
    def _post(url, json, headers, timeout):
        assert headers["authkey"] == "k1"
        return _Resp(200, {"type": "success", "request_id": "REQ123"})

    monkeypatch.setattr("sms.providers.msg91_provider.requests.post", _post)

    provider = MSG91SMSProvider(_app({"auth_key": "k1"}))
    result = provider.send_sms(to="+14155551111", body="hello")

    assert result.success is True
    assert result.message_id == "REQ123"


def test_send_sms_error(monkeypatch):
    def _post(url, json, headers, timeout):
        return _Resp(400, {"type": "error", "message": "bad request"})

    monkeypatch.setattr("sms.providers.msg91_provider.requests.post", _post)

    provider = MSG91SMSProvider(_app({"auth_key": "k1"}))
    result = provider.send_sms(to="+14155551111", body="hello")

    assert result.success is False


def test_parse_inbound_webhook():
    provider = MSG91SMSProvider(_app())

    parsed = provider.parse_inbound_webhook(
        {"mobile": "+14155551111", "sender": "JINA", "message": "ping", "msg_id": "M1"}
    )

    assert parsed.from_number == "+14155551111"
    assert parsed.to_number == "JINA"
    assert parsed.body == "ping"
    assert parsed.provider_message_id == "M1"


def test_parse_dlr_webhook():
    provider = MSG91SMSProvider(_app())

    delivered = provider.parse_dlr_webhook({"request_id": "R1", "status": "1"})
    failed = provider.parse_dlr_webhook({"request_id": "R2", "status": "2"})

    assert delivered.status == "DELIVERED"
    assert failed.status == "FAILED"


def test_validate_webhook_signature_no_secret_returns_false():
    """When no webhook_secret is set the provider must fail closed."""
    app = SimpleNamespace(provider_credentials={}, sender_id="JINA", dlt_template_id="F1", webhook_secret="")
    provider = MSG91SMSProvider(app)
    assert provider.validate_webhook_signature(SimpleNamespace(headers={}, body=b"")) is False


def test_validate_webhook_signature_missing_header_returns_false():
    """Matching secret set but header absent → fail closed."""
    secret = "mysecret"
    app = SimpleNamespace(provider_credentials={}, sender_id="JINA", dlt_template_id="F1", webhook_secret=secret)
    provider = MSG91SMSProvider(app)
    req = SimpleNamespace(headers={}, body=b"data")
    assert provider.validate_webhook_signature(req) is False


def test_validate_webhook_signature_valid():
    """Correct HMAC-SHA256 signature over request body is accepted."""
    import hashlib
    import hmac as hmac_module

    secret = "webhook-secret-123"
    body = b'{"mobile":"919999999999"}'
    expected_sig = hmac_module.new(secret.encode(), body, hashlib.sha256).hexdigest()

    app = SimpleNamespace(
        provider_credentials={}, sender_id="JINA", dlt_template_id="F1", webhook_secret=secret
    )
    provider = MSG91SMSProvider(app)
    req = MagicMock()
    req.headers = {"X-Msg91-Signature": expected_sig}
    req.body = body
    assert provider.validate_webhook_signature(req) is True


def test_validate_webhook_signature_tampered_body_rejected():
    """Signature for original body must reject tampered body."""
    import hashlib
    import hmac as hmac_module

    secret = "webhook-secret-123"
    real_body = b'{"mobile":"919999999999"}'
    sig = hmac_module.new(secret.encode(), real_body, hashlib.sha256).hexdigest()

    app = SimpleNamespace(
        provider_credentials={}, sender_id="JINA", dlt_template_id="F1", webhook_secret=secret
    )
    provider = MSG91SMSProvider(app)
    req = MagicMock()
    req.headers = {"X-Msg91-Signature": sig}
    req.body = b'{"mobile":"malicious"}'
    assert provider.validate_webhook_signature(req) is False
