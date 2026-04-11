"""
Custom Serializer Fields for WhatsApp Templates

This module contains custom serializer fields for validating
template buttons and cards with WhatsApp-specific requirements.
"""

from drf_yasg import openapi
from rest_framework import serializers

from wa.utility.data_model.gupshup.template_button_input import parse_template_buttons
from wa.utility.data_model.gupshup.template_card_input import parse_template_cards


class TemplateButtonsField(serializers.JSONField):
    """
    Custom field for validating template buttons.

    Supports button types:
    - PHONE_NUMBER: Click-to-call buttons
    - URL: Click-to-open-URL buttons
    - OTP: One-time password buttons (COPY_CODE or ONE_TAP)
    - QUICK_REPLY: Quick reply buttons

    Maximum 3 buttons per template.
    """

    class Meta:
        swagger_schema_fields = {
            "type": openapi.TYPE_ARRAY,
            "items": openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    "type": openapi.Schema(
                        type=openapi.TYPE_STRING,
                        enum=["PHONE_NUMBER", "URL", "OTP", "QUICK_REPLY"],
                        description="Type of button",
                    ),
                    "text": openapi.Schema(
                        type=openapi.TYPE_STRING, max_length=20, description="Button text displayed to user"
                    ),
                    "phone_number": openapi.Schema(
                        type=openapi.TYPE_STRING,
                        description="Phone number for PHONE_NUMBER type buttons (required for PHONE_NUMBER type)",
                    ),
                    "url": openapi.Schema(
                        type=openapi.TYPE_STRING,
                        format=openapi.FORMAT_URI,
                        description="URL for URL type buttons (required for URL type)",
                    ),
                    "example": openapi.Schema(
                        type=openapi.TYPE_ARRAY,
                        items=openapi.Schema(type=openapi.TYPE_STRING),
                        description="Example URLs showing how dynamic content would be replaced",
                    ),
                    "otp_type": openapi.Schema(
                        type=openapi.TYPE_STRING,
                        enum=["COPY_CODE", "ONE_TAP"],
                        description="OTP type for OTP buttons (required for OTP type)",
                    ),
                    "autofill_text": openapi.Schema(
                        type=openapi.TYPE_STRING, description="Autofill text for ONE_TAP OTP buttons"
                    ),
                    "package_name": openapi.Schema(
                        type=openapi.TYPE_STRING,
                        description="Android package name for ONE_TAP OTP buttons (required for ONE_TAP)",
                    ),
                    "signature_hash": openapi.Schema(
                        type=openapi.TYPE_STRING,
                        description="Signature hash for ONE_TAP OTP buttons (required for ONE_TAP)",
                    ),
                },
                required=["type", "text"],
            ),
            "maxItems": 3,
            "description": "List of template buttons (maximum 3 allowed)",
            "example": [
                {"type": "PHONE_NUMBER", "text": "Call Support", "phone_number": "+919876543210"},
                {
                    "type": "URL",
                    "text": "Visit Website",
                    "url": "https://example.com/{{1}}",
                    "example": ["https://example.com/promo"],
                },
                {"type": "OTP", "text": "Copy code", "otp_type": "COPY_CODE"},
                {"type": "QUICK_REPLY", "text": "Yes, I'm interested"},
            ],
        }

    def to_internal_value(self, data):
        """
        Convert input data to internal Python representation with validation.
        """
        # First get the basic JSONField validation
        json_data = super().to_internal_value(data)

        if json_data is None:
            return None

        if not isinstance(json_data, list):
            raise serializers.ValidationError("Buttons must be a list of button objects")

        if len(json_data) == 0:
            return json_data  # Empty list is valid

        # Manual validation for each button
        validation_errors = {}

        for i, button_data in enumerate(json_data):
            if not isinstance(button_data, dict):
                validation_errors[f"button_{i}"] = "Button must be a dictionary"
                continue

            button_type = button_data.get("type")
            button_text = button_data.get("text")

            # Validate button type
            valid_types = ["PHONE_NUMBER", "URL", "OTP", "QUICK_REPLY", "COPY_CODE"]
            if button_type not in valid_types:
                validation_errors[f"button_{i}_type"] = f"Invalid button type. Must be one of: {', '.join(valid_types)}"

            # Validate button text
            if not button_text:
                validation_errors[f"button_{i}_text"] = "Button text is required"
            elif len(button_text) > 20:  # WhatsApp limit
                validation_errors[f"button_{i}_text"] = "Button text cannot exceed 20 characters"

            # Type-specific validation
            if button_type == "PHONE_NUMBER":
                phone_number = button_data.get("phone_number")
                if not phone_number:
                    validation_errors[f"button_{i}_phone_number"] = (
                        "phone_number is required for PHONE_NUMBER type buttons"
                    )

            elif button_type == "URL":
                url = button_data.get("url")
                if not url:
                    validation_errors[f"button_{i}_url"] = "url is required for URL type buttons"
                elif not (url.startswith("http://") or url.startswith("https://")):
                    validation_errors[f"button_{i}_url"] = "URL must start with http:// or https://"

            elif button_type == "OTP":
                otp_type = button_data.get("otp_type") or button_data.get("otp-type")
                if not otp_type:
                    validation_errors[f"button_{i}_otp_type"] = "otp_type is required for OTP type buttons"
                elif otp_type not in ["COPY_CODE", "ONE_TAP"]:
                    validation_errors[f"button_{i}_otp_type"] = "otp_type must be COPY_CODE or ONE_TAP"

                # ONE_TAP specific validation
                if otp_type == "ONE_TAP":
                    if not button_data.get("package_name"):
                        validation_errors[f"button_{i}_package_name"] = (
                            "package_name is required for ONE_TAP OTP buttons"
                        )
                    if not button_data.get("signature_hash"):
                        validation_errors[f"button_{i}_signature_hash"] = (
                            "signature_hash is required for ONE_TAP OTP buttons"
                        )
            elif button_type == "QUICK_REPLY":
                # check text only
                text = button_data.get("text")
                if not text:
                    validation_errors[f"button_{i}_text"] = "text is required for QUICK_REPLY type buttons"
            elif button_type == "COPY_CODE":
                # COPY_CODE is for MARKETING coupon-code templates; coupon_code value is required
                coupon_code = button_data.get("coupon_code")
                if not coupon_code:
                    validation_errors[f"button_{i}_coupon_code"] = "coupon_code is required for COPY_CODE type buttons"

        # Check button count limit
        if len(json_data) > 3:
            validation_errors["button_count"] = "Maximum 3 buttons allowed per template"

        # Raise validation error if any issues found
        if validation_errors:
            raise serializers.ValidationError(validation_errors)

        # Try Pydantic validation as well (if it works)
        try:
            parse_template_buttons(json_data)
        except Exception:
            # If Pydantic validation fails, we already have manual validation
            pass

        return json_data

    def to_representation(self, value):
        """
        Convert internal value to representation.
        """
        if value is None:
            return None

        # Return the JSON data as-is
        return value


