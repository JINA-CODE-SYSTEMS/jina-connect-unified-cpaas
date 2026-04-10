import logging
from unicodedata import decimal

from abstract.serializers import BaseSerializer
from djmoney.contrib.django_rest_framework import MoneyField
from rest_framework import serializers, status
from tenants.converters import (AutoMediaConverter, ConversionError,
                                MediaConverter, get_conversion_capabilities)
from tenants.models import (IndustryChoices, RolePermission, Tenant,
                            TenantMedia, TenantRole, TenantTags, TenantUser,
                            TenantWAApp, WABAInfo)
from tenants.permissions import ALL_PERMISSIONS
from tenants.rbac_validators import (
    validate_permission_escalation,
    validate_priority_escalation,
    validate_role_assignment,
)
from tenants.utility.money_to_dict import money_to_dict
from tenants.validators import (MediaValidationError,
                                WhatsAppMediaSerializerValidator,
                                WhatsAppMediaValidator)
from wa.utility.apis.gupshup.wallet_api import WalletAPI
from wa.utility.data_model.gupshup.host_wallet_data import WalletData

logger = logging.getLogger(__name__)


class TenantGupshupAppNestedSerializer(serializers.ModelSerializer):
    """
    Nested serializer for WhatsApp Gupshup app details with pricing information.
    """
    wa_authentication_message_price = MoneyField(decimal_places=2, max_digits=15, source='authentication_message_price', read_only=True)
    wa_authentication_message_price_currency = serializers.CharField(source='authentication_message_price_currency', read_only=True)
    wa_marketing_message_price = MoneyField(decimal_places=2, max_digits=15, source='marketing_message_price', read_only=True)
    wa_marketing_message_price_currency = serializers.CharField(source='marketing_message_price_currency', read_only=True)
    wa_utility_message_price = MoneyField(decimal_places=2, max_digits=15, source='utility_message_price', read_only=True)
    wa_utility_message_price_currency = serializers.CharField(source='utility_message_price_currency', read_only=True)
    wa_number = serializers.CharField(read_only=True)
    wa_app_name = serializers.CharField(source='app_name', read_only=True)
    wa_app_id = serializers.CharField(source='app_id', read_only=True)
    bsp = serializers.CharField(read_only=True)
    
    class Meta:
        model = TenantWAApp
        fields = [
            'id',
            'wa_app_name',
            'wa_number',
            'wa_authentication_message_price',
            'wa_authentication_message_price_currency',
            'wa_marketing_message_price',
            'wa_marketing_message_price_currency',
            'wa_utility_message_price',
            'wa_utility_message_price_currency',
            'wa_app_id',
            'bsp',
        ]


class TenantGupshupAppSafeNestedSerializer(serializers.ModelSerializer):
    """
    Safe nested serializer for WA apps — excludes pricing and BSP identifiers.
    Used when the requesting user's role priority is below ADMIN (< 80).
    """
    wa_number = serializers.CharField(read_only=True)
    wa_app_name = serializers.CharField(source='app_name', read_only=True)
    bsp = serializers.CharField(read_only=True)

    class Meta:
        model = TenantWAApp
        fields = ['id', 'wa_app_name', 'wa_number', 'bsp']


class TenantSerializer(BaseSerializer):
    """
    Serializer for the Tenant model.
    """
    balance = MoneyField(decimal_places=2, max_digits=15)
    credit_line = MoneyField(decimal_places=2, max_digits=15)
    threshold_alert = MoneyField(decimal_places=2, max_digits=15)
    wa_apps = TenantGupshupAppNestedSerializer(many=True, read_only=True)
    products_using = serializers.ListField(child=serializers.CharField(), read_only=True)
    contacts_count = serializers.IntegerField(read_only=True)

    class Meta(BaseSerializer.Meta):
        model = Tenant
        fields = "__all__"

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        # Add products list
        representation['products_using'] = instance.products
        # Use annotated contacts_count if available, otherwise fall back to property
        if not hasattr(instance, 'contacts_count'):
            representation['contacts_count'] = instance.contact_count
        return representation


