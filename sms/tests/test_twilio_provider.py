from types import SimpleNamespace

from sms.providers.twilio_provider import TwilioSMSProvider


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _app(credentials=None):
    return SimpleNamespace(
        provider_credentials=credentials or {},
        sender_id="+14155550000",
        dlr_webhook_url="https://example.com/sms/dlr",
    )


def test_send_sms_missing_credentials_returns_failure():
    provider = TwilioSMSProvider(_app({}))

    result = provider.send_sms(to="+14155551111", body="hello")

    assert result.success is False
    assert "missing" in (result.error_message or "").lower()


def test_send_sms_success(monkeypatch):
    def _post(url, data, auth, timeout):
        assert "Messages.json" in url
        assert data["To"] == "+14155551111"
        assert data["Body"] == "hello"
        assert auth == ("AC123", "tok")
        return _Resp(200, {"sid": "SM123", "num_segments": "2"})

    monkeypatch.setattr("sms.providers.twilio_provider.requests.post", _post)

    provider = TwilioSMSProvider(_app({"account_sid": "AC123", "auth_token": "tok"}))
    result = provider.send_sms(to="+14155551111", body="hello")

    assert result.success is True
    assert result.message_id == "SM123"
    assert result.segment_count == 2


def test_send_sms_error_response(monkeypatch):
    def _post(url, data, auth, timeout):
        return _Resp(400, {"code": 21614, "message": "Invalid To"})

    monkeypatch.setattr("sms.providers.twilio_provider.requests.post", _post)

    provider = TwilioSMSProvider(_app({"account_sid": "AC123", "auth_token": "tok"}))
    result = provider.send_sms(to="+141", body="hello")

    assert result.success is False
    assert result.error_code == "21614"


def test_parse_inbound_webhook():
    provider = TwilioSMSProvider(_app())

    parsed = provider.parse_inbound_webhook({
        "From": "+14155551111",
        "To": "+14155550000",
        "Body": "ping",
        "MessageSid": "SM-IN-1",
    })

    assert parsed.from_number == "+14155551111"
    assert parsed.to_number == "+14155550000"
    assert parsed.body == "ping"
    assert parsed.provider_message_id == "SM-IN-1"


def test_parse_dlr_webhook_maps_status():
    provider = TwilioSMSProvider(_app())

    parsed = provider.parse_dlr_webhook({"MessageSid": "SM-DLR-1", "MessageStatus": "delivered"})

    assert parsed.provider_message_id == "SM-DLR-1"
    assert parsed.status == "DELIVERED"


def test_validate_webhook_signature():
    creds = {"account_sid": "AC123", "auth_token": "tok"}
    provider = TwilioSMSProvider(_app(creds))

    class _Req:
        headers = {}

        @staticmethod
        def build_absolute_uri():
            return "https://example.com/sms/v1/webhooks/1/inbound/"

        class POST:
            @staticmethod
            def dict():
                return {"From": "+14155551111", "Body": "hello"}

    req = _Req()
    sig = provider._build_twilio_signature(req.build_absolute_uri(), req.POST.dict(), "tok")
    req.headers["X-Twilio-Signature"] = sig

    assert provider.validate_webhook_signature(req) is True
