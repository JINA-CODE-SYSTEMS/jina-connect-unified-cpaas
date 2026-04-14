from types import SimpleNamespace

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

    parsed = provider.parse_inbound_webhook({"mobile": "+14155551111", "sender": "JINA", "message": "ping", "msg_id": "M1"})

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


def test_validate_webhook_signature_defaults_true():
    provider = MSG91SMSProvider(_app())
    assert provider.validate_webhook_signature(SimpleNamespace()) is True
