from django.db import models
from django.db.models import Case, CharField, F, IntegerField, Q, Value, When
from django.db.models.functions import Cast, Coalesce, Concat, Extract
from django.utils import timezone

from abstract.managers import BaseTenantModelForFilterUserManager


class MessageEventIdsQuerySet(models.QuerySet):
    """Custom QuerySet for MessageEventIds with timeline methods."""

    def for_contact(self, contact):
        """
        Get all MessageEventIds that have messages or events for the given contact.
        """
        return self.filter(Q(messages__contact=contact) | Q(events__contact=contact)).distinct()

    def for_tenant(self, tenant):
        """
        Get all MessageEventIds for a specific tenant.
        """
        return self.filter(Q(messages__tenant=tenant) | Q(events__tenant=tenant)).distinct()

    def with_related(self):
        """
        Prefetch related messages and events for efficiency.
        """
        return self.prefetch_related("messages", "events")


class MessageEventIdsManager(models.Manager):
    """Manager for MessageEventIds with timeline retrieval methods."""

    def get_queryset(self):
        return MessageEventIdsQuerySet(self.model, using=self._db)

    def for_contact(self, contact):
        return self.get_queryset().for_contact(contact)

    def for_tenant(self, tenant):
        return self.get_queryset().for_tenant(tenant)

    def with_related(self):
        return self.get_queryset().with_related()

    def get_timeline_for_contact(self, contact, limit=50, offset=0, include_date_separators=True):
        """
        Get a unified timeline of messages and events for a contact.
        Uses a single UNION query for efficiency - pagination happens at DB level.

        Args:
            contact: TenantContact instance
            limit: Maximum number of items to return
            offset: Offset for pagination
            include_date_separators: If True, includes date separator items for UI rendering

        Returns:
            List of timeline items sorted by numbering (descending).
            Includes 'date_separator' items with labels like 'Today', 'Yesterday', or date string.
        """
        from django.db import connection

        from team_inbox.models import Event, Messages

        # Use raw SQL with UNION for efficient DB-level pagination
        # This fetches only the IDs we need, then hydrates objects
        query = """
            SELECT 'message' as item_type, m.id as item_id, mei.numbering
            FROM team_inbox_messages m
            JOIN team_inbox_messageeventids mei ON m.message_id_id = mei.numbering
            WHERE m.contact_id = %s

            UNION ALL

            SELECT 'event' as item_type, e.id as item_id, mei.numbering
            FROM team_inbox_event e
            JOIN team_inbox_messageeventids mei ON e.event_id_id = mei.numbering
            WHERE e.contact_id = %s

            ORDER BY numbering DESC
            LIMIT %s OFFSET %s
        """

        with connection.cursor() as cursor:
            cursor.execute(query, [contact.id, contact.id, limit, offset])
            rows = cursor.fetchall()

        # Collect IDs by type
        message_ids = [row[1] for row in rows if row[0] == "message"]
        event_ids = [row[1] for row in rows if row[0] == "event"]

        # Fetch objects in bulk with select_related
        messages_map = {}
        if message_ids:
            messages = Messages.objects.filter(id__in=message_ids).select_related(
                "message_id", "tenant_user", "contact"
            )
            messages_map = {msg.id: msg for msg in messages}

        events_map = {}
        if event_ids:
            events = Event.objects.filter(id__in=event_ids).select_related(
                "event_id", "created_by_user", "assigned_by_user", "assigned_to_user", "contact"
            )
            events_map = {evt.id: evt for evt in events}

        # Build timeline in the correct order (from DB query)
        timeline = []
        current_date_label = None

        for item_type, item_id, numbering in rows:
            item = None
            timestamp = None

            if item_type == "message" and item_id in messages_map:
                msg = messages_map[item_id]
                timestamp = msg.timestamp
                item = {
                    "type": "message",
                    "numbering": numbering,
                    "timestamp": timestamp,
                    "object": msg,
                }
            elif item_type == "event" and item_id in events_map:
                evt = events_map[item_id]
                timestamp = evt.timestamp
                item = {
                    "type": "event",
                    "numbering": numbering,
                    "timestamp": timestamp,
                    "object": evt,
                }

            if item and timestamp:
                # Add date separator if date changed
                if include_date_separators:
                    date_label = self._get_date_label(timestamp)
                    if date_label != current_date_label:
                        # Insert date separator before this item
                        timeline.append(
                            {
                                "type": "date_separator",
                                "label": date_label,
                                "date": timestamp.date().isoformat(),
                                "timestamp": timestamp,
                            }
                        )
                        current_date_label = date_label

                timeline.append(item)

        return timeline

    def _get_date_label(self, timestamp):
        """
        Get a human-readable date label for timeline separators.

        Returns:
            - 'Today' for today's date
            - 'Yesterday' for yesterday's date
            - 'Monday', 'Tuesday', etc. for dates within last 7 days
            - 'December 6, 2025' for older dates
        """
        from datetime import timedelta

        today = timezone.now().date()
        item_date = timestamp.date()

        if item_date == today:
            return "Today"
        elif item_date == today - timedelta(days=1):
            return "Yesterday"
        elif item_date > today - timedelta(days=7):
            # Within last week - show day name
            return item_date.strftime("%A")  # e.g., "Monday"
        else:
            # Older - show full date
            return item_date.strftime("%B %d, %Y")  # e.g., "December 6, 2025"


