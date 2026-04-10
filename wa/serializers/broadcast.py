"""
WABroadcast Serializer

Serializer for WhatsApp Broadcasts with quota validation.
"""

from broadcast.serializers import BroadcastLimitedSerializer, BroadcastSerializer
from rest_framework import serializers
from wa.models import WABroadcast


class WABroadcastSerializer(BroadcastSerializer):
    """
    Serializer for WhatsApp Broadcasts.
    
    Extends BroadcastSerializer with WhatsApp-specific fields:
    - Product message sections (MPM, SPM, Carousel, Catalog)
    - Quota validation before broadcast creation
    """
    # Product message fields - structure depends on template type:
    # - MPM: multiple sections, multiple products per section
    # - SPM: 1 section, 1 product
    # - Carousel: 1 section, up to 10 products
    # - Catalog: use catalog_id instead
    sections = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        allow_null=True,
        help_text="Product sections. Format: [{'title': 'Section Name', 'product_ids': [1, 2, 3]}]"
    )
    catalog_id = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        help_text="Catalog ID for catalog-type messages"
    )
    
    class Meta:
        model = WABroadcast
        fields = '__all__'

    def validate(self, attrs):
        """
        Validate quota before allowing broadcast creation or update.
        Raises ValidationError if broadcast would exceed tier limit.
        
        Validates on:
        - QUEUED/SCHEDULED creation (hard block)
        - DRAFT → QUEUED update (hard block)
        - Recipient changes on existing QUEUED broadcast
        
        Skips validation for DRAFT (validated when moved to QUEUED).
        """
        from broadcast.models import BroadcastStatusChoices
        from wa.services.quota_service import QuotaService
        
        attrs = super().validate(attrs)
        
        status = attrs.get('status', BroadcastStatusChoices.DRAFT)
        instance = getattr(self, 'instance', None)
        
        # Skip quota validation for DRAFT - validate only when committing to QUEUED
        if status == BroadcastStatusChoices.DRAFT:
            return attrs
        
        # Skip if updating an already QUEUED broadcast without recipient changes
        if instance and instance.status == BroadcastStatusChoices.QUEUED:
            if 'recipients' not in attrs and not attrs.get('select_all'):
                return attrs
        
        # Get wa_app - from template_number or existing instance
        wa_app = None
        
        # Try to get from attrs (new template_number being set)
        # After super().validate(), template_number should be a TemplateNumber instance
        template_number = attrs.get('template_number')
        
        if template_number:
            # If it's an ID (int), we need to fetch the object
            if isinstance(template_number, int):
                from message_templates.models import TemplateNumber as TN
                try:
                    template_number = TN.objects.select_related('gupshup_template__wa_app').get(pk=template_number)
                except TN.DoesNotExist:
                    template_number = None
            
            # Now check for gupshup_template relationship
            if template_number and hasattr(template_number, 'gupshup_template'):
                try:
                    wa_app = template_number.gupshup_template.wa_app
                except Exception:
                    pass
        
        # Fallback: get from existing instance
        if not wa_app and instance:
            if instance.template_number and hasattr(instance.template_number, 'gupshup_template') and instance.template_number.gupshup_template:
                wa_app = instance.template_number.gupshup_template.wa_app
        
        # Fallback: get from request user's tenant
        if not wa_app:
            request = self.context.get('request')
            if request:
                from tenants.models import TenantWAApp
                wa_app = TenantWAApp.objects.filter_by_user_tenant(request.user).first()
        
        if not wa_app:
            return attrs  # Can't validate without app, let it proceed
        
        # Get phone numbers for validation — branch for select_all (#412)
        select_all = attrs.get('select_all', False)
        if select_all:
            phone_list = self._resolve_select_all_phones(attrs)
            if not phone_list:
                return attrs
        else:
            recipients = attrs.get('recipients')
            if recipients is None and instance:
                recipients = list(instance.recipients.all())
            if not recipients:
                return attrs
            phone_list = [str(c.phone) for c in recipients if hasattr(c, 'phone')]
            if not phone_list:
                return attrs
        
        # Validate against quota
        quota_service = QuotaService(wa_app)
        
        scheduled_time = attrs.get('scheduled_time')
        if scheduled_time is None and instance:
            scheduled_time = instance.scheduled_time
        
        # Pass existing broadcast ID for updates (to exclude its own contacts from window)
        broadcast_id = instance.id if instance else None
        validation_result = quota_service.validate_broadcast(
            recipient_phones=phone_list,
            scheduled_time=scheduled_time,
            exclude_broadcast_id=broadcast_id
        )
        
        if not validation_result['is_valid']:
            raise serializers.ValidationError({
                'recipients': validation_result['error'],
                'quota_details': {
                    'tier_limit': validation_result.get('tier_limit'),
                    'max_allowed_new_contacts': validation_result.get('max_allowed_new_contacts'),
                    'unique_new_contacts': validation_result.get('unique_new_contacts'),
                    'effective_remaining': validation_result.get('effective_remaining'),
                    'overflow_by': validation_result.get('overflow_by'),
                }
            })
        
        # =====================================================================
        # PRE-FLIGHT BALANCE CHECK
        # Prevents the broadcast from being created only to be silently marked
        # as FAILED in the post_save signal when balance is insufficient.
        # =====================================================================
        self._check_balance_for_broadcast(wa_app, phone_list, phone_list)
        
        return attrs

    def _resolve_select_all_phones(self, attrs):
        """Resolve phone numbers for select_all mode (#412)."""
        from contacts.models import TenantContact
        tenant = attrs.get('tenant')
        instance = getattr(self, 'instance', None)
        if not tenant and instance:
            tenant = instance.tenant
        if not tenant:
            return []
        tag_filter = attrs.get('tag_filter', [])
        qs = TenantContact.objects.filter(
            tenant=tenant, phone__isnull=False
        ).exclude(phone='')
        if tag_filter:
            qs = qs.filter(tag__in=tag_filter)
        return list(qs.values_list('phone', flat=True))

    def _check_balance_for_broadcast(self, wa_app, recipients, phone_list):
        """
        Pre-flight balance check before broadcast creation/scheduling.
        
        Raises ValidationError with structured error if balance is insufficient.
        Attaches a low-balance warning to serializer context if balance is below threshold.
        """
        from djmoney.money import Money
        
        request = self.context.get('request')
        if not request:
            return
        
        tenant = request.user.tenant
        
        # Calculate estimated cost: recipients × price_per_message
        price_per_message = self._get_price_per_message(wa_app)
        if price_per_message <= 0:
            return
        
        recipient_count = len(phone_list)
        estimated_cost = Money(recipient_count * price_per_message, tenant.balance.currency)
        available_balance = tenant.total_balance
        
        # Hard block: insufficient balance
        if available_balance < estimated_cost:
            raise serializers.ValidationError({
                'balance': (
                    f"Insufficient balance. Estimated cost: {estimated_cost}, "
                    f"Available: {available_balance}."
                ),
                'balance_details': {
                    'estimated_cost': float(estimated_cost.amount),
                    'available_balance': float(available_balance.amount),
                    'balance': float(tenant.balance.amount),
                    'credit_line': float(tenant.credit_line.amount),
                    'currency': str(tenant.balance.currency),
                    'shortfall': float((estimated_cost - available_balance).amount),
                    'recipient_count': recipient_count,
                    'price_per_message': float(price_per_message),
                }
            })
        
        # Soft warning: low balance (balance will be below threshold after deduction)
        balance_after = available_balance - estimated_cost
        if balance_after < tenant.threshold_alert or tenant.is_below_threshold:
            # Attach warning to serializer context for the viewset to include in response
            warnings = self.context.setdefault('_broadcast_warnings', [])
            warnings.append({
                'type': 'LOW_BALANCE',
                'message': (
                    f"Low balance warning: After this broadcast, your balance will be "
                    f"{balance_after} (threshold: {tenant.threshold_alert})."
                ),
                'details': {
                    'available_balance': float(available_balance.amount),
                    'estimated_cost': float(estimated_cost.amount),
                    'balance_after_broadcast': float(balance_after.amount),
                    'threshold': float(tenant.threshold_alert.amount),
                    'currency': str(tenant.balance.currency),
                }
            })

    @staticmethod
    def _get_price_per_message(wa_app):
        """Get the default price per message for the WA app (uses marketing rate as default)."""
        try:
            return float(wa_app.marketing_message_price.amount)
        except Exception:
            return 0


class WABroadcastLimitedSerializer(BroadcastLimitedSerializer):
    """
    Limited WA broadcast serializer — hides financial fields.
    Ticket #251: AGENT cannot see cost/refund data on broadcasts.
    Mirrors WABroadcastSerializer's extra fields (sections, catalog_id)
    but inherits BroadcastLimitedSerializer's field exclusions.
    """
    sections = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        allow_null=True,
    )
    catalog_id = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
    )

    class Meta(BroadcastLimitedSerializer.Meta):
        model = WABroadcast
