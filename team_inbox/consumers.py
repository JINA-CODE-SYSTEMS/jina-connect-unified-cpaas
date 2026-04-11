"""
WebSocket consumers for team inbox real-time messaging
Supports both web and mobile clients
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.serializers.json import DjangoJSONEncoder
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import UntypedToken

from contacts.models import TenantContact
from team_inbox.models import MessageEventIds, Messages
from team_inbox.serializers import MessagesSerializer
from tenants.models import DefaultRoleSlugs, TenantUser

User = get_user_model()
logger = logging.getLogger(__name__)


class TeamInboxConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for team inbox real-time messaging
    Supports authentication via JWT token and tenant-based room isolation
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant_id = None
        self.user = None
        self.room_group_name = None
        self.client_type = "web"  # Default to web, can be 'mobile' or 'web'
        self.role_slug = None
        self.role_priority = None

    async def connect(self):
        """
        Handle WebSocket connection
        """
        try:
            # Extract tenant_id from URL
            self.tenant_id = self.scope["url_route"]["kwargs"].get("tenant_id")

            if not self.tenant_id:
                logger.error("No tenant_id provided in WebSocket URL")
                await self.close(code=4000)
                return

            # Authenticate user
            await self.authenticate_user()

            if isinstance(self.user, AnonymousUser) or not self.user:
                logger.error("Authentication failed for WebSocket connection")
                await self.close(code=4001)
                return

            # Verify user has access to tenant
            has_access = await self.check_tenant_access()
            if not has_access:
                logger.error(f"User {self.user.id} does not have access to tenant {self.tenant_id}")
                await self.close(code=4003)
                return

            # Load RBAC role info for event filtering
            await self._load_role_info()

            # Create room group name
            self.room_group_name = f"team_inbox_{self.tenant_id}"

            # Join room group
            await self.channel_layer.group_add(self.room_group_name, self.channel_name)

            # Accept WebSocket connection
            await self.accept()

            # Send connection confirmation
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "connection_established",
                        "message": "Connected to team inbox",
                        "tenant_id": self.tenant_id,
                        "user_id": self.user.id,
                        "role": self.role_slug,
                        "timestamp": datetime.now().isoformat(),
                    }
                )
            )

            logger.info(f"User {self.user.id} connected to team inbox for tenant {self.tenant_id}")

        except Exception as e:
            logger.error(f"Error in WebSocket connect: {str(e)}")
            await self.close(code=4500)

    async def disconnect(self, close_code):
        """
        Handle WebSocket disconnection
        """
        if self.room_group_name:
            # Leave room group
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

        logger.info(f"User {getattr(self.user, 'id', 'Unknown')} disconnected from team inbox (code: {close_code})")

    async def receive(self, text_data):
        """
        Handle messages received from WebSocket
        """
        try:
            data = json.loads(text_data)
            message_type = data.get("type")

            if message_type == "mark_as_read":
                await self.handle_mark_as_read(data)
            elif message_type == "get_timeline":
                await self.handle_get_timeline(data)
            elif message_type == "get_chat_list":
                await self.handle_get_chat_list(data)
            elif message_type == "typing_indicator":
                await self.handle_typing_indicator(data)
            elif message_type == "client_info":
                await self.handle_client_info(data)
            else:
                await self.send_error(f"Unknown message type: {message_type}")

        except json.JSONDecodeError:
            await self.send_error("Invalid JSON format")
        except Exception as e:
            logger.error(f"Error in receive: {str(e)}")
            await self.send_error("Internal server error")

    async def handle_client_info(self, data):
        """
        Handle client type information (mobile/web)
        """
        client_type = data.get("client_type", "web")
        if client_type in ["mobile", "web"]:
            self.client_type = client_type
            logger.info(f"Client type set to {client_type} for user {self.user.id}")

    async def handle_get_timeline(self, data):
        """
        Handle request to get timeline (messages + events) for a contact

        Expected data:
        {
            "type": "get_timeline",
            "contact_id": 123,  # Required - contact ID
            "limit": 50,  # Optional, defaults to 50
            "offset": 0  # Optional, defaults to 0
        }
        """
        try:
            limit = data.get("limit", 50)
            offset = data.get("offset", 0)
            contact_id = data.get("contact_id")

            if not contact_id:
                await self.send_error("contact_id is required for timeline")
                return

            # Ensure contact_id is an integer (may come as string from JSON)
            try:
                contact_id = int(contact_id)
            except (ValueError, TypeError):
                await self.send_error(f"Invalid contact_id format: {contact_id}")
                return

            # Verify contact exists and belongs to tenant
            contact = await self.get_contact(contact_id)
            if not contact:
                logger.warning(f"Contact {contact_id} not found for tenant {self.tenant_id}")
                await self.send_error("Contact not found or access denied")
                return

            timeline = await self.get_timeline_for_contact(contact, limit, offset)

            # Get expires_at from the most recent INCOMING message
            # WhatsApp session window is based on when the contact last messaged us,
            # not when we last messaged them. Outgoing messages don't extend the window.
            expires_at = None
            for item in timeline:
                if item.get("type") == "message" and item.get("data"):
                    msg_data = item["data"]
                    # Only use INCOMING messages for expires_at calculation
                    if msg_data.get("direction") == "INCOMING":
                        expires_at = msg_data.get("expires_at")
                        break  # Take from the most recent incoming message

            logger.debug(f"Timeline for contact {contact_id}: {len(timeline)} items")

            await self.send(
                text_data=json.dumps(
                    {
                        "type": "timeline",
                        "timeline": timeline,
                        "contact_id": contact_id,
                        "expires_at": expires_at,  # Global expires_at from most recent message
                        "limit": limit,
                        "offset": offset,
                        "timestamp": datetime.now().isoformat(),
                    },
                    cls=DjangoJSONEncoder,
                )
            )

        except Exception as e:
            logger.error(f"Error getting timeline for contact_id={data.get('contact_id')}: {str(e)}", exc_info=True)
            await self.send_error("Failed to get timeline")

    async def handle_get_chat_list(self, data):
        """
        Handle request to get chat list (unique contacts with last message preview)

        Expected data:
        {
            "type": "get_chat_list",
            "limit": 50,  # Optional, defaults to 50
            "offset": 0,  # Optional, defaults to 0
            "search": "john"  # Optional - search by name/phone
        }
        """
        try:
            limit = data.get("limit", 50)
            offset = data.get("offset", 0)
            search = data.get("search")

            chat_list = await self.get_chat_list(limit, offset, search)

            await self.send(
                text_data=json.dumps(
                    {
                        "type": "chat_list",
                        "chats": chat_list,
                        "limit": limit,
                        "offset": offset,
                        "timestamp": datetime.now().isoformat(),
                    },
                    cls=DjangoJSONEncoder,
                )
            )

        except Exception as e:
            logger.error(f"Error getting chat list: {str(e)}")
            await self.send_error("Failed to get chat list")

    async def handle_mark_as_read(self, data):
        """
        Handle marking messages as read.
        When a team member reads messages, they are marked as read for the whole team.

        Expected data:
        {
            "type": "mark_as_read",
            "message_ids": [1, 2, 3],  # List of message IDs to mark as read
            "contact_id": 123  # Optional - if provided, marks all unread messages for this contact
        }
        """
        try:
            message_ids = data.get("message_ids", [])
            contact_id = data.get("contact_id")

            # If contact_id provided, mark all unread messages for that contact
            if contact_id and not message_ids:
                marked_ids = await self.mark_contact_messages_as_read(contact_id)
            elif message_ids:
                marked_ids = await self.mark_messages_as_read(message_ids)
            else:
                await self.send_error("Either message_ids or contact_id is required")
                return

            if marked_ids:
                # Get user's name for broadcast
                reader_name = await self.get_user_full_name()

                # Broadcast read status to room group
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "messages_read",
                        "message_ids": marked_ids,
                        "contact_id": contact_id,
                        "user_id": self.user.id,
                        "user_name": reader_name,
                        "sender_channel": self.channel_name,
                    },
                )

        except Exception as e:
            logger.error(f"Error marking messages as read: {str(e)}")
            await self.send_error("Failed to mark messages as read")

    async def handle_typing_indicator(self, data):
        """
        Handle typing indicators for team inbox.
        Broadcasts to all team members when someone is typing in a conversation.

        Expected data:
        {
            "type": "typing_indicator",
            "contact_id": 123,  # Required - which conversation
            "is_typing": true,  # true when started typing, false when stopped
            "actor_type": "USER"  # Optional, defaults to USER. Can be USER or BOT
        }
        """
        try:
            is_typing = data.get("is_typing", False)
            contact_id = data.get("contact_id")
            actor_type = data.get("actor_type", "USER")

            if not contact_id:
                await self.send_error("contact_id is required for typing indicator")
                return

            # Get the typer's display name
            if actor_type == "USER":
                typer_name = await self.get_user_full_name()
                typer_id = self.user.id
            else:
                # BOT typing - bot_id should be provided
                typer_name = data.get("bot_name", "Bot")
                typer_id = data.get("bot_id")

            # Broadcast typing indicator to others in room
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "typing_indicator",
                    "contact_id": contact_id,
                    "actor_type": actor_type,
                    "actor_id": typer_id,
                    "actor_name": typer_name,
                    "is_typing": is_typing,
                    "sender_channel": self.channel_name,
                },
            )

        except Exception as e:
            logger.error(f"Error handling typing indicator: {str(e)}")

    # Group message handlers
    async def message_broadcast(self, event):
        """
        Handle message broadcast to group
        """
        # Don't send to the sender
        if event.get("sender_id") != self.user.id:
            # RBAC: Agent only receives events for assigned contacts
            if self._should_filter_by_assignment():
                contact_id = event.get("contact_id")
                if contact_id and not await self._is_assigned_to_me(contact_id):
                    return
            await self.send(
                text_data=json.dumps(
                    {"type": "new_message", "message": event["message"], "timestamp": datetime.now().isoformat()},
                    cls=DjangoJSONEncoder,
                )
            )

    async def messages_read(self, event):
        """
        Handle messages read broadcast to all team members.
        Notifies everyone when messages are marked as read.
        """
        # RBAC: Agent only receives events for assigned contacts
        if self._should_filter_by_assignment():
            contact_id = event.get("contact_id")
            if contact_id and not await self._is_assigned_to_me(contact_id):
                return
        # Send to all team members (including sender, so their UI updates)
        await self.send(
            text_data=json.dumps(
                {
                    "type": "messages_read",
                    "message_ids": event["message_ids"],
                    "contact_id": event.get("contact_id"),
                    "user_id": event["user_id"],
                    "user_name": event.get("user_name", "Unknown"),
                    "timestamp": datetime.now().isoformat(),
                }
            )
        )

    async def typing_indicator(self, event):
        """
        Handle typing indicator broadcast to team members.
        Shows who is typing in which conversation.
        """
        # Don't send typing indicator back to sender
        if event.get("sender_channel") != self.channel_name:
            # RBAC: Agent only receives events for assigned contacts
            if self._should_filter_by_assignment():
                contact_id = event.get("contact_id")
                if contact_id and not await self._is_assigned_to_me(contact_id):
                    return
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "typing_indicator",
                        "contact_id": event["contact_id"],
                        "actor_type": event["actor_type"],
                        "actor_id": event["actor_id"],
                        "actor_name": event["actor_name"],
                        "is_typing": event["is_typing"],
                        "timestamp": datetime.now().isoformat(),
                    }
                )
            )

    async def team_message(self, event):
        """
        Handle team_message broadcasts from signals (new messages, new events).
        This is called when signals broadcast messages via channel layer.
        """
        try:
            message_data = event.get("message", {})
            # RBAC: Agent only receives events for assigned contacts
            if self._should_filter_by_assignment():
                contact_id = message_data.get("contact_id")
                if contact_id and not await self._is_assigned_to_me(contact_id):
                    return
            await self.send(
                text_data=json.dumps({**message_data, "timestamp": datetime.now().isoformat()}, cls=DjangoJSONEncoder)
            )
        except Exception as e:
            logger.error(f"Error handling team_message: {str(e)}")

    async def message_status_update(self, event):
        """
        Handle message status update broadcasts (sent, delivered, read, failed).
        This is called when outgoing message status changes via webhook.

        Supports both session messages (GupshupOutgoingMessages) and broadcast messages (BroadcastMessage).

        Event structure for session messages:
        {
            'type': 'message_status_update',
            'outgoing_message_id': int,  # GupshupOutgoingMessages.pk
            'id': int,  # Messages.pk (matches 'id' in serializer)
            'message_id': int,  # MessageEventIds.pk (matches 'message_id' in serializer)
            'contact_id': int,
            'status': str,  # 'sent', 'delivered', 'read', 'failed'
            ...timestamps...
        }

        Event structure for broadcast messages:
        {
            'type': 'message_status_update',
            'broadcast_message_id': int,  # BroadcastMessage.pk
            'external_message_id': str,  # WhatsApp message ID (gs_id) for matching
            'contact_id': int,
            'status': str,
            ...timestamps...
        }
        """
        try:
            # RBAC: Agent only receives events for assigned contacts
            if self._should_filter_by_assignment():
                contact_id = event.get("contact_id")
                if contact_id and not await self._is_assigned_to_me(contact_id):
                    return
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "message_status_update",
                        # Session message identifiers
                        "outgoing_message_id": event.get("outgoing_message_id"),
                        "id": event.get("id"),  # Messages.pk
                        "message_id": event.get("message_id"),  # MessageEventIds.pk
                        # Broadcast message identifiers
                        "broadcast_message_id": event.get("broadcast_message_id"),
                        "external_message_id": event.get("external_message_id"),  # gs_id for matching
                        # Common fields
                        "contact_id": event.get("contact_id"),
                        "status": event.get("status"),
                        "outgoing_status": event.get("outgoing_status"),
                        "sent_at": event.get("sent_at"),
                        "delivered_at": event.get("delivered_at"),
                        "read_at": event.get("read_at"),
                        "failed_at": event.get("failed_at"),
                        "outgoing_sent_at": event.get("outgoing_sent_at"),
                        "outgoing_delivered_at": event.get("outgoing_delivered_at"),
                        "outgoing_read_at": event.get("outgoing_read_at"),
                        "outgoing_failed_at": event.get("outgoing_failed_at"),
                        "timestamp": datetime.now().isoformat(),
                    },
                    cls=DjangoJSONEncoder,
                )
            )
        except Exception as e:
            logger.error(f"Error handling message_status_update: {str(e)}")

    async def payment_status_update(self, event):
        """
        Handle payment status update broadcasts (BE-17).
        Called when a payment webhook updates WAOrder status.

        Event structure:
        {
            'type': 'payment_status_update',
            'event_type': 'payment_status_update',
            'reference_id': str,
            'order_id': str,
            'order_status': str,
            'payment_status': str,
            'transaction_id': str,
            'pg_transaction_id': str,
            'payment_captured_at': str | None,
            'contact_id': int | None,
        }
        """
        try:
            # RBAC: Agent only receives events for assigned contacts
            if self._should_filter_by_assignment():
                contact_id = event.get("contact_id")
                if contact_id and not await self._is_assigned_to_me(contact_id):
                    return
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "payment_status_update",
                        "reference_id": event.get("reference_id"),
                        "order_id": event.get("order_id"),
                        "order_status": event.get("order_status"),
                        "payment_status": event.get("payment_status"),
                        "transaction_id": event.get("transaction_id"),
                        "pg_transaction_id": event.get("pg_transaction_id"),
                        "payment_captured_at": event.get("payment_captured_at"),
                        "contact_id": event.get("contact_id"),
                        "timestamp": datetime.now().isoformat(),
                    },
                    cls=DjangoJSONEncoder,
                )
            )
        except Exception as e:
            logger.error(f"Error handling payment_status_update: {str(e)}")

    # ── RBAC helpers ──────────────────────────────────────────────────

    def _should_filter_by_assignment(self) -> bool:
        """
        Return True when the connected user is an AGENT — the only
        role whose WebSocket events are narrowed to assigned contacts.
        Mirrors ``_SCOPED_ROLE_SLUGS`` in abstract/viewsets/base.py.
        """
        return self.role_slug == DefaultRoleSlugs.AGENT

    @database_sync_to_async
    def _is_assigned_to_me(self, contact_id) -> bool:
        """
        Check whether the given contact is assigned to the current user.
        Returns True on missing contact_id or on lookup failure (fail-open).
        """
        if not contact_id:
            return True
        try:
            return TenantContact.objects.filter(
                id=contact_id,
                tenant_id=self.tenant_id,
                assigned_to_user=self.user,
            ).exists()
        except Exception:
            return True  # fail-open to avoid blocking events on DB errors

    @database_sync_to_async
    def _load_role_info(self):
        """
        Fetch the user's TenantRole for this tenant and store slug + priority.
        Called once during connect().
        """
        try:
            tenant_user = TenantUser.objects.select_related("role").get(
                user=self.user,
                tenant_id=self.tenant_id,
            )
            if tenant_user.role:
                self.role_slug = tenant_user.role.slug
                self.role_priority = tenant_user.role.priority
        except TenantUser.DoesNotExist:
            pass

    # Utility methods
    async def send_error(self, message: str):
        """
        Send error message to client
        """
        await self.send(
            text_data=json.dumps({"type": "error", "message": message, "timestamp": datetime.now().isoformat()})
        )

    async def authenticate_user(self):
        """
        Authenticate user from JWT token in query params or headers
        """
        # Try to get token from query parameters (mobile friendly)
        token = None
        query_string = self.scope.get("query_string", b"").decode("utf-8")

        if query_string:
            query_params = dict(param.split("=") for param in query_string.split("&") if "=" in param)
            token = query_params.get("token")

        # Try to get token from headers (web friendly)
        if not token:
            headers = dict(self.scope["headers"])
            auth_header = headers.get(b"authorization", b"").decode("utf-8")
            if auth_header.startswith("Bearer "):
                token = auth_header.split(" ", 1)[1]

        if not token:
            self.user = AnonymousUser()
            return

        try:
            # Validate JWT token
            UntypedToken(token)

            # Get user from token
            from rest_framework_simplejwt.authentication import JWTAuthentication

            jwt_auth = JWTAuthentication()
            validated_token = jwt_auth.get_validated_token(token)
            self.user = await database_sync_to_async(jwt_auth.get_user)(validated_token)

        except (InvalidToken, TokenError) as e:
            logger.error(f"JWT token validation failed: {str(e)}")
            self.user = AnonymousUser()
        except Exception as e:
            logger.error(f"Authentication error: {str(e)}")
            self.user = AnonymousUser()

    @database_sync_to_async
    def check_tenant_access(self) -> bool:
        """
        Check if user has access to the tenant
        """
        try:
            return TenantUser.objects.filter(user=self.user, tenant_id=self.tenant_id).exists()
        except Exception:
            return False

    @database_sync_to_async
    def get_contact(self, contact_id: int) -> Optional[TenantContact]:
        """
        Get contact by ID and verify it belongs to the tenant
        """
        try:
            return TenantContact.objects.get(id=contact_id, tenant_id=self.tenant_id)
        except TenantContact.DoesNotExist:
            return None
        except Exception as e:
            logger.error(f"Error getting contact: {str(e)}")
            return None

    @database_sync_to_async
    def get_timeline_for_contact(
        self, contact: TenantContact, limit: int = 50, offset: int = 0, include_date_separators: bool = True
    ):
        """
        Get unified timeline (messages + events) for a contact using the optimized manager method.
        Returns serialized timeline items with optional date separators.
        """
        try:
            # Use the optimized manager method with UNION query
            timeline = MessageEventIds.objects.get_timeline_for_contact(
                contact=contact, limit=limit, offset=offset, include_date_separators=include_date_separators
            )

            # Serialize timeline items
            serialized_timeline = []
            for item in timeline:
                if item["type"] == "date_separator":
                    # Date separator item - pass through as-is
                    serialized_timeline.append({"type": "date_separator", "date": item["date"], "label": item["label"]})
                elif item["type"] == "message":
                    serializer = MessagesSerializer(item["object"])
                    serialized_timeline.append(
                        {
                            "type": "message",
                            "numbering": item["numbering"],
                            "timestamp": item["timestamp"].isoformat(),
                            "data": serializer.data,
                        }
                    )
                elif item["type"] == "event":
                    from team_inbox.serializers import EventSerializer

                    evt_serializer = EventSerializer(item["object"])
                    serialized_timeline.append(
                        {
                            "type": "event",
                            "numbering": item["numbering"],
                            "timestamp": item["timestamp"].isoformat(),
                            "data": evt_serializer.data,
                        }
                    )

            return serialized_timeline

        except Exception as e:
            logger.error(f"Error getting timeline: {str(e)}")
            return []

    @database_sync_to_async
    def get_chat_list(self, limit: int = 50, offset: int = 0, search: str = None):
        """
        Get chat list with unique contacts and their latest message preview.
        Uses the optimized manager method.
        """
        try:
            return Messages.objects.get_chat_list(tenant_id=self.tenant_id, limit=limit, offset=offset, search=search)
        except Exception as e:
            logger.error(f"Error getting chat list: {str(e)}")
            return []

    @database_sync_to_async
    def serialize_message(self, message: Messages) -> Dict[str, Any]:
        """
        Serialize message for WebSocket transmission
        """
        try:
            serializer = MessagesSerializer(message)
            return serializer.data
        except Exception as e:
            logger.error(f"Error serializing message: {str(e)}")
            return {}

    @database_sync_to_async
    def mark_messages_as_read(self, message_ids: list) -> list:
        """
        Mark specific messages as read by the current user.
        Only marks INCOMING messages that are not already read.
        Returns list of message IDs that were actually marked as read.
        """
        try:
            from django.utils import timezone

            # Get unread incoming messages only (skip already read)
            unread_messages = Messages.objects.filter(
                id__in=message_ids, tenant_id=self.tenant_id, direction="INCOMING", is_read=False
            )

            # Get the IDs
            unread_ids = list(unread_messages.values_list("id", flat=True))

            if not unread_ids:
                # All messages are already read, nothing to do
                return []

            # Mark messages as read
            updated_count = Messages.objects.filter(id__in=unread_ids).update(
                is_read=True, read_at=timezone.now(), read_by=self.user
            )

            if updated_count > 0:
                logger.info(f"User {self.user.id} marked {updated_count} messages as read")

            return unread_ids

        except Exception as e:
            logger.error(f"Error marking messages as read: {str(e)}")
            return []

    @database_sync_to_async
    def mark_contact_messages_as_read(self, contact_id: int) -> list:
        """
        Mark all unread messages for a contact as read.
        Returns list of message IDs that were marked as read.
        """
        try:
            from django.utils import timezone

            # Get IDs of unread incoming messages for this contact
            unread_ids = list(
                Messages.objects.filter(
                    contact_id=contact_id, tenant_id=self.tenant_id, direction="INCOMING", is_read=False
                ).values_list("id", flat=True)
            )

            if unread_ids:
                # Mark them as read
                Messages.objects.filter(id__in=unread_ids).update(
                    is_read=True, read_at=timezone.now(), read_by=self.user
                )
                logger.info(f"User {self.user.id} marked {len(unread_ids)} messages as read for contact {contact_id}")

            return unread_ids

        except Exception as e:
            logger.error(f"Error marking contact messages as read: {str(e)}")
            return []

    @database_sync_to_async
    def get_user_full_name(self) -> str:
        """
        Get the current user's full name for display.
        """
        try:
            if self.user:
                full_name = self.user.get_full_name()
                if full_name:
                    return full_name
                return self.user.email or self.user.username or "Unknown User"
            return "Unknown User"
        except Exception:
            return "Unknown User"


class NotificationConsumer(AsyncWebsocketConsumer):
    """
    Consumer for user-specific notifications.
    Requires JWT authentication and validates tenant membership.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = None

    async def connect(self):
        """
        Handle notification WebSocket connection with JWT authentication.
        Rejects unauthenticated or tenant-less users with proper close codes.
        """
        try:
            # Authenticate user via JWT
            await self._authenticate_user()

            if isinstance(self.user, AnonymousUser) or not self.user:
                logger.error("Authentication failed for notification WebSocket")
                await self.close(code=4001)
                return

            # Verify user belongs to at least one tenant
            has_tenant = await self._check_has_tenant()
            if not has_tenant:
                logger.error(f"User {self.user.id} has no tenant membership")
                await self.close(code=4003)
                return

            # Accept connection
            await self.accept()

            # Join user-specific notification group
            await self.channel_layer.group_add(
                f"user_notifications_{self.user.id}",
                self.channel_name,
            )

            await self.send(
                text_data=json.dumps(
                    {
                        "type": "connection_established",
                        "message": "Connected to notifications",
                        "user_id": self.user.id,
                        "timestamp": datetime.now().isoformat(),
                    }
                )
            )

            logger.info(f"User {self.user.id} connected to notifications")

        except Exception as e:
            logger.error(f"Error in notification WebSocket connect: {str(e)}")
            await self.close(code=4500)

    async def disconnect(self, close_code):
        """
        Handle notification WebSocket disconnection.
        """
        if self.user and not isinstance(self.user, AnonymousUser):
            await self.channel_layer.group_discard(
                f"user_notifications_{self.user.id}",
                self.channel_name,
            )
        logger.info(f"User {getattr(self.user, 'id', 'Unknown')} disconnected from notifications (code: {close_code})")

    async def send_notification(self, event):
        """
        Send notification to user.
        """
        await self.send(text_data=json.dumps(event["data"]))

    # ── Auth helpers ──────────────────────────────────────────────────

    async def _authenticate_user(self):
        """
        Authenticate user from JWT token in query params or headers.
        """
        token = None
        query_string = self.scope.get("query_string", b"").decode("utf-8")

        if query_string:
            query_params = dict(param.split("=") for param in query_string.split("&") if "=" in param)
            token = query_params.get("token")

        if not token:
            headers = dict(self.scope["headers"])
            auth_header = headers.get(b"authorization", b"").decode("utf-8")
            if auth_header.startswith("Bearer "):
                token = auth_header.split(" ", 1)[1]

        if not token:
            self.user = AnonymousUser()
            return

        try:
            UntypedToken(token)
            from rest_framework_simplejwt.authentication import JWTAuthentication

            jwt_auth = JWTAuthentication()
            validated_token = jwt_auth.get_validated_token(token)
            self.user = await database_sync_to_async(jwt_auth.get_user)(validated_token)
        except (InvalidToken, TokenError) as e:
            logger.error(f"JWT validation failed for notifications: {str(e)}")
            self.user = AnonymousUser()
        except Exception as e:
            logger.error(f"Notification auth error: {str(e)}")
            self.user = AnonymousUser()

    @database_sync_to_async
    def _check_has_tenant(self) -> bool:
        """
        Verify the user is a member of at least one tenant.
        """
        try:
            return TenantUser.objects.filter(user=self.user).exists()
        except Exception:
            return False
