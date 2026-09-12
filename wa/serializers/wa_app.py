"""
WAApp Serializers (v2)

Serializers for WhatsApp Business App configuration.
"""

from drf_yasg import openapi
from rest_framework import serializers

from abstract.serializers import BaseSerializer
from wa.models import BSPChoices, WAApp


def reject_app_secret_in_bsp_credentials(value):
    """Refuse to let a META app secret come to rest in the plaintext JSON (#311).

    ``bsp_credentials`` is documented as non-secret configuration, and the model
    moves the two keys older clients do send — ``access_token`` and
    ``partner_app_token`` — into their encrypted columns on save. An app secret
    posted there would match neither, so it would simply stay in the plain
    column, in every backup and on every replica.

    No client can be sending it that way today (the field is new in #311), so
    nothing is broken by refusing it, and the error names the field that does
    accept it. Shared with ``tenants.serializers.TenantGupshupAppsSerializer``,
    because both endpoints write this column and a guard on only one of them
    would just tell a caller which endpoint to use instead.
    """
    if not isinstance(value, dict):
        return value

    offered = sorted(key for key in value if "app_secret" in str(key).lower())
    if offered:
        raise serializers.ValidationError(
            f"{', '.join(offered)} cannot be set here — bsp_credentials is a plaintext column. "
            "Send the META app secret as the write-only meta_app_secret field instead, which is "
            "stored encrypted."
        )
    return value


class WAAppListSerializer(BaseSerializer):
    """
    Minimal serializer for WA App list views.

    Used for efficient list endpoints with only essential fields.
    """

    phone_number = serializers.CharField(source="wa_number", read_only=True)

    class Meta:
        model = WAApp
        fields = [
            "id",
            "app_name",
            "phone_number",
            "bsp",
            "is_active",
            "is_verified",
        ]
        swagger_schema_fields = {
            "type": openapi.TYPE_OBJECT,
            "title": "WAAppList",
            "description": "Minimal WhatsApp Business App for list views",
        }