class TenantLimitedSerializer(BaseSerializer):
    """
    Limited serializer for the Tenant model — hides financial fields.
    Used for AGENT / VIEWER / MANAGER (role priority < 80).

    Ticket #251: balance, credit_line, threshold_alert, and pricing fields
    are restricted to ADMIN/OWNER only.
    """
    wa_apps = TenantGupshupAppSafeNestedSerializer(many=True, read_only=True)
    products_using = serializers.ListField(child=serializers.CharField(), read_only=True)
    contacts_count = serializers.IntegerField(read_only=True)

    class Meta(BaseSerializer.Meta):
        model = Tenant
        fields = None  # clear parent's __all__
        exclude = ['balance', 'balance_currency', 'credit_line', 'credit_line_currency',
                    'threshold_alert', 'threshold_alert_currency']

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation['products_using'] = instance.products
        if not hasattr(instance, 'contacts_count'):
            representation['contacts_count'] = instance.contact_count
        return representation


class TenantMediaSerializer(BaseSerializer):
    """
    Serializer for the TenantMedia model.
    Validates and auto-converts uploaded media files to WhatsApp-compatible formats.
    
    Supported file types (native):
    - Documents: PDF, DOCX, XLSX, PPTX (max 100MB)
    - Images: JPG, JPEG, PNG (max 5MB for templates, 16MB for messages)
    - Videos: MP4 H.264 (max 5MB for templates, 16MB for messages)
    - Audio: OGG, AMR, MP3 (max 16MB)
    
    Auto-conversion supported:
    - Images: HEIC, HEIF, WebP, BMP, TIFF → JPEG/PNG
    - Videos: MOV, AVI, MKV, WebM → MP4 (requires FFmpeg)
    - Audio: WAV, FLAC, M4A → OGG/MP3 (requires FFmpeg)
    """
    media = serializers.FileField(
        required=True, 
        help_text="Upload media file. Auto-converts: HEIC, WebP, MOV, AVI, WAV to WhatsApp formats"
    )
    media_type = serializers.CharField(read_only=True, help_text="Detected media type (document, image, video, audio)")
    file_size = serializers.IntegerField(read_only=True, help_text="File size in bytes")
    mime_type = serializers.CharField(read_only=True, help_text="Detected MIME type")
    was_converted = serializers.BooleanField(read_only=True, help_text="Whether the file was auto-converted")
    original_filename = serializers.CharField(read_only=True, help_text="Original filename before conversion")
    handle_id = serializers.SerializerMethodField(read_only=True, help_text="WhatsApp handle ID for this media")
    auto_convert = serializers.BooleanField(
        write_only=True, 
        required=False, 
        default=True,
        help_text="Set to false to disable auto-conversion and strictly validate"
    )
    
    class Meta(BaseSerializer.Meta):
        model = TenantMedia
        fields = "__all__"
        extra_kwargs = {
            'media': {
                'help_text': 'Upload a media file',
                'style': {'base_template': 'file.html'}
            },
            'is_active': {'read_only': True},
            'card_index': {
                'required': False,
                'help_text': 'Zero-based card index for carousel templates (0, 1, 2, ...). Leave null for header media.'
            },
        }
    
    def get_handle_id(self, obj):
        """Get the WhatsApp handleId from wa_handle_id field."""
        return obj.handle_id
    
    def validate_media(self, value):
        """
        Validate and optionally auto-convert the uploaded media file.
        """
        # Get auto_convert flag from initial data
        auto_convert_enabled = self.initial_data.get('auto_convert', True)
        if isinstance(auto_convert_enabled, str):
            auto_convert_enabled = auto_convert_enabled.lower() not in ['false', '0', 'no']
        
        original_filename = value.name if hasattr(value, 'name') else 'unknown'
        was_converted = False
        
        # Try auto-conversion if enabled
        if auto_convert_enabled:
            needs_conv, media_type, target_ext = MediaConverter.needs_conversion(original_filename)
            
            if needs_conv:
                try:
                    converted_file, new_filename, was_converted = AutoMediaConverter.auto_convert(
                        value, 
                        is_template=True
                    )
                    if was_converted:
                        logger.info(f"Auto-converted {original_filename} → {new_filename}")
                        value = converted_file
                        self._was_converted = True
                        self._original_filename = original_filename
                except ConversionError as e:
                    # Log warning but continue with validation
                    logger.warning(f"Auto-conversion failed for {original_filename}: {e}")
                    raise serializers.ValidationError(
                        f"File format '{target_ext}' requires conversion but conversion failed: {str(e)}. "
                        f"Please convert the file manually to a supported format."
                    )
        
        # Now validate the (possibly converted) file
        try:
            result = WhatsAppMediaValidator.validate(value, is_template=True)
            self._validation_result = result
            
            if not hasattr(self, '_was_converted'):
                self._was_converted = False
                self._original_filename = original_filename
            
            return value
        except MediaValidationError as e:
            # If validation fails and file could be converted, suggest it
            needs_conv, media_type, target_ext = MediaConverter.needs_conversion(original_filename)
            if needs_conv and not auto_convert_enabled:
                raise serializers.ValidationError(
                    f"{str(e)} Enable auto_convert=true to automatically convert this file."
                )
            raise serializers.ValidationError(str(e))
    
    def to_representation(self, instance):
        """Add media metadata to the response."""
        representation = super().to_representation(instance)
        
        # If we have validation result from create/update, use it
        if hasattr(self, '_validation_result'):
            representation['media_type'] = self._validation_result.get('media_type')
            representation['file_size'] = self._validation_result.get('file_size')
            representation['mime_type'] = self._validation_result.get('mime_type')
            representation['was_converted'] = getattr(self, '_was_converted', False)
            if getattr(self, '_was_converted', False):
                representation['original_filename'] = getattr(self, '_original_filename', None)
        else:
            # For existing instances, detect from the saved file
            if instance.media:
                try:
                    representation['media_type'] = WhatsAppMediaValidator.get_media_type(instance.media)
                    representation['file_size'] = instance.media.size if hasattr(instance.media, 'size') else None
                except Exception:
                    pass
            representation['was_converted'] = False
        
        return representation

    def create(self, validated_data):
        """
        Create TenantMedia.
        
        This only saves the file to the server.  To upload the file to
        WhatsApp and obtain a handle_id / media_id, call the separate
        ``upload-to-wa`` action on the viewset.
        """
        instance = super().create(validated_data)
        return instance

    def update(self, instance, validated_data):
        """
        Update TenantMedia.
        
        If the media file changed, any existing WhatsApp IDs are cleared
        (they refer to the old file).  Call ``upload-to-wa`` again
        afterwards to obtain new IDs from the BSP.
        """
        media_changed = 'media' in validated_data
        instance = super().update(instance, validated_data)
        
        if media_changed:
            # Invalidate stale WA references
            instance.wa_handle_id = None
            instance.media_id = None
            instance.save(update_fields=['wa_handle_id', 'media_id'])
        
        return instance


