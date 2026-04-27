"""Unit tests for _handle_message_reaction (#148)."""

from unittest.mock import patch

import pytest

from team_inbox.models import AuthorChoices, MessageDirectionChoices, MessagePlatformChoices, Messages


@pytest.fixture
def inbox_message(db, bot_app):
    """Create a Messages row that can be targeted by a reaction event."""
    return Messages.objects.create(
        tenant=bot_app.tenant,
        external_message_id="9001",
        platform=MessagePlatformChoices.TELEGRAM,
        direction=MessageDirectionChoices.INCOMING,
        author=AuthorChoices.CONTACT,
        content={"type": "text", "body": {"text": "hello"}},
        reactions=[],
    )


def _make_reaction_event(bot_app, message_id, emoji, user_id=42):
    """Build a minimal TelegramWebhookEvent for a message_reaction."""
    from telegram.models import TelegramWebhookEvent

    return TelegramWebhookEvent(
        bot_app=bot_app,
        event_type="MESSAGE_REACTION",
        payload={
            "message_reaction": {
                "message_id": message_id,
                "user": {"id": user_id},
                "new_reaction": [{"emoji": emoji}],
            }
        },
    )


@pytest.mark.django_db
class TestHandleMessageReaction:
    def test_happy_path_appends_reaction(self, bot_app, inbox_message):
        """Reaction emoji is appended to Messages.reactions and message is saved."""
        from telegram.tasks import _handle_message_reaction

        event = _make_reaction_event(bot_app, message_id=9001, emoji="👍")

        with patch("team_inbox.signals.broadcast_to_tenant_team") as mock_broadcast:
            _handle_message_reaction(event)

        inbox_message.refresh_from_db()
        assert len(inbox_message.reactions) == 1
        assert inbox_message.reactions[0]["emoji"] == "👍"
        assert inbox_message.reactions[0]["user_id"] == 42
        mock_broadcast.assert_called_once()
        broadcast_kwargs = mock_broadcast.call_args
        assert broadcast_kwargs[1]["message_type"] == "message_updated" or broadcast_kwargs[0][1] == "message_updated"

    def test_multiple_reactions_accumulate(self, bot_app, inbox_message):
        """Calling twice accumulates both reactions on the message."""
        from telegram.tasks import _handle_message_reaction

        event1 = _make_reaction_event(bot_app, message_id=9001, emoji="👍", user_id=1)
        event2 = _make_reaction_event(bot_app, message_id=9001, emoji="❤️", user_id=2)

        with patch("team_inbox.signals.broadcast_to_tenant_team"):
            _handle_message_reaction(event1)
            _handle_message_reaction(event2)

        inbox_message.refresh_from_db()
        emojis = [r["emoji"] for r in inbox_message.reactions]
        assert "👍" in emojis
        assert "❤️" in emojis

    def test_no_message_id_silently_discards(self, bot_app):
        """Event with no message_id is silently ignored (no exception raised)."""
        from telegram.models import TelegramWebhookEvent
        from telegram.tasks import _handle_message_reaction

        event = TelegramWebhookEvent(
            bot_app=bot_app,
            event_type="MESSAGE_REACTION",
            payload={"message_reaction": {"new_reaction": [{"emoji": "👍"}]}},
        )
        _handle_message_reaction(event)  # must not raise

    def test_message_not_found_silently_discards(self, bot_app):
        """Reaction for an unknown external_message_id is silently discarded."""
        from telegram.tasks import _handle_message_reaction

        event = _make_reaction_event(bot_app, message_id=99999, emoji="🔥")
        _handle_message_reaction(event)  # must not raise

    def test_websocket_broadcast_contains_expected_data(self, bot_app, inbox_message):
        """broadcast_to_tenant_team is called with the correct tenant_id and data keys."""
        from telegram.tasks import _handle_message_reaction

        event = _make_reaction_event(bot_app, message_id=9001, emoji="🎉")

        with patch("team_inbox.signals.broadcast_to_tenant_team") as mock_broadcast:
            _handle_message_reaction(event)

        mock_broadcast.assert_called_once()
        call_kwargs = mock_broadcast.call_args[1]
        assert call_kwargs["tenant_id"] == bot_app.tenant.pk
        assert call_kwargs["data"]["external_message_id"] == "9001"
        assert len(call_kwargs["data"]["reactions"]) == 1