class MessagesQuerySet(models.QuerySet):
    """
    Custom QuerySet for Messages model with annotation methods.
    """

    def with_created_by(self):
        """
        Annotate queryset with 'created_by_name' field based on author type.
        - USER: Returns tenant_user's full name
        - CONTACT: Returns contact's full name
        - BOT: Returns 'Bot'
        """
        return self.annotate(
            created_by_name=Case(
                When(
                    author="USER",
                    then=Coalesce(
                        Concat(
                            F("tenant_user__first_name"),
                            Value(" "),
                            F("tenant_user__last_name"),
                        ),
                        Value("Unknown User"),
                    ),
                ),
                When(
                    author="CONTACT",
                    then=Coalesce(
                        Concat(
                            F("contact__first_name"),
                            Value(" "),
                            F("contact__last_name"),
                        ),
                        Value("Unknown Contact"),
                    ),
                ),
                When(author="BOT", then=Value("Bot")),
                default=Value("Unknown"),
                output_field=CharField(),
            )
        )

    def with_expires_at(self):
        """
        Annotate queryset with 'expires_at_timestamp' field.
        - WHATSAPP: Returns timestamp + 24 hours (as Unix timestamp)
        - Other platforms: Returns None
        """
        # 24 hours in seconds
        twenty_four_hours = 24 * 60 * 60

        return self.annotate(
            expires_at_timestamp=Case(
                When(
                    platform="WHATSAPP",
                    then=Cast(Extract("timestamp", "epoch") + Value(twenty_four_hours), output_field=IntegerField()),
                ),
                default=Value(None),
                output_field=IntegerField(),
            )
        )

    def with_message_annotations(self):
        """
        Convenience method to add both created_by and expires_at annotations.
        """
        return self.with_created_by().with_expires_at()