class TenantGupshupAppsSerializer(BaseSerializer):
    """
    Serializer for the TenantGupshupApps model.
    """
    authentication_message_price = MoneyField(decimal_places=2, max_digits=15)
    marketing_message_price = MoneyField(decimal_places=2, max_digits=15)
    utility_message_price = MoneyField(decimal_places=2, max_digits=15)
    wa_authentication_message_price = MoneyField(decimal_places=2, max_digits=15, write_only=True, source='authentication_message_price')
    wa_marketing_message_price = MoneyField(decimal_places=2, max_digits=15, write_only=True, source='marketing_message_price')
    wa_utility_message_price = MoneyField(decimal_places=2, max_digits=15, write_only=True, source='utility_message_price')

    class Meta(BaseSerializer.Meta):
        model = TenantWAApp
        fields = "__all__"
        extra_kwargs ={
            "authentication_message_price": {"read_only": True},
            "marketing_message_price": {"read_only": True},
            "utility_message_price": {"read_only": True},
        }
    
    def update(self, instance, validated_data):
        """
        Override update method to handle pricing fields correctly.
        """
        # Handle the pricing fields
        if 'wa_authentication_message_price' in validated_data:
            instance.authentication_message_price = validated_data.pop('wa_authentication_message_price')
        if 'wa_marketing_message_price' in validated_data:
            instance.marketing_message_price = validated_data.pop('wa_marketing_message_price')
        if 'wa_utility_message_price' in validated_data:
            instance.utility_message_price = validated_data.pop('wa_utility_message_price')
        return super().update(instance, validated_data)

