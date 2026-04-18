"""Construct RCS RichCard payloads."""

from __future__ import annotations

from typing import Optional


class RichCardBuilder:
    """Construct RCS RichCard payloads.

    iOS considerations:
    - Media is rendered at a fixed 3:2 aspect ratio on iPhone (iOS 18+)
    - Use MEDIUM height for cross-platform compatibility
    - Suggestions text should be ≤20 chars for iOS (vs 25 for Android)
    """

    IOS_SAFE_MEDIA_HEIGHT = "MEDIUM"
    IOS_SUGGESTION_TEXT_LIMIT = 20
    ANDROID_SUGGESTION_TEXT_LIMIT = 25

    @staticmethod
    def standalone_card(
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        media_url: Optional[str] = None,
        media_height: str = "MEDIUM",
        thumbnail_url: Optional[str] = None,
        suggestions: Optional[list] = None,
        orientation: str = "VERTICAL",
        thumbnail_alignment: str = "LEFT",
        device_profile: Optional[str] = None,
    ) -> dict:
        # Force iOS-safe media height when targeting Apple devices (#113)
        if device_profile and device_profile.upper() == "IOS" and media_url:
            media_height = RichCardBuilder.IOS_SAFE_MEDIA_HEIGHT
        card_content: dict = {}
        if title:
            card_content["title"] = title[:200]
        if description:
            card_content["description"] = description[:2000]
        if media_url:
            card_content["media"] = {
                "height": media_height,
                "contentInfo": {"fileUrl": media_url},
            }
            if thumbnail_url:
                card_content["media"]["contentInfo"]["thumbnailUrl"] = thumbnail_url
        if suggestions:
            card_content["suggestions"] = suggestions[:4]
        return {
            "richCard": {
                "standaloneCard": {
                    "cardOrientation": orientation,
                    "thumbnailImageAlignment": thumbnail_alignment,
                    "cardContent": card_content,
                }
            }
        }

    @staticmethod
    def carousel(
        cards: list,
        card_width: str = "MEDIUM",
    ) -> dict:
        if len(cards) < 2:
            raise ValueError("Carousel requires at least 2 cards")
        if len(cards) > 10:
            raise ValueError("Carousel supports maximum 10 cards")
        return {
            "richCard": {
                "carouselCard": {
                    "cardWidth": card_width,
                    "cardContents": cards[:10],
                }
            }
        }

    @staticmethod
    def card_content(
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        media_url: Optional[str] = None,
        media_height: str = "MEDIUM",
        suggestions: Optional[list] = None,
    ) -> dict:
        content: dict = {}
        if title:
            content["title"] = title[:200]
        if description:
            content["description"] = description[:2000]
        if media_url:
            content["media"] = {
                "height": media_height,
                "contentInfo": {"fileUrl": media_url},
            }
        if suggestions:
            content["suggestions"] = suggestions[:4]
        return content
