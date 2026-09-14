"""A "view as organisation" operator can read the inbox, and only read it (#300).

The socket used to refuse a borrowed token at authentication. There is no HTTP
method on a WebSocket to gate writes on, so #300 closed the whole connection —
and a platform operator opening Team Inbox to answer a support question saw
"Disconnected" instead of the conversation they were asked about.

A socket does have something to gate on: the ``type`` of each inbound frame. So
the refusal moved to ``receive``, and these tests are about the two halves of
that move being right — the read now works, and the writes still do not.

``mark_as_read`` is the one that matters, and it is not local bookkeeping: it
clears the organisation's unread state *and* sends a read receipt to their
customer over WhatsApp. An operator must not send blue ticks from a customer's
own account.
"""

import asyncio
import re
import uuid
from unittest.mock import AsyncMock, patch

from django.test import TestCase

from team_inbox.consumers import WRITE_FRAMES, TeamInboxConsumer
from tenants.models import Tenant, TenantRole, TenantUser
from users.models import User


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class ReadOnlySocketTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        suffix = uuid.uuid4().hex[:8]
        cls.tenant = Tenant.objects.create(name=f"ReadOnly-{suffix}")
        cls.operator = User.objects.create_user(username=f"op-{suffix}", email=f"op-{suffix}@x.com")
        cls.operator.is_superuser = True
        cls.operator.save(update_fields=["is_superuser"])
        cls.outsider = User.objects.create_user(username=f"out-{suffix}", email=f"out-{suffix}@x.com")

    def _consumer(self, user, read_only):
        consumer = TeamInboxConsumer()
        consumer.tenant_id = str(self.tenant.id)
        consumer.user = user
        consumer.read_only = read_only
        return consumer

    @staticmethod
    def _access(consumer):
        return TeamInboxConsumer.__dict__["check_tenant_access"].func(consumer)

    # ── who may open it ────────────────────────────────────────────────
    def test_an_operator_with_no_membership_may_open_the_inbox(self):
        """The whole point: they hold no TenantUser row in the org they view."""
        self.assertFalse(TenantUser.objects.filter(user=self.operator, tenant=self.tenant).exists())

        self.assertTrue(self._access(self._consumer(self.operator, read_only=True)))

    def test_an_ordinary_outsider_still_may_not(self):
        """The bypass is `is_superuser`, not "no membership found, allow it"."""
        self.assertFalse(self._access(self._consumer(self.outsider, read_only=False)))

    def test_a_member_is_unaffected(self):
        role = TenantRole.objects.get(tenant=self.tenant, slug="viewer")
        member = User.objects.create_user(username=f"m-{uuid.uuid4().hex[:6]}", email=f"m-{uuid.uuid4().hex[:6]}@x.com")
        TenantUser.objects.create(user=member, tenant=self.tenant, role=role)

        self.assertTrue(self._access(self._consumer(member, read_only=False)))

    # ── what it may then do ────────────────────────────────────────────
    def _receive(self, consumer, frame_type):
        consumer.send_error = AsyncMock()
        with (
            patch.object(TeamInboxConsumer, "handle_mark_as_read", new=AsyncMock()) as mark,
            patch.object(TeamInboxConsumer, "handle_typing_indicator", new=AsyncMock()) as typing,
            patch.object(TeamInboxConsumer, "handle_get_timeline", new=AsyncMock()) as timeline,
            patch.object(TeamInboxConsumer, "handle_get_chat_list", new=AsyncMock()) as chats,
        ):
            _run(consumer.receive(f'{{"type": "{frame_type}"}}'))
            return {
                "mark_as_read": mark,
                "typing_indicator": typing,
                "get_timeline": timeline,
                "get_chat_list": chats,
            }

    def test_marking_read_is_refused_and_never_reaches_whatsapp(self):
        consumer = self._consumer(self.operator, read_only=True)

        handlers = self._receive(consumer, "mark_as_read")

        handlers["mark_as_read"].assert_not_awaited()
        consumer.send_error.assert_awaited_once()
        self.assertIn("read-only", consumer.send_error.await_args.args[0].lower())

    def test_typing_is_refused_too(self):
        consumer = self._consumer(self.operator, read_only=True)

        handlers = self._receive(consumer, "typing_indicator")

        handlers["typing_indicator"].assert_not_awaited()

    def test_reading_the_conversation_is_what_still_works(self):
        for frame in ("get_timeline", "get_chat_list"):
            with self.subTest(frame=frame):
                consumer = self._consumer(self.operator, read_only=True)

                handlers = self._receive(consumer, frame)

                handlers[frame].assert_awaited_once()
                consumer.send_error.assert_not_awaited()

    def test_an_ordinary_session_may_still_mark_read(self):
        """The refusal is on the borrowed session, not on the endpoint."""
        consumer = self._consumer(self.operator, read_only=False)

        handlers = self._receive(consumer, "mark_as_read")

        handlers["mark_as_read"].assert_awaited_once()
        consumer.send_error.assert_not_awaited()

    # ── the list cannot quietly fall behind ────────────────────────────
    def test_every_frame_receive_handles_is_classified(self):
        """A frame added to `receive` must be put on one side or the other.

        `WRITE_FRAMES` is a denylist, so a new *write* frame added without
        thought would be permitted on a borrowed session and nothing would say
        so. This is what says so.
        """
        import inspect

        source = inspect.getsource(TeamInboxConsumer.receive)
        # Strip comments so prose naming a frame cannot stand in for a branch.
        code = re.sub(r"#.*", "", source)
        handled = set(re.findall(r'message_type == "([a-z_]+)"', code))

        known_reads = {"get_timeline", "get_chat_list", "client_info"}
        self.assertEqual(
            handled,
            WRITE_FRAMES | known_reads,
            "receive() handles a frame this test does not classify — decide whether a "
            "'view as organisation' session may send it, then add it here.",
        )
