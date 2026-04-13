"""Unit tests for Telegram task content extraction and chatflow routing."""

import sys
import types
from unittest.mock import MagicMock

import pytest

from contacts.models import AssigneeTypeChoices
from telegram.tasks import _extract_message_content, _handle_chatflow_routing_telegram


class TestExtractMessageContent:
    def test_extract_text(self):
        content = _extract_message_content({"text": "hello"})
        assert content == {"type": "text", "body": {"text": "hello"}}

    def test_extract_photo_prefers_largest_and_caption(self):
        message = {
            "photo": [{"file_id": "small"}, {"file_id": "large"}],
            "caption": "an image",
        }
        content = _extract_message_content(message)
        assert content == {
            "type": "image",
            "body": {"text": "an image"},
            "media": {"file_id": "large", "mime_type": "image/jpeg"},
        }

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            (
                {
                    "document": {
                        "file_id": "doc-id",
                        "file_name": "policy.pdf",
                        "mime_type": "application/pdf",
                    },
                    "caption": "doc",
                },
                {
                    "type": "document",
                    "body": {"text": "doc"},
                    "media": {
                        "file_id": "doc-id",
                        "file_name": "policy.pdf",
                        "mime_type": "application/pdf",
                    },
                },
            ),
            (
                {
                    "video": {
                        "file_id": "vid-id",
                        "mime_type": "video/webm",
                    },
                    "caption": "clip",
                },
                {
                    "type": "video",
                    "body": {"text": "clip"},
                    "media": {
                        "file_id": "vid-id",
                        "mime_type": "video/webm",
                    },
                },
            ),
            (
                {
                    "audio": {
                        "file_id": "aud-id",
                        "mime_type": "audio/ogg",
                    },
                    "caption": "song",
                },
                {
                    "type": "audio",
                    "body": {"text": "song"},
                    "media": {
                        "file_id": "aud-id",
                        "mime_type": "audio/ogg",
                    },
                },
            ),
            (
                {
                    "voice": {
                        "file_id": "voice-id",
                        "mime_type": "audio/ogg",
                    }
                },
                {
                    "type": "audio",
                    "body": {"text": ""},
                    "media": {
                        "file_id": "voice-id",
                        "mime_type": "audio/ogg",
                    },
                },
            ),
        ],
    )
    def test_extract_media_types(self, message, expected):
        assert _extract_message_content(message) == expected

    def test_extract_location(self):
        content = _extract_message_content({"location": {"latitude": 10.2, "longitude": 20.4}})
        assert content == {
            "type": "location",
            "body": {"latitude": 10.2, "longitude": 20.4},
        }

    def test_extract_contact(self):
        content = _extract_message_content(
            {
                "contact": {
                    "phone_number": "+1234567890",
                    "first_name": "Jane",
                    "last_name": "Doe",
                }
            }
        )
        assert content == {
            "type": "contact",
            "body": {
                "phone_number": "+1234567890",
                "first_name": "Jane",
                "last_name": "Doe",
            },
        }

    def test_extract_fallback_for_unsupported_message(self):
        content = _extract_message_content({"sticker": {"file_id": "sticker-id"}})
        assert content == {
            "type": "text",
            "body": {"text": "[Unsupported message type]"},
        }


@pytest.mark.django_db
class TestHandleChatflowRoutingTelegram:
    def test_routes_text_input_and_unassigns_on_completion(self, contact, monkeypatch):
        contact.assigned_to_type = AssigneeTypeChoices.CHATFLOW
        contact.assigned_to_id = 99
        contact.save(update_fields=["assigned_to_type", "assigned_to_id"])

        flow = object()
        session = object()

        class _FakeChatFlow:
            objects = types.SimpleNamespace(get=lambda **kwargs: flow)

        class _QS:
            def __init__(self, first_obj):
                self._first_obj = first_obj

            def first(self):
                return self._first_obj

        class _FakeSessionModel:
            objects = types.SimpleNamespace(filter=lambda **kwargs: _QS(session))

        fake_executor = MagicMock()
        fake_executor.process_input.return_value = {"current_node_id": "node-2", "is_complete": True}

        monkeypatch.setitem(
            sys.modules,
            "chat_flow.models",
            types.SimpleNamespace(ChatFlow=_FakeChatFlow, UserChatFlowSession=_FakeSessionModel),
        )
        monkeypatch.setitem(
            sys.modules,
            "chat_flow.services.graph_executor",
            types.SimpleNamespace(get_executor=lambda _flow: fake_executor),
        )

        _handle_chatflow_routing_telegram(contact, {"type": "text", "body": {"text": "Hello"}})

        fake_executor.process_input.assert_called_once_with(contact_id=contact.id, user_input="Hello")
        contact.refresh_from_db()
        assert contact.assigned_to_type == AssigneeTypeChoices.UNASSIGNED
        assert contact.assigned_to_id is None

    def test_button_reply_uses_button_id_fallback(self, contact, monkeypatch):
        contact.assigned_to_type = AssigneeTypeChoices.CHATFLOW
        contact.assigned_to_id = 77
        contact.save(update_fields=["assigned_to_type", "assigned_to_id"])

        flow = object()
        session = object()

        class _FakeChatFlow:
            objects = types.SimpleNamespace(get=lambda **kwargs: flow)

        class _QS:
            def __init__(self, first_obj):
                self._first_obj = first_obj

            def first(self):
                return self._first_obj

        class _FakeSessionModel:
            objects = types.SimpleNamespace(filter=lambda **kwargs: _QS(session))

        fake_executor = MagicMock()
        fake_executor.process_input.return_value = {"current_node_id": "node-1", "is_complete": False}

        monkeypatch.setitem(
            sys.modules,
            "chat_flow.models",
            types.SimpleNamespace(ChatFlow=_FakeChatFlow, UserChatFlowSession=_FakeSessionModel),
        )
        monkeypatch.setitem(
            sys.modules,
            "chat_flow.services.graph_executor",
            types.SimpleNamespace(get_executor=lambda _flow: fake_executor),
        )

        _handle_chatflow_routing_telegram(
            contact,
            {
                "type": "button_reply",
                "body": {"text": ""},
                "button_id": "approve",
            },
        )

        fake_executor.process_input.assert_called_once_with(contact_id=contact.id, user_input="approve")