class HostWalletSerializer(serializers.Serializer):
    gupshup_wallet = serializers.DictField()
    outstanding_balance = serializers.FloatField()

    def _get_active_tenant_app(self):
        """
        Helper method to fetch the first active tenant and its active Gupshup app.
        Raises ValidationError if not found.
        """
        tenant = Tenant.objects.filter(is_active=True).first()
        if not tenant:
            raise serializers.ValidationError(
                {"error": "No active tenant found"},
                code=status.HTTP_404_NOT_FOUND,
            )

        wa_app = tenant.wa_apps.filter(is_active=True).first()
        if not wa_app:
            raise serializers.ValidationError(
                {"error": f"No active WA app found for tenant {tenant.id}"},
                code=status.HTTP_404_NOT_FOUND,
            )

        return wa_app

    def get_wallet_balance(self):
        """
        Fetch wallet balance from BSP and outstanding tenant balance.
        """
        # ✅ use helper
        wa_app = self._get_active_tenant_app()

        wallet_api = WalletAPI(
            appId=wa_app.app_id,
            token=wa_app.app_secret
        )

        # Call Gupshup API
        try:
            data = wallet_api.get_wallet_balance()
        except Exception as e:
            raise serializers.ValidationError(
                {"error": "Failed to fetch wallet balance from Gupshup", "details": str(e)},
                code=status.HTTP_502_BAD_GATEWAY,
            )

        # Normalize wallet data
        wallet_data = WalletData.from_api(data).model_dump()

        # Compute outstanding balance across tenants
        balance = Tenant.objects.aggregate_total_balance()
        outstanding = Tenant.objects.get_outstanding_balance_total()
        return {
            "gupshup" : {
            "gupshup_wallet": wallet_data,
            "total_tenants_balance": money_to_dict(balance),
            "creditors_outstanding": money_to_dict(outstanding),
            "total_tenant_count": Tenant.objects.count()
            }
        }

class TenantTagsSerializer(BaseSerializer):
    """
    Serializer for the TenantTags model.
    """
    class Meta(BaseSerializer.Meta):
        model = TenantTags
        fields = "__all__"


class WABAInfoSerializer(BaseSerializer):
    """
    Serializer for WABA (WhatsApp Business Account) information.
    Tracks account status, messaging limits, and other WABA details.
    """
    wa_app_name = serializers.CharField(source='wa_app.app_name', read_only=True)
    wa_app_number = serializers.CharField(source='wa_app.wa_number', read_only=True)
    last_synced_at = serializers.DateTimeField(read_only=True)
    last_sync_error = serializers.JSONField(read_only=True)
    
    class Meta(BaseSerializer.Meta):
        model = WABAInfo
        fields = "__all__"
        read_only_fields = ['last_synced_at', 'last_sync_error']
    
   
class TenantUserSerializer(BaseSerializer):
    """
    Serializer for the TenantUser model.
    """
    first_name = serializers.CharField(source='user.first_name', read_only=True)
    last_name = serializers.CharField(source='user.last_name', read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)
    name = serializers.SerializerMethodField(read_only=True)

    def get_name(self, obj):
        """Get full name of the user."""
        first_name = obj.user.first_name or ''
        last_name = obj.user.last_name or ''
        return f"{first_name} {last_name}".strip() or obj.user.email or obj.user.username
    class Meta(BaseSerializer.Meta):
        model = TenantUser
        fields = "__all__"