class TemplateCardsField(serializers.JSONField):
    """
    Custom field for validating template cards for carousel templates.

    Each card must have:
    - headerType: IMAGE or VIDEO
    - body: Card body text (max 160 chars)
    - sampleText: Sample text for preview
    - mediaId or mediaUrl: Media for the card header
    - buttons: Optional array of buttons (max 2 per card)

    Maximum 10 cards per template.
    """

    class Meta:
        swagger_schema_fields = {
            "type": openapi.TYPE_ARRAY,
            "items": openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    "headerType": openapi.Schema(
                        type=openapi.TYPE_STRING, enum=["IMAGE", "VIDEO"], description="Type of card header media"
                    ),
                    "mediaId": openapi.Schema(
                        type=openapi.TYPE_STRING,
                        description="Media ID from Gupshup for the card header (use either mediaId or mediaUrl)",
                    ),
                    "mediaUrl": openapi.Schema(
                        type=openapi.TYPE_STRING,
                        format=openapi.FORMAT_URI,
                        description="Media URL for the card header (use either mediaId or mediaUrl)",
                    ),
                    "body": openapi.Schema(
                        type=openapi.TYPE_STRING, max_length=160, description="Card body text (required)"
                    ),
                    "sampleText": openapi.Schema(type=openapi.TYPE_STRING, description="Sample text for the card body"),
                    "buttons": openapi.Schema(
                        type=openapi.TYPE_ARRAY,
                        items=openapi.Schema(
                            type=openapi.TYPE_OBJECT,
                            properties={
                                "type": openapi.Schema(
                                    type=openapi.TYPE_STRING,
                                    enum=["PHONE_NUMBER", "URL", "QUICK_REPLY"],
                                    description="Type of button within the card",
                                ),
                                "text": openapi.Schema(
                                    type=openapi.TYPE_STRING, max_length=20, description="Button text displayed to user"
                                ),
                                "phone_number": openapi.Schema(
                                    type=openapi.TYPE_STRING, description="Phone number for PHONE_NUMBER type buttons"
                                ),
                                "url": openapi.Schema(
                                    type=openapi.TYPE_STRING,
                                    format=openapi.FORMAT_URI,
                                    description="URL for URL type buttons",
                                ),
                            },
                            required=["type", "text"],
                        ),
                        maxItems=2,
                        description="Buttons within the card (maximum 2 per card)",
                    ),
                },
                required=["headerType", "body", "sampleText"],
            ),
            "maxItems": 10,
            "description": "List of template cards for carousel templates (maximum 10 allowed). Note: When cards are used, template-level buttons are not allowed.",
            "example": [
                {
                    "headerType": "IMAGE",
                    "mediaId": "sample_media_id_123",
                    "body": "Check out our latest product!",
                    "sampleText": "Check out our latest product!",
                    "buttons": [
                        {"type": "URL", "text": "View Details", "url": "https://example.com/product/1"},
                        {"type": "QUICK_REPLY", "text": "Add to Cart"},
                    ],
                }
            ],
        }

    def to_internal_value(self, data):
        """
        Convert input data to internal Python representation with validation.
        """
        # First get the basic JSONField validation
        json_data = super().to_internal_value(data)

        if json_data is None:
            return None

        if not isinstance(json_data, list):
            raise serializers.ValidationError("Cards must be a list of card objects")

        if len(json_data) == 0:
            return json_data  # Empty list is valid

        # Manual validation for each card
        validation_errors = {}

        for i, card_data in enumerate(json_data):
            if not isinstance(card_data, dict):
                validation_errors[f"card_{i}"] = "Card must be a dictionary"
                continue

            # Validate required fields
            header_type = card_data.get("headerType")
            body = card_data.get("body")
            sample_text = card_data.get("sampleText")

            if not header_type:
                validation_errors[f"card_{i}_headerType"] = "headerType is required"
            elif header_type not in ["IMAGE", "VIDEO"]:
                validation_errors[f"card_{i}_headerType"] = "headerType must be IMAGE or VIDEO"

            if not body:
                validation_errors[f"card_{i}_body"] = "body is required"
            elif len(body) > 160:
                validation_errors[f"card_{i}_body"] = "Card body cannot exceed 160 characters"

            if not sample_text:
                validation_errors[f"card_{i}_sampleText"] = "sampleText is required"

            # Validate media (either mediaId or mediaUrl required)
            media_id = card_data.get("mediaId")
            media_url = card_data.get("mediaUrl")
            if not media_id and not media_url:
                validation_errors[f"card_{i}_media"] = "Either mediaId or mediaUrl is required"

            # Validate card buttons if present
            card_buttons = card_data.get("buttons", [])
            if card_buttons:
                if len(card_buttons) > 2:
                    validation_errors[f"card_{i}_buttons_count"] = "Maximum 2 buttons allowed per card"

                for j, button_data in enumerate(card_buttons):
                    if not isinstance(button_data, dict):
                        validation_errors[f"card_{i}_button_{j}"] = "Button must be a dictionary"
                        continue

                    button_type = button_data.get("type")
                    button_text = button_data.get("text")

                    # Validate button type (cards support different types than template buttons)
                    valid_card_button_types = ["PHONE_NUMBER", "URL", "QUICK_REPLY"]
                    if button_type not in valid_card_button_types:
                        validation_errors[f"card_{i}_button_{j}_type"] = (
                            f"Invalid card button type. Must be one of: {', '.join(valid_card_button_types)}"
                        )

                    # Validate button text
                    if not button_text:
                        validation_errors[f"card_{i}_button_{j}_text"] = "Button text is required"
                    elif len(button_text) > 20:
                        validation_errors[f"card_{i}_button_{j}_text"] = "Button text cannot exceed 20 characters"

                    # Type-specific validation
                    if button_type == "PHONE_NUMBER":
                        phone_number = button_data.get("phone_number")
                        if not phone_number:
                            validation_errors[f"card_{i}_button_{j}_phone_number"] = (
                                "phone_number is required for PHONE_NUMBER type buttons"
                            )

                    elif button_type == "URL":
                        url = button_data.get("url")
                        if not url:
                            validation_errors[f"card_{i}_button_{j}_url"] = "url is required for URL type buttons"
                        elif not (url.startswith("http://") or url.startswith("https://")):
                            validation_errors[f"card_{i}_button_{j}_url"] = "URL must start with http:// or https://"

        # Check card count limit
        if len(json_data) > 10:
            validation_errors["card_count"] = "Maximum 10 cards allowed per template"

        # Raise validation error if any issues found
        if validation_errors:
            raise serializers.ValidationError(validation_errors)

        # Try Pydantic validation as well (if it works)
        try:
            parse_template_cards(json_data)
        except Exception:
            # If Pydantic validation fails, we already have manual validation
            pass

        return json_data

    def to_representation(self, value):
        """
        Convert internal value to representation.
        """
        if value is None:
            return None

        # Return the JSON data as-is
        return value
