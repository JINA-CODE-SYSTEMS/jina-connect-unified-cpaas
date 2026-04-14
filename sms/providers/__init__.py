from sms.providers.base import BaseSMSProvider
from sms.providers.fast2sms_provider import Fast2SMSProvider
from sms.providers.msg91_provider import MSG91SMSProvider
from sms.providers.twilio_provider import TwilioSMSProvider

_PROVIDER_REGISTRY = {
    "TWILIO": TwilioSMSProvider,
    "MSG91": MSG91SMSProvider,
    "FAST2SMS": Fast2SMSProvider,
}


def get_sms_provider(sms_app) -> BaseSMSProvider:
    """Resolve provider implementation from SMSApp.provider."""
    provider_cls = _PROVIDER_REGISTRY.get((sms_app.provider or "").upper())
    if provider_cls is None:
        raise NotImplementedError(f"No SMS provider for '{sms_app.provider}'")
    return provider_cls(sms_app)
