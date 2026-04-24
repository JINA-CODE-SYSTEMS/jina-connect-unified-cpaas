import logging

from djmoney.contrib.django_rest_framework import MoneyField
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from abstract.serializers import BaseSerializer
from broadcast.models import Broadcast, BroadcastMessage, BroadcastStatusChoices

logger = logging.getLogger(__name__)


class BroadcastSerializer(BaseSerializer):
    # UI-friendly status
    ui_status = serializers.CharField(read_only=True)
    ui_status_display = serializers.CharField(read_only=True)

    # Message status counts
    pending_count = serializers.SerializerMethodField()
    queued_count = serializers.SerializerMethodField()
    sending_count = serializers.SerializerMethodField()
    sent_count = serializers.SerializerMethodField()
    delivered_count = serializers.SerializerMethodField()
    read_count = serializers.SerializerMethodField()
    failed_count = serializers.SerializerMethodField()
    blocked_count = serializers.SerializerMethodField()
    total_messages = serializers.SerializerMethodField()
    success_count = serializers.SerializerMethodField()
    initial_cost = MoneyField(max_digits=14, decimal_places=2, default_currency="USD", read_only=True)
    refund_amount = MoneyField(max_digits=14, decimal_places=2, default_currency="USD", read_only=True)

    # Server-side select-all support (#412)
    select_all = serializers.BooleanField(write_only=True, default=False, required=False)
    tag_filter = serializers.ListField(child=serializers.CharField(), write_only=True, default=list, required=False)

    class Meta:
        model = Broadcast
        fields = "__all__"
        extra_kwargs = {
            "tenant": {"required": False},  # Set by viewset in perform_create()
            "created_by": {"required": False},  # Set by viewset in perform_create()
            "platform": {"required": False},  # Set by viewset in perform_create()
        }

    def get_pending_count(self, obj):
        return getattr(obj, "pending_count", 0)

    def get_queued_count(self, obj):
        return getattr(obj, "queued_count", 0)

    def get_sending_count(self, obj):
        return getattr(obj, "sending_count", 0)

    def get_sent_count(self, obj):
        return getattr(obj, "sent_count", 0)

    def get_delivered_count(self, obj):
        return getattr(obj, "delivered_count", 0)

    def get_read_count(self, obj):
        return getattr(obj, "read_count", 0)

    def get_failed_count(self, obj):
        return getattr(obj, "failed_count", 0)

    def get_blocked_count(self, obj):
        return getattr(obj, "blocked_count", 0)

    def get_total_messages(self, obj):
        return getattr(obj, "total_messages", 0)

    def get_success_count(self, obj):
        return getattr(obj, "success_count", 0)

    def to_representation(self, instance):
        representation = super().to_representation(instance)

        # Add template-related fields
        template = None
        if instance.template_number and hasattr(instance.template_number, "gupshup_template"):
            template = instance.template_number.gupshup_template

        # template_type
        representation["template_type"] = template.template_type if template else None

        # template_media_url
        template_media_url = None
        if template and template.tenant_media:
            request = self.context.get("request")
            if request:
                template_media_url = request.build_absolute_uri(template.tenant_media.media.url)
            else:
                template_media_url = template.tenant_media.media.url
        representation["template_media_url"] = template_media_url

        # cards_media_urls
        cards_media_urls = []
        if template and template.card_media:
            request = self.context.get("request")
            for media in template.card_media.all():
                if request:
                    cards_media_urls.append(request.build_absolute_uri(media.media.url))
                else:
                    cards_media_urls.append(media.media.url)
        representation["cards_media_urls"] = cards_media_urls

        return representation

    def create(self, validated_data):
        # Pop select_all fields — not model fields (#412)
        select_all = validated_data.pop("select_all", False)
        tag_filter = validated_data.pop("tag_filter", [])

        status = validated_data.get("status", BroadcastStatusChoices.DRAFT)
        logger.debug(f"[SERIALIZER CREATE] status from validated_data: {status!r}")
        logger.debug(f"[SERIALIZER CREATE] validated_data keys: {list(validated_data.keys())}")
        logger.debug(
            f"[SERIALIZER CREATE] template_number in validated_data: {validated_data.get('template_number')!r}"
        )
        logger.debug(f"[SERIALIZER CREATE] QUEUED constant: {BroadcastStatusChoices.QUEUED!r}")
        logger.debug(f"[SERIALIZER CREATE] status == QUEUED: {status == BroadcastStatusChoices.QUEUED}")
        logger.debug(f"[SERIALIZER CREATE] select_all={select_all}, tag_filter={tag_filter}")
        if status == BroadcastStatusChoices.QUEUED:
            # Validate template_number is set for QUEUED broadcasts (WhatsApp only)
            # Telegram, SMS, RCS use placeholder_data directly without templates
            template_number = validated_data.get("template_number")
            platform = validated_data.get("platform")

            # Only require template for WhatsApp
            from broadcast.models import BroadcastPlatformChoices

            if platform == BroadcastPlatformChoices.WHATSAPP and not template_number:
                from rest_framework.exceptions import ValidationError

                raise ValidationError({"template_number": "A template is required for WhatsApp broadcasts."})

            validated_data.pop("status")
            scheduled_time = validated_data.pop("scheduled_time", None)
            logger.debug(f"[SERIALIZER CREATE] Popped status & scheduled_time={scheduled_time}. Creating as DRAFT...")
            # Create as DRAFT first — DRF will set M2M recipients after
            # create() returns.  We store the desired status/time and apply
            # them in save() after M2M is attached.
            instance: Broadcast = super().create(validated_data)
            # Stash for save() to pick up after M2M is set
            instance._deferred_queue_status = True
            instance._deferred_scheduled_time = scheduled_time
            if select_all:
                instance._select_all = True
                instance._tag_filter = tag_filter
            logger.debug(f"[SERIALIZER CREATE] Instance {instance.pk} created as DRAFT, _deferred_queue_status=True")
            return instance
        elif status == BroadcastStatusChoices.DRAFT:
            logger.debug("[SERIALIZER CREATE] Creating as DRAFT (no defer needed)")
            instance = super().create(validated_data)
            if select_all:
                instance._select_all = True
                instance._tag_filter = tag_filter
            return instance
        else:
            raise ValueError(f"Invalid status '{status}' for Broadcast creation. Only 'DRAFT' or 'QUEUED' allowed.")

    def save(self, **kwargs):
        """
        Override save to handle deferred QUEUED scheduling.
        DRF's save() calls create(), then sets M2M fields, then returns.
        We hook in here so scheduling happens AFTER recipients are attached.
        """
        logger.debug(f"[SERIALIZER SAVE] Called with kwargs={kwargs}")
        instance = super().save(**kwargs)
        logger.debug(f"[SERIALIZER SAVE] super().save() returned instance {instance.pk}, status={instance.status}")

        # Handle select_all: resolve and attach recipients before deferred queue check (#412)
        if getattr(instance, "_select_all", False):
            from contacts.models import TenantContact

            qs = TenantContact.objects.filter(tenant=instance.tenant, phone__isnull=False).exclude(phone="")
            tag_filter = getattr(instance, "_tag_filter", [])
            if tag_filter:
                qs = qs.filter(tag__in=tag_filter)
            instance.recipients.set(qs)
            logger.debug(f"[SERIALIZER SAVE] select_all: resolved {instance.recipients.count()} recipients")
            del instance._select_all
            if hasattr(instance, "_tag_filter"):
                del instance._tag_filter

        has_deferred = getattr(instance, "_deferred_queue_status", False)
        logger.debug("[SERIALIZER SAVE] _deferred_queue_status=%s", has_deferred)
        if has_deferred:
            recipients_exist = instance.recipients.exists()
            template_set = bool(instance.template_number)
            logger.debug(f"[SERIALIZER SAVE] recipients.exists()={recipients_exist}, template_number={template_set}")
            instance.status = BroadcastStatusChoices.QUEUED
            scheduled_time = getattr(instance, "_deferred_scheduled_time", None)
            if scheduled_time:
                instance.scheduled_time = scheduled_time
            else:
                instance.scheduled_time = instance._default_scheduled_time
            logger.debug(f"[SERIALIZER SAVE] Set status=QUEUED, scheduled_time={instance.scheduled_time}")
            logger.debug(f"[SERIALIZER SAVE] good_to_send={instance.good_to_send}")
            logger.debug("[SERIALIZER SAVE] Calling instance.save(update_fields=['status', 'scheduled_time'])...")
            instance.save(update_fields=["status", "scheduled_time"])
            logger.debug(
                f"[SERIALIZER SAVE] instance.save() completed. DB status now: {Broadcast.objects.values_list('status', flat=True).get(pk=instance.pk)}"
            )
            del instance._deferred_queue_status
            if hasattr(instance, "_deferred_scheduled_time"):
                del instance._deferred_scheduled_time
        return instance

    def validate_media_overrides(self, value):
        """Validate carousel card count in media_overrides (BE-14, #114)."""
        if not value or not isinstance(value, dict):
            return value
        cards = value.get("cards")
        if cards and isinstance(cards, list):
            count = len(cards)
            if count < 2:
                raise ValidationError("Carousel requires at least 2 cards.")
            if count > 10:
                raise ValidationError("Carousel supports a maximum of 10 cards.")
        return value

    def update(self, instance, validated_data):
        # Pop select_all fields — not model fields (#412)
        validated_data.pop("select_all", False)
        validated_data.pop("tag_filter", [])
        instance: Broadcast = instance
        if instance.status not in instance.allowed_status_for_update:
            raise ValidationError({"status": f"Cannot update broadcast in '{instance.get_status_display()}' status."})
        return super().update(instance, validated_data)


class BroadcastLimitedSerializer(BroadcastSerializer):
    """
    Limited broadcast serializer — hides financial fields (initial_cost, refund_amount).
    Ticket #251: AGENT cannot see cost/refund data on broadcasts.
    MANAGER+ (priority >= 60) gets the full BroadcastSerializer.
    """

    # Disable the parent's declared MoneyField attributes so DRF doesn't
    # complain about them being in both declared_fields and exclude.
    initial_cost = None
    refund_amount = None

    class Meta(BroadcastSerializer.Meta):
        exclude = ["initial_cost", "initial_cost_currency", "refund_amount", "refund_amount_currency"]
        fields = None  # override parent's __all__


class BroadcastMessageSerializer(BaseSerializer):
    """Serializer for BroadcastMessage model"""

    contact_name = serializers.CharField(read_only=True, source="contact.name")
    contact_phone = serializers.CharField(read_only=True, source="contact.phone")

    class Meta:
        model = BroadcastMessage
        fields = "__all__"
