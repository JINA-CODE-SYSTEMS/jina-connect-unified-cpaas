"""Tests for rich_card_builder and suggestion_builder services."""

from rcs.services.rich_card_builder import RichCardBuilder
from rcs.services.suggestion_builder import SuggestionBuilder


class TestRichCardBuilder:
    def test_standalone_card_with_title(self):
        card = RichCardBuilder.standalone_card(title="Hello", media_url="https://example.com/img.jpg")
        standalone = card["richCard"]["standaloneCard"]
        assert standalone["cardContent"]["title"] == "Hello"

    def test_standalone_card_media_url_set(self):
        card = RichCardBuilder.standalone_card(media_url="https://example.com/img.jpg")
        content = card["richCard"]["standaloneCard"]["cardContent"]
        assert content["media"]["contentInfo"]["fileUrl"] == "https://example.com/img.jpg"

    def test_standalone_card_default_height_medium(self):
        card = RichCardBuilder.standalone_card(media_url="https://example.com/img.jpg")
        height = card["richCard"]["standaloneCard"]["cardContent"]["media"]["height"]
        assert height == "MEDIUM"

    def test_standalone_card_custom_height(self):
        card = RichCardBuilder.standalone_card(media_url="https://example.com/img.jpg", media_height="TALL")
        height = card["richCard"]["standaloneCard"]["cardContent"]["media"]["height"]
        assert height == "TALL"

    def test_standalone_card_with_suggestions(self):
        suggestions = [{"reply": {"text": "Yes", "postbackData": "yes"}}]
        card = RichCardBuilder.standalone_card(
            title="Pick", media_url="https://example.com/img.jpg", suggestions=suggestions
        )
        content = card["richCard"]["standaloneCard"]["cardContent"]
        assert len(content["suggestions"]) == 1

    def test_standalone_card_no_media_url_no_media_key(self):
        card = RichCardBuilder.standalone_card(title="Title only")
        content = card["richCard"]["standaloneCard"]["cardContent"]
        assert "media" not in content

    def test_carousel_creates_correct_structure(self):
        cards = [
            {"title": "Card 1", "media_url": "https://example.com/1.jpg"},
            {"title": "Card 2", "media_url": "https://example.com/2.jpg"},
        ]
        carousel = RichCardBuilder.carousel(cards)
        card_contents = carousel["richCard"]["carouselCard"]["cardContents"]
        assert len(card_contents) == 2

    def test_carousel_card_width_default_medium(self):
        carousel = RichCardBuilder.carousel([{"title": "C1"}, {"title": "C2"}])
        card_width = carousel["richCard"]["carouselCard"]["cardWidth"]
        assert card_width == "MEDIUM"

    def test_carousel_card_width_custom(self):
        carousel = RichCardBuilder.carousel([{"title": "C1"}, {"title": "C2"}], card_width="SMALL")
        card_width = carousel["richCard"]["carouselCard"]["cardWidth"]
        assert card_width == "SMALL"


class TestSuggestionBuilder:
    def test_quick_reply_structure(self):
        suggestion = SuggestionBuilder.suggested_reply("Yes", "yes_postback")
        assert suggestion["reply"]["text"] == "Yes"
        assert suggestion["reply"]["postbackData"] == "yes_postback"

    def test_dial_action_structure(self):
        suggestion = SuggestionBuilder.suggested_action_dial("Call Us", "+14155550100")
        action = suggestion["action"]
        assert action["text"] == "Call Us"
        assert action["dialAction"]["phoneNumber"] == "+14155550100"

    def test_view_location_structure(self):
        suggestion = SuggestionBuilder.suggested_action_view_location("View Map", lat=37.7749, lng=-122.4194, label="SF")
        action = suggestion["action"]
        latlong = action["viewLocationAction"]["latLong"]
        assert latlong["latitude"] == 37.7749
        assert latlong["longitude"] == -122.4194

    def test_open_url_structure(self):
        suggestion = SuggestionBuilder.suggested_action_open_url("Visit us", "https://example.com")
        action = suggestion["action"]
        assert action["openUrlAction"]["url"] == "https://example.com"

    def test_from_channel_agnostic_keyboard_plain_list(self):
        keyboard = [
            {"text": "Yes", "callback_data": "yes"},
            {"text": "No", "callback_data": "no"},
        ]
        suggestions = SuggestionBuilder.from_channel_agnostic_keyboard(keyboard)
        assert len(suggestions) == 2
        assert suggestions[0]["reply"]["text"] == "Yes"
        assert suggestions[0]["reply"]["postbackData"] == "yes"

    def test_from_channel_agnostic_keyboard_max_11(self):
        keyboard = [{"text": f"Option {i}", "callback_data": str(i)} for i in range(20)]
        suggestions = SuggestionBuilder.from_channel_agnostic_keyboard(keyboard)
        assert len(suggestions) == 11

    def test_from_channel_agnostic_keyboard_dict_with_postback(self):
        keyboard = [{"type": "reply", "text": "Option A", "callback_data": "a"}]
        suggestions = SuggestionBuilder.from_channel_agnostic_keyboard(keyboard)
        assert suggestions[0]["reply"]["text"] == "Option A"
        assert suggestions[0]["reply"]["postbackData"] == "a"
