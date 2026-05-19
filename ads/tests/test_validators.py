"""Pre-publish validator tests (#196 + #201 review)."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from ads.validators import (
    PREFILLED_MESSAGE_MAX_CHARS,
    validate_creative_dimensions,
    validate_prefilled_message,
)


class TestPrefilledMessageValidator:
    def test_accepts_clean_message(self):
        validate_prefilled_message("Hi! I saw your ad on Facebook.")

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            validate_prefilled_message("")
        with pytest.raises(ValidationError):
            validate_prefilled_message("   ")

    def test_rejects_non_string(self):
        with pytest.raises(ValidationError):
            validate_prefilled_message(None)  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            validate_prefilled_message(["a", "b"])  # type: ignore[arg-type]

    def test_rejects_over_length(self):
        with pytest.raises(ValidationError) as exc:
            validate_prefilled_message("x" * (PREFILLED_MESSAGE_MAX_CHARS + 1))
        assert "≤" in str(exc.value)

    def test_denylist_guarantee(self):
        with pytest.raises(ValidationError):
            validate_prefilled_message("100% guaranteed results!")

    def test_denylist_medical(self):
        with pytest.raises(ValidationError):
            validate_prefilled_message("Our miracle cure for diabetes is here.")

    def test_denylist_financial(self):
        with pytest.raises(ValidationError):
            validate_prefilled_message("Get rich quick — no risk!")

    def test_denylist_unicode_homoglyph(self):
        # NFKC normalisation should canonicalise full-width Latin to
        # ASCII so the denylist still bites. (#201 review)
        with pytest.raises(ValidationError):
            validate_prefilled_message("１００％ guaranteed results")  # full-width digits

    def test_denylist_case_insensitive(self):
        with pytest.raises(ValidationError):
            validate_prefilled_message("MIRACLE CURE for joint pain!")


class TestCreativeDimensionsValidator:
    def test_accepts_landscape(self):
        validate_creative_dimensions(width=1200, height=628, media_type="image")

    def test_accepts_square(self):
        validate_creative_dimensions(width=1080, height=1080, media_type="image")

    def test_rejects_extreme_panorama(self):
        with pytest.raises(ValidationError):
            validate_creative_dimensions(width=4000, height=400, media_type="image")

    def test_rejects_extreme_portrait(self):
        with pytest.raises(ValidationError):
            validate_creative_dimensions(width=300, height=2000, media_type="image")

    def test_no_op_on_missing_dimensions(self):
        # Video / unknown dimensions — skip check.
        validate_creative_dimensions(width=0, height=0, media_type="video")
        validate_creative_dimensions(width=None, height=None, media_type="image")