class WAAppSerializer(BaseSerializer):
    """
    Full serializer for WhatsApp Business App configuration.

    Handles CRUD operations for WA App entities including:
    - Phone number and WABA configuration
    - BSP credentials management
    - Quota and tier tracking
    """

    phone_number = serializers.CharField(
        source="wa_number", help_text="WhatsApp phone number with country code (e.g., +919876543210)"
    )
    bsp_display = serializers.CharField(source="get_bsp_display", read_only=True, help_text="Human-readable BSP name")

    # The client's own META app secret (#311). Declared here rather than left to
    # ``extra_kwargs`` on a model field, because ``write_only`` then belongs to
    # the field itself: it cannot be undone by a ``fields = "__all__"`` further
    # down an inheritance chain or by an ``extra_kwargs`` dict that gets
    # rebuilt, which is exactly how the token was once exposed.
    #
    # This is the intake route for the secret, in place of a
    # ``bsp_credentials`` key: the JSON column is plaintext, no client has ever
    # sent an app secret inside it, and adding a key would have created a
    # plaintext path rather than preserved one. See ``_BSP_SECRET_FIELDS`` in
    # ``tenants/models.py``.
    #
    # ``allow_blank=False``: rotating a secret means sending the new one, and an
    # empty string is far more often a form that submitted nothing than a
    # deliberate "forget the key that verifies my webhooks". It is rejected
    # rather than quietly interpreted either way.
    meta_app_secret = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=False,
        trim_whitespace=True,
        help_text=(
            "The client's own META app secret, used to verify X-Hub-Signature-256 on their webhook "
            "deliveries. Not the same as app_secret, which is the Gupshup one. Write-only: it is "
            "stored encrypted and never returned. Omit to leave the stored value unchanged."
        ),
    )

    # Not a model field: a request for the live check in
    # ``wa.services.meta_preflight``, usable on create and on a credential
    # update. Opt-in rather than implicit, because it makes the request wait on
    # Graph — the on-demand ``preflight`` action is the other way to ask.
    verify_with_meta = serializers.BooleanField(
        write_only=True,
        required=False,
        default=False,
        help_text=(
            "Check the META credentials against META before saving: that the token can read the WABA, "
            "that phone_number_id is one of its numbers, and that the WABA is subscribed to the app. "
            "Failures come back as field errors."
        ),
    )

    class Meta:
        model = WAApp
        fields = [
            "id",
            "tenant",
            "app_name",
            "description",
            "phone_number",
            "waba_id",
            "phone_number_id",
            "bsp",
            "bsp_display",
            "app_id",
            "meta_app_id",
            "bsp_credentials",
            "bsp_access_token",
            "bsp_partner_app_token",
            "meta_app_secret",
            "verify_with_meta",
            "is_active",
            "is_verified",
            "daily_limit",
            "messages_sent_today",
            "tier",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "messages_sent_today",
            "is_verified",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            # Write-only: a live access token must never come back out of the
            # API. The entry was here before #275 but ``bsp_credentials`` was
            # missing from ``fields``, so DRF ignored it and the per-tenant
            # token could not be set at all — every send fell back to the one
            # global ``META_PERM_TOKEN``.
            #
            # The token now lives in its own encrypted column (#289), settable
            # directly as ``bsp_access_token``. ``bsp_credentials`` stays
            # writable because clients already send the token inside it; the
            # model moves it out of the JSON on save.
            "bsp_credentials": {"write_only": True},
            "bsp_access_token": {"write_only": True},
            "bsp_partner_app_token": {"write_only": True},
            "meta_app_id": {"help_text": "META App ID — used for the Resumable Upload API"},
            "waba_id": {"help_text": "WhatsApp Business Account ID from META"},
            "bsp": {"help_text": "Business Solution Provider (META, GUPSHUP, etc.)"},
        }
        swagger_schema_fields = {
            "type": openapi.TYPE_OBJECT,
            "title": "WAApp",
            "description": "WhatsApp Business App configuration",
            "properties": {
                "id": openapi.Schema(type=openapi.TYPE_INTEGER),
                "app_name": openapi.Schema(type=openapi.TYPE_STRING),
                "phone_number": openapi.Schema(type=openapi.TYPE_STRING),
                "bsp": openapi.Schema(type=openapi.TYPE_STRING, enum=["META", "GUPSHUP", "TWILIO", "MESSAGEBIRD"]),
                "is_active": openapi.Schema(type=openapi.TYPE_BOOLEAN),
                "is_verified": openapi.Schema(type=openapi.TYPE_BOOLEAN),
                "daily_limit": openapi.Schema(type=openapi.TYPE_INTEGER),
            },
        }

    def validate_wa_number(self, value):
        """
        Validate phone number format.
        """
        import re

        if not value:
            raise serializers.ValidationError("Phone number is required")

        # Remove spaces and dashes for validation
        cleaned = re.sub(r"[\s\-]", "", value)

        # Should start with + and contain only digits after
        if not re.match(r"^\+\d{10,15}$", cleaned):
            raise serializers.ValidationError("Phone number must be in E.164 format (e.g., +919876543210)")

        return cleaned

    def validate_bsp_credentials(self, value):
        """See :func:`reject_app_secret_in_bsp_credentials`."""
        return reject_app_secret_in_bsp_credentials(value)

    def validate(self, data):
        """Run the on-request META preflight, if the request asked for one.

        ``verify_with_meta`` is not a model field and must not reach ``create``
        or ``update``, so it is consumed here. It is honoured on both, because
        replacing credentials is a PATCH and the point of replacing them is to
        find out whether the new ones work.
        """
        data = super().validate(data)

        if not data.pop("verify_with_meta", False):
            return data

        from wa.services import meta_preflight

        report = meta_preflight.run_meta_preflight(self._build_preflight_subject(data))
        if not report.ok:
            # One entry per failing check, keyed on the field to look at. A
            # passing run says nothing extra here — the detail of *what* passed
            # is what the ``preflight`` action on the viewset is for.
            raise serializers.ValidationError(report.as_field_errors())

        return data

    def _build_preflight_subject(self, data):
        """What to preflight: the values being sent, over the values stored.

        A PATCH that sends only a new token must be checked against the WABA and
        number already on the row — re-entering unchanged credentials to verify
        the changed one is exactly the friction the acceptance criteria rule out.
        """
        from wa.services.meta_preflight import PreflightSubject

        def resolved(name):
            sent = data.get(name) or ""
            if sent:
                return str(sent)
            stored = getattr(self.instance, name, "") or "" if self.instance is not None else ""
            return str(stored)

        # The token has one more place to come from: inside ``bsp_credentials``,
        # which is still how clients send it (#275). A value in the request wins
        # over the stored one either way — rotating a token and verifying the
        # rotation are the same request.
        token = str(data.get("bsp_access_token") or "")
        if not token:
            credentials = data.get("bsp_credentials")
            if isinstance(credentials, dict):
                token = str(credentials.get("access_token") or "")
        if not token and self.instance is not None:
            token = str(getattr(self.instance, "bsp_access_token", "") or "")

        return PreflightSubject(
            waba_id=resolved("waba_id"),
            phone_number_id=resolved("phone_number_id"),
            meta_app_id=resolved("meta_app_id"),
            bsp_access_token=token,
        )