class MessagesManager(BaseTenantModelForFilterUserManager):
    """
    Custom manager for Messages model that uses MessagesQuerySet.
    """

    def get_queryset(self):
        return MessagesQuerySet(self.model, using=self._db)

    def with_created_by(self):
        return self.get_queryset().with_created_by()

    def with_expires_at(self):
        return self.get_queryset().with_expires_at()

    def with_message_annotations(self):
        return self.get_queryset().with_message_annotations()

    def get_chat_list(self, tenant_id, limit=50, offset=0, search=None):
        """
        Get chat list with unique contacts and their latest message.
        Uses efficient DB-level query with subquery for latest message per contact.
        Includes unread message count for each contact.

        Args:
            tenant_id: Tenant ID to filter by
            limit: Maximum number of chats to return
            offset: Offset for pagination
            search: Optional search string to filter contacts by name/phone

        Returns:
            List of dicts with contact info, last message preview, unread count,
            status, platform, assignment data, and author_type
        """
        from django.db import connection

        # Build the query - get latest message per contact using window function
        # Includes tenant_user info for created_by calculation and unread count
        # Also includes contact status and assignment info
        # Added: last_incoming_timestamp for correct WhatsApp session expiry calculation
        query = """
            WITH latest_messages AS (
                SELECT
                    m.id,
                    m.contact_id,
                    m.content,
                    m.timestamp,
                    m.direction,
                    m.author,
                    m.platform,
                    m.tenant_user_id,
                    m.is_read,
                    ROW_NUMBER() OVER (PARTITION BY m.contact_id ORDER BY m.timestamp DESC) as rn
                FROM team_inbox_messages m
                WHERE m.tenant_id = %s
            ),
            unread_counts AS (
                SELECT
                    contact_id,
                    COUNT(*) as unread_count
                FROM team_inbox_messages
                WHERE tenant_id = %s
                    AND direction = 'INCOMING'
                    AND is_read = FALSE
                GROUP BY contact_id
            ),
            last_incoming AS (
                SELECT
                    contact_id,
                    MAX(timestamp) as last_incoming_timestamp
                FROM team_inbox_messages
                WHERE tenant_id = %s
                    AND direction = 'INCOMING'
                GROUP BY contact_id
            )
            SELECT
                lm.id as message_id,
                lm.contact_id,
                lm.content,
                lm.timestamp,
                lm.direction,
                lm.author,
                lm.platform,
                lm.tenant_user_id,
                lm.is_read,
                c.first_name as contact_first_name,
                c.last_name as contact_last_name,
                c.phone,
                c.status as contact_status,
                c.assigned_to_type,
                c.assigned_to_id,
                c.assigned_to_user_id,
                u.first_name as user_first_name,
                u.last_name as user_last_name,
                au.first_name as assigned_user_first_name,
                au.last_name as assigned_user_last_name,
                au.email as assigned_user_email,
                COALESCE(uc.unread_count, 0) as unread_count,
                li.last_incoming_timestamp
            FROM latest_messages lm
            JOIN contacts_tenantcontact c ON c.id = lm.contact_id
            LEFT JOIN users_user u ON u.id = lm.tenant_user_id
            LEFT JOIN users_user au ON au.id = c.assigned_to_user_id
            LEFT JOIN unread_counts uc ON uc.contact_id = lm.contact_id
            LEFT JOIN last_incoming li ON li.contact_id = lm.contact_id
            WHERE lm.rn = 1
        """

        params = [tenant_id, tenant_id, tenant_id]  # tenant_id used three times now

        # Add search filter if provided
        if search:
            query += """
                AND (
                    LOWER(c.first_name) LIKE LOWER(%s)
                    OR LOWER(c.last_name) LIKE LOWER(%s)
                    OR c.phone LIKE %s
                )
            """
            search_pattern = f"%{search}%"
            params.extend([search_pattern, search_pattern, search_pattern])

        query += """
            ORDER BY lm.timestamp DESC
            LIMIT %s OFFSET %s
        """
        params.extend([limit, offset])

        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        # Build chat list with message previews
        chat_list = []
        for row in rows:
            (
                message_id,
                contact_id,
                content,
                timestamp,
                direction,
                author,
                platform,
                tenant_user_id,
                is_read,
                contact_first_name,
                contact_last_name,
                phone,
                contact_status,
                assigned_to_type,
                assigned_to_id,
                assigned_to_user_id,
                user_first_name,
                user_last_name,
                assigned_user_first_name,
                assigned_user_last_name,
                assigned_user_email,
                unread_count,
                last_incoming_timestamp,
            ) = row

            # Generate message preview from content
            preview = self._get_message_preview(content)

            # Calculate created_by based on author
            created_by = self._get_created_by(
                author, user_first_name, user_last_name, contact_first_name, contact_last_name, phone
            )

            # Calculate expires_at (24 hours from LAST INCOMING message for WhatsApp)
            # WhatsApp session window is based on when the contact last messaged us,
            # not when we last messaged them. Outgoing messages don't extend the window.
            expires_at = None
            if platform == "WHATSAPP" and last_incoming_timestamp:
                expires_at = int(last_incoming_timestamp.timestamp()) + (24 * 60 * 60)

            # Build assigned_to_name
            assigned_to_name = self._get_assigned_to_name(
                assigned_to_type, assigned_to_id, assigned_user_first_name, assigned_user_last_name, assigned_user_email
            )

            chat_list.append(
                {
                    "contact": {
                        "id": contact_id,
                        "first_name": contact_first_name or "",
                        "last_name": contact_last_name or "",
                        "full_name": f"{contact_first_name or ''} {contact_last_name or ''}".strip() or str(phone),
                        "phone": str(phone),
                    },
                    "status": contact_status or "OPEN",
                    "platform": platform,
                    "assignment": {
                        "assigned_to_type": assigned_to_type or "UNASSIGNED",
                        "assigned_to_id": assigned_to_id,
                        "assigned_to_name": assigned_to_name,
                    },
                    "last_message": {
                        "id": message_id,
                        "preview": preview["text"],
                        "preview_type": preview["type"],
                        "timestamp": timestamp.isoformat() if timestamp else None,
                        "direction": direction,
                        "author_type": author,
                        "platform": platform,
                        "created_by": created_by,
                        "expires_at": expires_at,
                        "is_read": is_read,
                    },
                    "unread_count": unread_count,
                }
            )

        return chat_list

    def _get_assigned_to_name(self, assigned_to_type, assigned_to_id, first_name, last_name, email):
        """
        Get the display name for who the contact is assigned to.
        """
        if assigned_to_type == "USER":
            if first_name or last_name:
                return f"{first_name or ''} {last_name or ''}".strip()
            if email:
                return email
            return "Unknown User"
        elif assigned_to_type == "BOT":
            # TODO: Fetch bot name from Bot model when available
            return f"Bot #{assigned_to_id}" if assigned_to_id else "Bot"
        elif assigned_to_type == "CHATFLOW":
            # Fetch ChatFlow name from database
            if assigned_to_id:
                try:
                    from chat_flow.models import ChatFlow

                    flow = ChatFlow.objects.filter(pk=assigned_to_id).values("name").first()
                    if flow:
                        return flow["name"]
                except Exception:
                    pass
            return f"ChatFlow #{assigned_to_id}" if assigned_to_id else "ChatFlow"
        return "Unassigned"

    def _get_created_by(self, author, user_first_name, user_last_name, contact_first_name, contact_last_name, phone):
        """
        Get the created_by display name based on author type.
        """
        if author == "USER":
            if user_first_name or user_last_name:
                return f"{user_first_name or ''} {user_last_name or ''}".strip()
            return "Unknown User"
        elif author == "CONTACT":
            if contact_first_name or contact_last_name:
                return f"{contact_first_name or ''} {contact_last_name or ''}".strip()
            return str(phone) if phone else "Unknown Contact"
        elif author == "BOT":
            return "Bot"
        return "Unknown"

    def _get_message_preview(self, content, max_length=50):
        """
        Generate a preview string from message content.

        Returns:
            dict with 'type' and 'text' keys
            - text: "Hello, how are you..." (truncated)
            - image: "📷 Photo" or "📷 caption..."
            - video: "🎥 Video" or "🎥 caption..."
            - audio: "🎵 Audio"
            - document: "📄 Document" or "📄 filename"
            - cards: "📋 Card message"
        """
        import json

        # Handle content that might come as JSON string from raw SQL
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                return {"type": "unknown", "text": ""}

        if not content or not isinstance(content, dict):
            return {"type": "unknown", "text": ""}

        content_type = content.get("type", "text")

        if content_type == "text":
            # body is an object: {"text": "message"}
            body = content.get("body", {})
            text = body.get("text", "") if isinstance(body, dict) else str(body)
            if len(text) > max_length:
                text = text[:max_length].rstrip() + "..."
            return {"type": "text", "text": text}

        elif content_type == "image":
            # image is an object: {"url": "...", "caption": "..."}
            image = content.get("image", {})
            caption = image.get("caption", "") if isinstance(image, dict) else ""
            if caption:
                if len(caption) > max_length - 3:  # Account for emoji + space
                    caption = caption[: max_length - 6].rstrip() + "..."
                return {"type": "image", "text": f"📷 {caption}"}
            return {"type": "image", "text": "📷 Photo"}

        elif content_type == "video":
            # video is an object: {"url": "...", "caption": "..."}
            video = content.get("video", {})
            caption = video.get("caption", "") if isinstance(video, dict) else ""
            if caption:
                if len(caption) > max_length - 3:
                    caption = caption[: max_length - 6].rstrip() + "..."
                return {"type": "video", "text": f"🎥 {caption}"}
            return {"type": "video", "text": "🎥 Video"}

        elif content_type == "audio":
            return {"type": "audio", "text": "🎵 Audio"}

        elif content_type == "document":
            # document is an object: {"url": "...", "filename": "...", "caption": "..."}
            document = content.get("document", {})
            filename = document.get("filename", "") if isinstance(document, dict) else ""
            if filename:
                if len(filename) > max_length - 3:
                    filename = filename[: max_length - 6].rstrip() + "..."
                return {"type": "document", "text": f"📄 {filename}"}
            return {"type": "document", "text": "📄 Document"}

        elif content_type == "cards":
            cards = content.get("cards", [])
            if cards and len(cards) > 0:
                # Get first card's title or body if available
                first_card = cards[0]
                body = first_card.get("body", {})
                title = body.get("text", "") if isinstance(body, dict) else ""
                if title:
                    if len(title) > max_length - 3:
                        title = title[: max_length - 6].rstrip() + "..."
                    return {"type": "cards", "text": f"📋 {title}"}
            return {"type": "cards", "text": "📋 Card message"}

        return {"type": content_type, "text": f"[{content_type}]"}