class BrandingSettingsSerializer(serializers.ModelSerializer):
    """
    Serializer for BrandingSettings model.
    Admin-only access for managing branding assets.
    
    Supports both file uploads and external URLs for:
    - Favicon: PNG 583x583 px
    - Primary Logo: SVG 854x262 px (aspect ratio 3.26:1)
    - Secondary Logo: SVG 532x380 px (aspect ratio 1.4:1)
    """
    
    # Computed URLs (prefer uploaded file, fallback to external URL)
    effective_favicon_url = serializers.SerializerMethodField(read_only=True)
    effective_primary_logo_url = serializers.SerializerMethodField(read_only=True)
    effective_secondary_logo_url = serializers.SerializerMethodField(read_only=True)
    
    class Meta:
        model = None  # Will be set dynamically to avoid circular import
        fields = [
            'id',
            'favicon',
            'favicon_url',
            'primary_logo',
            'primary_logo_url',
            'secondary_logo',
            'secondary_logo_url',
            'effective_favicon_url',
            'effective_primary_logo_url',
            'effective_secondary_logo_url',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Lazy import to avoid circular dependency
        from tenants.models import BrandingSettings
        self.Meta.model = BrandingSettings
    
    def get_effective_favicon_url(self, obj):
        """Return file URL if uploaded, otherwise external URL."""
        if obj.favicon:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.favicon.url)
            return obj.favicon.url
        return obj.favicon_url
    
    def get_effective_primary_logo_url(self, obj):
        """Return file URL if uploaded, otherwise external URL."""
        if obj.primary_logo:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.primary_logo.url)
            return obj.primary_logo.url
        return obj.primary_logo_url
    
    def get_effective_secondary_logo_url(self, obj):
        """Return file URL if uploaded, otherwise external URL."""
        if obj.secondary_logo:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.secondary_logo.url)
            return obj.secondary_logo.url
        return obj.secondary_logo_url
    
    def validate_favicon(self, value):
        """Validate favicon is PNG and approximately 583x583 px."""
        if value:
            # Check file extension
            if not value.name.lower().endswith('.png'):
                raise serializers.ValidationError("Favicon must be a PNG file.")
            
            # Check file size (max 1MB for favicon)
            if value.size > 1 * 1024 * 1024:
                raise serializers.ValidationError("Favicon file size must be less than 1MB.")
        return value
    
    def validate_primary_logo(self, value):
        """Validate primary logo is SVG."""
        if value:
            if not value.name.lower().endswith('.svg'):
                raise serializers.ValidationError("Primary logo must be an SVG file.")
            
            # Check file size (max 500KB for SVG)
            if value.size > 500 * 1024:
                raise serializers.ValidationError("Primary logo file size must be less than 500KB.")
        return value
    
    def validate_secondary_logo(self, value):
        """Validate secondary logo is SVG."""
        if value:
            if not value.name.lower().endswith('.svg'):
                raise serializers.ValidationError("Secondary logo must be an SVG file.")
            
            # Check file size (max 500KB for SVG)
            if value.size > 500 * 1024:
                raise serializers.ValidationError("Secondary logo file size must be less than 500KB.")
        return value