class WAAppSafeSerializer(BaseSerializer):
    """
    Safe serializer for WA App — hides BSP identifiers (app_id, waba_id, phone_number_id).
    Ticket #251: MANAGER and below get this instead of WAAppSerializer.
    """

    phone_number = serializers.CharField(source="wa_number", help_text="WhatsApp phone number with country code")
    bsp_display = serializers.CharField(source="get_bsp_display", read_only=True, help_text="Human-readable BSP name")

    class Meta:
        model = WAApp
        fields = [
            "id",
            "tenant",
            "app_name",
            "description",
            "phone_number",
            "bsp",
            "bsp_display",
            "is_active",
            "is_verified",
            "daily_limit",
            "messages_sent_today",
            "tier",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "messages_sent_today",
            "is_verified",
            "created_at",
            "updated_at",
        ]


class MetaIdentifiersRequiredMixin:
    """Refuse to create a META app that cannot send or receive (#311).

    ``waba_id`` and ``phone_number_id`` are not optional for META: without them
    every send raises and no webhook can be routed. The check itself has existed
    since the create serializer was written — it was simply never reached,
    because ``WAAppViewSet.get_serializer_class`` chose between the list, full
    and safe serializers and never the create one, so ``POST /wa/v2/apps/``
    accepted a META app with neither identifier and the failure surfaced much
    later and far from its cause.

    Mixed in rather than inherited from one create serializer because there are
    two create paths, one per privilege level (#251), and the rule is the same
    on both: a half-configured app is not worth creating either way.
    """

    def validate(self, data):
        # Before ``super()``, not after: the parent may spend a round trip to
        # META on this data, and there is nothing to ask META about an app whose
        # identifiers are missing outright.
        if self._effective_bsp(data) == BSPChoices.META:
            self._validate_meta_identifiers(data)

        return super().validate(data)

    def _effective_bsp(self, data):
        """The BSP the app will actually have once saved.

        ``bsp`` defaults to META on the model, so an omitted ``bsp`` produces a
        META app — and it has to be validated as one. Reading the field default
        rather than hard-coding it keeps this honest if the default moves; a test
        already pins ``bsp``'s default against ``wa.adapters.DEFAULT_BSP``.
        """
        if data.get("bsp"):
            return data["bsp"]
        return WAApp._meta.get_field("bsp").default

    def _validate_meta_identifiers(self, data):
        if not data.get("waba_id"):
            raise serializers.ValidationError({"waba_id": "WABA ID is required for META BSP"})
        if not data.get("phone_number_id"):
            raise serializers.ValidationError({"phone_number_id": "Phone Number ID is required for META BSP"})


class WAAppCreateSerializer(MetaIdentifiersRequiredMixin, WAAppSerializer):
    """
    Serializer for creating new WA Apps.

    Includes additional validation for required fields during creation.
    """

    class Meta(WAAppSerializer.Meta):
        extra_kwargs = {
            **WAAppSerializer.Meta.extra_kwargs,
            "app_name": {"required": True},
            "wa_number": {"required": True},
            "bsp": {"required": True},
        }


class WAAppSafeCreateSerializer(MetaIdentifiersRequiredMixin, WAAppSafeSerializer):
    """Create, for a role that may manage apps but may not see BSP identifiers.

    Same field surface as ``WAAppSafeSerializer`` — deliberately, to the letter.
    Wiring a create serializer into the live path must not hand a lower-privileged
    role anything it lacks today, and #251 drew that line at the identifiers and
    the credentials, so this serializer adds *no* writable field: it adds only
    the rule.

    The consequence is that such a role cannot create a META app at all, because
    it cannot supply the identifiers a META app requires. That is the honest
    outcome of the two rules meeting, and it is the better one: today the create
    succeeds and produces an app that silently sends nothing and receives
    nothing. The error says who can finish the job.

    Under the default roles this serializer is never reached — only OWNER (100)
    and ADMIN (80) hold ``wa_app.manage``, and both clear the #251 threshold. It
    exists for a custom role that was granted ``wa_app.manage`` below priority 80.
    """

    def _validate_meta_identifiers(self, data):
        raise serializers.ValidationError(
            {
                "bsp": (
                    "A META app needs a WABA ID and a Phone Number ID, and your role cannot set "
                    "BSP identifiers. Ask an owner or admin to create this app, or choose a "
                    "different BSP."
                )
            }
        )
