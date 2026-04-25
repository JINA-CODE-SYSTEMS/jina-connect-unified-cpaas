"""
Tests for keyboard builder — build_inline_keyboard, build_callback_data, parse_callback_data.
"""

import pytest

from telegram.services.keyboard_builder import (
    build_callback_data,
    build_inline_keyboard,
    build_template_button_keyboard,
    parse_callback_data,
)


class TestBuildInlineKeyboard:
    def test_single_row_callback_buttons(self):
        buttons = [[{"text": "Yes", "callback_data": "yes"}, {"text": "No", "callback_data": "no"}]]
        result = build_inline_keyboard(buttons)
        assert "inline_keyboard" in result
        assert len(result["inline_keyboard"]) == 1
        assert len(result["inline_keyboard"][0]) == 2
        assert result["inline_keyboard"][0][0]["text"] == "Yes"
        assert result["inline_keyboard"][0][0]["callback_data"] == "yes"

    def test_multiple_rows(self):
        buttons = [
            [{"text": "A", "callback_data": "a"}],
            [{"text": "B", "callback_data": "b"}],
        ]
        result = build_inline_keyboard(buttons)
        assert len(result["inline_keyboard"]) == 2

    def test_url_button(self):
        buttons = [[{"text": "Visit", "url": "https://example.com"}]]
        result = build_inline_keyboard(buttons)
        assert result["inline_keyboard"][0][0]["url"] == "https://example.com"
        assert "callback_data" not in result["inline_keyboard"][0][0]

    def test_callback_data_exceeds_64_bytes_raises(self):
        long_data = "x" * 65
        buttons = [[{"text": "Long", "callback_data": long_data}]]
        with pytest.raises(ValueError, match="exceeds 64 bytes"):
            build_inline_keyboard(buttons)

    def test_exactly_64_bytes_ok(self):
        data = "x" * 64
        buttons = [[{"text": "OK", "callback_data": data}]]
        result = build_inline_keyboard(buttons)
        assert result["inline_keyboard"][0][0]["callback_data"] == data


class TestBuildCallbackData:
    def test_format(self):
        result = build_callback_data("select", "node1", "abc123")
        assert result == "v1:select:node1:abc123"

    def test_exceeds_limit_raises(self):
        with pytest.raises(ValueError, match="exceeds 64 bytes"):
            build_callback_data("action", "x" * 60, "nonce")


class TestBuildTemplateButtonKeyboard:
    def test_url_and_phone_number_buttons(self):
        result = build_template_button_keyboard(
            [
                {"type": "URL", "text": "Visit", "url": "https://example.com"},
                {"type": "PHONE_NUMBER", "text": "Call", "phone_number": "+911234567890"},
            ]
        )

        assert result == {
            "inline_keyboard": [
                [{"text": "Visit", "url": "https://example.com"}],
                [{"text": "Call", "url": "tel:+911234567890"}],
            ]
        }

    @pytest.mark.parametrize(
        ("button_type", "expected_callback"),
        [
            ("QUICK_REPLY", "quick_reply:Yes"),
            ("COPY_CODE", "copy_code:Code"),
            ("OTP", "otp:123456"),
        ],
    )
    def test_interactive_buttons_use_callback_data(self, button_type, expected_callback):
        button_text = expected_callback.split(":", 1)[1]
        result = build_template_button_keyboard([{"type": button_type, "text": button_text}])

        assert result["inline_keyboard"][0][0]["callback_data"] == expected_callback

    def test_unknown_button_type_falls_back_to_action_callback(self):
        result = build_template_button_keyboard([{"type": "UNKNOWN", "text": "Do it"}])

        assert result["inline_keyboard"][0][0]["callback_data"] == "action:Do it"

    def test_empty_text_buttons_are_skipped_and_empty_markup_returns_none(self):
        assert build_template_button_keyboard([{"type": "URL", "text": "   ", "url": "https://example.com"}]) is None

        result = build_template_button_keyboard(
            [
                {"type": "URL", "text": "", "url": "https://example.com"},
                {"type": "QUICK_REPLY", "text": "Keep"},
            ]
        )

        assert result == {"inline_keyboard": [[{"text": "Keep", "callback_data": "quick_reply:Keep"}]]}

    def test_multibyte_callback_data_is_truncated_safely(self):
        result = build_template_button_keyboard([{"type": "QUICK_REPLY", "text": "🙂" * 40}])

        callback_data = result["inline_keyboard"][0][0]["callback_data"]
        assert callback_data.endswith("...")
        assert len(callback_data.encode("utf-8")) <= 64


class TestParseCallbackData:
    def test_valid_data(self):
        result = parse_callback_data("v1:select:node1:abc123")
        assert result is not None
        assert result["version"] == "v1"
        assert result["action"] == "select"
        assert result["id"] == "node1"
        assert result["nonce"] == "abc123"

    def test_invalid_format_returns_none(self):
        assert parse_callback_data("invalid") is None
        assert parse_callback_data("") is None
        assert parse_callback_data("v1:only_two") is None

    def test_roundtrip(self):
        data = build_callback_data("next", "step5", "xyz789")
        parsed = parse_callback_data(data)
        assert parsed["action"] == "next"
        assert parsed["id"] == "step5"
        assert parsed["nonce"] == "xyz789"