class TenantRegistrationSerializer(serializers.Serializer):
    """
    Serializer for anonymous tenant + user registration.
    Creates a new user and tenant, then links them via TenantUser.
    """
    # Tenant fields
    tenant_name = serializers.CharField(max_length=255, help_text="Name of the tenant/organization")
    tenant_website = serializers.URLField(required=False, allow_blank=True, help_text="Tenant website URL")
    tenant_address = serializers.CharField(required=False, allow_blank=True, help_text="Tenant address")
    tenant_country = serializers.CharField(max_length=2, required=False, allow_blank=True, help_text="ISO 3166-1 alpha-2 country code (e.g., IN, US)")
    tenant_state = serializers.CharField(max_length=10, required=False, allow_blank=True, help_text="ISO 3166-2 subdivision code (e.g., MH, CA)")
    tenant_industry = serializers.ChoiceField(choices=IndustryChoices.choices, required=False, allow_blank=True, help_text="Industry/sector of the tenant")
    
    # User fields
    first_name = serializers.CharField(max_length=150, help_text="User's first name")
    last_name = serializers.CharField(max_length=150, help_text="User's last name")
    email = serializers.EmailField(help_text="User's email address")
    phone = serializers.CharField(max_length=20, help_text="User's phone number with country code (e.g., +919876543210)")
    password = serializers.CharField(write_only=True, min_length=8, help_text="User's password (min 8 characters)")
    
    def validate_email(self, value):
        """Check if email already exists."""
        from users.models import User
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value.lower()
    
    def validate_phone(self, value):
        """Validate and check if phone already exists."""
        import phonenumbers
        from users.models import User
        
        try:
            # Parse the phone number
            parsed = phonenumbers.parse(value, None)
            if not phonenumbers.is_valid_number(parsed):
                raise serializers.ValidationError("Invalid phone number.")
            
            # Format to E.164 for consistent storage
            formatted = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
            
            # Check if phone already exists
            if User.objects.filter(mobile=formatted).exists():
                raise serializers.ValidationError("A user with this phone number already exists.")
            
            return formatted
        except phonenumbers.NumberParseException:
            raise serializers.ValidationError("Invalid phone number format. Include country code (e.g., +919876543210).")
    
    def validate_tenant_name(self, value):
        """Check if tenant name already exists."""
        if Tenant.objects.filter(name__iexact=value).exists():
            raise serializers.ValidationError("A tenant with this name already exists.")
        return value
    
    def create(self, validated_data):
        """Create user, tenant, and link them. User is inactive until email verified."""
        from django.db import transaction
        from users.models import EmailVerificationToken, User
        from users.services.email_verification import EmailVerificationService
        
        with transaction.atomic():
            # Create User (inactive until email verified)
            user = User.objects.create_user(
                username=validated_data['email'],  # Use email as username
                email=validated_data['email'],
                mobile=validated_data['phone'],
                first_name=validated_data['first_name'],
                last_name=validated_data['last_name'],
                password=validated_data['password'],
                is_active=False,  # Inactive until email verified
            )
            
            # Create Tenant
            tenant = Tenant.objects.create(
                name=validated_data['tenant_name'],
                website=validated_data.get('tenant_website') or None,
                address=validated_data.get('tenant_address') or None,
                country=validated_data.get('tenant_country') or None,
                state=validated_data.get('tenant_state') or None,
                industry=validated_data.get('tenant_industry') or None,
                created_by=user,
            )
            
            # Link User to Tenant as OWNER
            owner_role = TenantRole.objects.get(tenant=tenant, slug="owner")
            TenantUser.objects.create(
                tenant=tenant,
                user=user,
                role=owner_role,
                created_by=user,
            )
            
            # Create verification token and send email
            token = EmailVerificationToken.create_for_user(user)
            email_sent = EmailVerificationService.send_verification_email(user, token)
            
            return {
                'user': user,
                'tenant': tenant,
                'email_sent': email_sent,
            }


class TenantRoleSerializer(BaseSerializer):
    """Serializer for TenantRole used in the my-permissions response."""
    is_custom = serializers.SerializerMethodField()

    class Meta(BaseSerializer.Meta):
        model = TenantRole
        fields = ["id", "slug", "name", "priority", "is_system", "is_custom"]
        read_only_fields = fields

    def get_is_custom(self, obj):
        return not obj.is_system


class MyPermissionsSerializer(serializers.Serializer):
    """Response serializer for GET /tenants/my-permissions/."""
    role = TenantRoleSerializer(read_only=True)
    permissions = serializers.DictField(
        child=serializers.BooleanField(),
        read_only=True,
    )


