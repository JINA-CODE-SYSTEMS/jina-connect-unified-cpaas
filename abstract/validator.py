# yourapp/validators.py
import phonenumbers
from django.conf import settings
from django.core.exceptions import ValidationError

# Change these to whatever series/prefixes you want to always accept
PHONE_SERIES_WHITELIST = getattr(
    settings,
    "PHONE_SERIES_WHITELIST",
    ["+15"],  # Example: accept +1-555-... demo/test numbers
)


def validate_phone_with_series(value):
    """
    Compatible with phonenumber_field's PhoneNumberField.
    - Accepts numbers starting with any whitelisted series/prefix.
    - For others, does a relaxed 'possible number' validation.
    Raise ValidationError on failure.
    """
    if value in (None, ""):
        return

    # value could be PhoneNumber or str; stringify safely
    s = str(value).replace(" ", "").replace("-", "")

    # 1) If matches any whitelisted series/prefix, accept
    for prefix in PHONE_SERIES_WHITELIST:
        if s.startswith(prefix):
            # Optionally ensure it's a plausible length: + + 7-15 digits total
            # If you want a minimal length check, uncomment:
            # digits = s[1:] if s.startswith("+") else s
            # if not digits.isdigit() or not (7 <= len(digits) <= 15):
            #     raise ValidationError("Enter a valid phone number (e.g. +12125552368).")
            return

    # 2) Otherwise, fall back to relaxed 'possible' validation with phonenumbers
    region = getattr(settings, "PHONENUMBER_DEFAULT_REGION", None)
    try:
        num = phonenumbers.parse(s, region)
    except phonenumbers.NumberParseException:
        print("⚠️ Phone number parsing failed.")
        raise ValidationError("Enter a valid phone number (e.g. +12125552369).")

    if not phonenumbers.is_possible_number(num):
        print("⚠️ Phone number failed 'possible' validation.")
        raise ValidationError("Enter a valid phone number (e.g. +12125552370).")
