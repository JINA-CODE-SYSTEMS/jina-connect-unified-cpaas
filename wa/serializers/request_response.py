"""
Request/Response Serializers

Utility serializers for request validation and response formatting.
These are non-model serializers used for API input/output handling.
"""

from rest_framework import serializers


class DateTimeRequestSerializer(serializers.Serializer):
    """
    Serializer for date/time range request validation.
    
    Used for filtering and querying data based on date ranges.
    """
    
    start_date = serializers.DateTimeField(
        required=False,
        allow_null=True,
        help_text="Start date for the date range filter (ISO 8601 format)"
    )
    
    end_date = serializers.DateTimeField(
        required=False,
        allow_null=True,
        help_text="End date for the date range filter (ISO 8601 format)"
    )
    
    def validate(self, data):
        """
        Validate that start_date is before end_date if both are provided.
        """
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        
        if start_date and end_date and start_date > end_date:
            raise serializers.ValidationError({
                'non_field_errors': [
                    "start_date must be before or equal to end_date"
                ]
            })
        
        return data


class ChargeBreakdownRequestSerializer(serializers.Serializer):
    """
    Serializer for charge breakdown request validation.

    Accepts EITHER:
      - contact_ids + template_id  (pre-creation estimation)
      - broadcast_id               (existing broadcast with contacts attached)

    Optional:
      - wa_app_id: required only when tenant has multiple WA apps.
    """

    contact_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=False,
        help_text="List of TenantContact IDs for the breakdown (pre-creation).",
    )

    template_id = serializers.CharField(
        required=False,
        help_text="WATemplate UUID or TemplateNumber PK — used to determine message category.",
    )

    broadcast_id = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Existing Broadcast ID (contacts & template resolved from the broadcast).",
    )

    wa_app_id = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="TenantWAApp ID (optional if tenant has only one WA app).",
    )

    def validate(self, data):
        contact_ids = data.get("contact_ids")
        broadcast_id = data.get("broadcast_id")

        if not contact_ids and not broadcast_id:
            raise serializers.ValidationError(
                "Provide either 'contact_ids' (with optional 'template_id') "
                "or 'broadcast_id'."
            )

        if contact_ids and broadcast_id:
            raise serializers.ValidationError(
                "Provide either 'contact_ids' or 'broadcast_id', not both."
            )

        if contact_ids and len(contact_ids) > 500_000:
            raise serializers.ValidationError(
                "contact_ids cannot exceed 500,000 entries."
            )

        return data


class ChargeBreakdownStatusSerializer(serializers.Serializer):
    """Serializer for polling an async charge-breakdown task result."""

    task_id = serializers.CharField(
        required=True,
        help_text="Celery task ID returned by the initial charge breakdown request.",
    )


class PaginationRequestSerializer(serializers.Serializer):
    """
    Serializer for pagination request parameters.
    """
    
    page = serializers.IntegerField(
        required=False,
        min_value=1,
        default=1,
        help_text="Page number (1-indexed)"
    )
    
    page_size = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=100,
        default=20,
        help_text="Number of items per page (max 100)"
    )


class SearchRequestSerializer(serializers.Serializer):
    """
    Serializer for search request parameters.
    """
    
    query = serializers.CharField(
        required=True,
        min_length=1,
        max_length=200,
        help_text="Search query string"
    )
    
    fields = serializers.ListField(
        child=serializers.CharField(max_length=50),
        required=False,
        allow_empty=True,
        help_text="List of fields to search in"
    )
    
    exact_match = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Whether to perform exact match instead of partial match"
    )