# ---------------------------------------------------------------------------
# Member Management serializers (RBAC-9)
# ---------------------------------------------------------------------------

class MemberSerializer(BaseSerializer):
    """
    Read serializer for member listing.
    Includes user info, nested role details, and email verification status.
    """
    first_name = serializers.CharField(source="user.first_name", read_only=True)
    last_name = serializers.CharField(source="user.last_name", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    name = serializers.SerializerMethodField(read_only=True)
    email_verified = serializers.SerializerMethodField(read_only=True)
    role_detail = TenantRoleSerializer(source="role", read_only=True)

    class Meta(BaseSerializer.Meta):
        model = TenantUser
        fields = [
            "id", "tenant", "user", "role", "role_detail",
            "first_name", "last_name", "email", "username", "name",
            "is_active", "email_verified", "created_at", "updated_at",
            "created_by", "updated_by",
        ]
        read_only_fields = fields

    def get_email_verified(self, obj):
        """User.is_active is set to True when email is verified via EmailVerificationToken."""
        return obj.user.is_active

    def get_name(self, obj):
        first = obj.user.first_name or ""
        last = obj.user.last_name or ""
        return f"{first} {last}".strip() or obj.user.email or obj.user.username


class AddMemberSerializer(serializers.Serializer):
    """
    Write serializer for POST /tenants/members/add/.
    Admin provides email, password, name, and role to add a member.
    password and first_name are only required when the email is new (validated in validate()).
    """
    email = serializers.EmailField()
    password = serializers.CharField(required=False, write_only=True)
    first_name = serializers.CharField(required=False, max_length=150)
    last_name = serializers.CharField(required=False, max_length=150, default="")
    role_id = serializers.IntegerField()

    def validate_role_id(self, value):
        return validate_role_assignment(
            value, self.context["request"], self.context["tenant"],
        )

    def validate_email(self, value):
        return value.lower()

    def validate_password(self, value):
        """Validate password strength: min 8 chars, 1 upper, 1 digit, 1 special."""
        import re
        if len(value) < 8:
            raise serializers.ValidationError("Password must be at least 8 characters.")
        if not re.search(r"[A-Z]", value):
            raise serializers.ValidationError("Password must contain at least one uppercase letter.")
        if not re.search(r"[0-9]", value):
            raise serializers.ValidationError("Password must contain at least one digit.")
        if not re.search(r"[^A-Za-z0-9]", value):
            raise serializers.ValidationError("Password must contain at least one special character.")
        return value

    def validate(self, attrs):
        """If email is new (no existing User), password and first_name are required."""
        from users.models import User
        email = attrs.get("email", "").lower()
        user_exists = User.objects.filter(email__iexact=email).exists()
        if not user_exists:
            if not attrs.get("password"):
                raise serializers.ValidationError(
                    {"password": "Password is required for new users."}
                )
            if not attrs.get("first_name"):
                raise serializers.ValidationError(
                    {"first_name": "First name is required for new users."}
                )
        return attrs


class ChangeRoleSerializer(serializers.Serializer):
    """
    Write serializer for PATCH /tenants/members/{id}/role/.
    """
    role_id = serializers.IntegerField()

    def validate_role_id(self, value):
        return validate_role_assignment(
            value, self.context["request"], self.context["tenant"],
        )


class TransferOwnershipSerializer(serializers.Serializer):
    """
    Write serializer for POST /tenants/transfer-ownership/.
    Target must be an existing ADMIN in the same tenant.
    """
    target_user_id = serializers.IntegerField()

    def validate_target_user_id(self, value):
        request = self.context["request"]
        tenant = self.context["tenant"]

        # Target must be an active tenant member with ADMIN role
        target_tu = TenantUser.objects.select_related("role").filter(
            tenant=tenant, user_id=value, is_active=True
        ).first()
        if not target_tu:
            raise serializers.ValidationError(
                "Target user is not an active member of this tenant."
            )
        if not target_tu.role or target_tu.role.slug != "admin":
            raise serializers.ValidationError(
                "Ownership can only be transferred to a user with the ADMIN role."
            )

        # Requester must be OWNER
        requester_tu = TenantUser.objects.select_related("role").filter(
            tenant=tenant, user=request.user, is_active=True
        ).first()
        if not requester_tu or not requester_tu.role or requester_tu.role.slug != "owner":
            raise serializers.ValidationError(
                "Only the current OWNER can transfer ownership."
            )

        return value


# ═══════════════════════════════════════════════════════════════════════════
# RBAC-10 — Role CRUD serializers
# ═══════════════════════════════════════════════════════════════════════════


class PermissionsCatalogSerializer(serializers.Serializer):
    """Serializer for a single permission entry in the permissions catalog."""
    key = serializers.CharField()
    label = serializers.CharField()


class RoleDetailSerializer(BaseSerializer):
    """Read serializer for role list/detail — includes permissions map and member count."""

    is_custom = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()

    class Meta(BaseSerializer.Meta):
        model = TenantRole
        fields = [
            "id", "slug", "name", "description", "priority",
            "is_system", "is_editable", "is_custom",
            "member_count", "permissions",
            "created_at", "updated_at", "created_by", "updated_by",
        ]
        read_only_fields = fields

    def get_is_custom(self, obj):
        return not obj.is_system

    def get_member_count(self, obj):
        if hasattr(obj, "member_count"):
            return obj.member_count
        return obj.members.filter(is_active=True).count()

    def get_permissions(self, obj):
        """Return {perm_key: bool} for all known permission keys."""
        perms = {rp.permission: rp.allowed for rp in obj.permissions.all()}
        return {p: perms.get(p, False) for p in ALL_PERMISSIONS}


class CreateRoleSerializer(serializers.Serializer):
    """Write serializer for POST /tenants/roles/."""

    name = serializers.CharField(max_length=100)
    slug = serializers.SlugField(max_length=100, required=False)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    priority = serializers.IntegerField(min_value=1, max_value=99)
    permissions = serializers.DictField(
        child=serializers.BooleanField(), required=False, default=dict,
    )

    def validate_slug(self, value):
        tenant = self.context["tenant"]
        if TenantRole.objects.filter(tenant=tenant, slug=value).exists():
            raise serializers.ValidationError(
                "A role with this slug already exists in this tenant."
            )
        return value

    def validate_priority(self, value):
        return validate_priority_escalation(
            value, self.context["request"], self.context["tenant"],
        )

    def validate_permissions(self, value):
        return validate_permission_escalation(
            value, self.context["request"], self.context["tenant"],
        )

    def validate(self, data):
        tenant = self.context["tenant"]
        # Auto-generate slug from name if not provided
        if "slug" not in data or not data.get("slug"):
            from django.utils.text import slugify

            base_slug = slugify(data["name"])
            if not base_slug:
                raise serializers.ValidationError(
                    {"slug": "Could not auto-generate slug from name. Provide one explicitly."}
                )
            slug = base_slug
            counter = 1
            while TenantRole.objects.filter(tenant=tenant, slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            data["slug"] = slug

        # 20 custom roles limit
        custom_count = TenantRole.objects.filter(
            tenant=tenant, is_system=False,
        ).count()
        if custom_count >= 20:
            raise serializers.ValidationError(
                "Maximum of 20 custom roles per tenant reached."
            )
        return data


class UpdateRoleSerializer(serializers.Serializer):
    """Write serializer for PATCH /tenants/roles/{id}/."""

    name = serializers.CharField(max_length=100, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    priority = serializers.IntegerField(min_value=1, max_value=99, required=False)
    permissions = serializers.DictField(
        child=serializers.BooleanField(), required=False,
    )

    def validate_priority(self, value):
        return validate_priority_escalation(
            value, self.context["request"], self.context["tenant"],
        )

    def validate_permissions(self, value):
        return validate_permission_escalation(
            value, self.context["request"], self.context["tenant"],
        )