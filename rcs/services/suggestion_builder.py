"""Construct RCS Suggestion payloads (replies + actions)."""

from __future__ import annotations


class SuggestionBuilder:
    """Construct RCS Suggestion payloads.

    Use ``device_profile="IOS"`` to apply the shorter 20-char text limit
    for suggestions on iOS devices (#113).
    """

    IOS_TEXT_LIMIT = 20
    ANDROID_TEXT_LIMIT = 25

    @classmethod
    def _text_limit(cls, device_profile: str | None = None) -> int:
        if device_profile and device_profile.upper() == "IOS":
            return cls.IOS_TEXT_LIMIT
        return cls.ANDROID_TEXT_LIMIT

    @classmethod
    def suggested_reply(cls, text: str, postback_data: str, *, device_profile: str | None = None) -> dict:
        limit = cls._text_limit(device_profile)
        return {
            "reply": {
                "text": text[:limit],
                "postbackData": postback_data,
            }
        }

    @classmethod
    def suggested_action_dial(
        cls, text: str, phone_number: str, postback_data: str = "", *, device_profile: str | None = None
    ) -> dict:
        limit = cls._text_limit(device_profile)
        return {
            "action": {
                "text": text[:limit],
                "postbackData": postback_data,
                "dialAction": {"phoneNumber": phone_number},
            }
        }

    @classmethod
    def suggested_action_open_url(
        cls, text: str, url: str, postback_data: str = "", *, device_profile: str | None = None
    ) -> dict:
        limit = cls._text_limit(device_profile)
        return {
            "action": {
                "text": text[:limit],
                "postbackData": postback_data,
                "openUrlAction": {"url": url},
            }
        }

    @classmethod
    def suggested_action_view_location(
        cls,
        text: str,
        lat: float,
        lng: float,
        label: str = "",
        postback_data: str = "",
        *,
        device_profile: str | None = None,
    ) -> dict:
        limit = cls._text_limit(device_profile)
        return {
            "action": {
                "text": text[:limit],
                "postbackData": postback_data,
                "viewLocationAction": {
                    "latLong": {"latitude": lat, "longitude": lng},
                    "label": label,
                },
            }
        }

    @classmethod
    def suggested_action_calendar(
        cls,
        text: str,
        title: str,
        description: str,
        start_time: str,
        end_time: str,
        postback_data: str = "",
        *,
        device_profile: str | None = None,
    ) -> dict:
        limit = cls._text_limit(device_profile)
        return {
            "action": {
                "text": text[:limit],
                "postbackData": postback_data,
                "createCalendarEventAction": {
                    "title": title[:100],
                    "description": description[:500],
                    "startTime": start_time,
                    "endTime": end_time,
                },
            }
        }

    @classmethod
    def suggested_action_share_location(
        cls, text: str, postback_data: str = "", *, device_profile: str | None = None
    ) -> dict:
        limit = cls._text_limit(device_profile)
        return {
            "action": {
                "text": text[:limit],
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
