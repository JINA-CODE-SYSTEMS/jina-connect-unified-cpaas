"""Construct RCS Suggestion payloads (replies + actions)."""

from __future__ import annotations


class SuggestionBuilder:
    """Construct RCS Suggestion payloads."""

    @staticmethod
    def suggested_reply(text: str, postback_data: str) -> dict:
        return {
            "reply": {
                "text": text[:25],
                "postbackData": postback_data,
            }
        }

    @staticmethod
    def suggested_action_dial(text: str, phone_number: str, postback_data: str = "") -> dict:
        return {
            "action": {
                "text": text[:25],
                "postbackData": postback_data,
                "dialAction": {"phoneNumber": phone_number},
            }
        }

    @staticmethod
    def suggested_action_open_url(text: str, url: str, postback_data: str = "") -> dict:
        return {
            "action": {
                "text": text[:25],
                "postbackData": postback_data,
                "openUrlAction": {"url": url},
            }
        }

    @staticmethod
    def suggested_action_view_location(
        text: str, lat: float, lng: float, label: str = "", postback_data: str = ""
    ) -> dict:
        return {
            "action": {
                "text": text[:25],
                "postbackData": postback_data,
                "viewLocationAction": {
                    "latLong": {"latitude": lat, "longitude": lng},
                    "label": label,
                },
            }
        }

    @staticmethod
    def suggested_action_calendar(
        text: str,
        title: str,
        description: str,
        start_time: str,
        end_time: str,
        postback_data: str = "",
    ) -> dict:
        return {
            "action": {
                "text": text[:25],
                "postbackData": postback_data,
                "createCalendarEventAction": {
                    "title": title[:100],
                    "description": description[:500],
                    "startTime": start_time,
                    "endTime": end_time,
                },
            }
        }

    @staticmethod
    def suggested_action_share_location(text: str, postback_data: str = "") -> dict:
        return {
            "action": {
                "text": text[:25],
                "postbackData": postback_data,
                "shareLocationAction": {},
            }
        }

    @staticmethod
    def from_channel_agnostic_keyboard(keyboard: list) -> list:
        """Convert BaseChannelAdapter keyboard spec to RCS suggestions.

        Input format (channel-agnostic):
        [
            {"text": "Yes", "callback_data": "yes"},
            {"text": "Call Us", "type": "phone", "phone": "+1234567890"},
            {"text": "Visit", "type": "url", "url": "https://example.com"},
        ]
        """
        suggestions = []
        for btn in keyboard[:11]:  # Max 11 suggestions
            btn_type = btn.get("type", "reply")
            if btn_type == "phone":
                suggestions.append(
                    SuggestionBuilder.suggested_action_dial(
                        btn.get("text", ""), btn.get("phone", ""), btn.get("callback_data", "")
                    )
                )
            elif btn_type == "url":
                suggestions.append(
                    SuggestionBuilder.suggested_action_open_url(
                        btn.get("text", ""), btn.get("url", ""), btn.get("callback_data", "")
                    )
                )
            elif btn_type == "location":
                suggestions.append(
                    SuggestionBuilder.suggested_action_share_location(
                        btn.get("text", "Share Location"), btn.get("callback_data", "")
                    )
                )
            else:
                suggestions.append(
                    SuggestionBuilder.suggested_reply(
                        btn.get("text", ""), btn.get("callback_data", btn.get("text", ""))
                    )
                )
        return suggestions
