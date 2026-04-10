"""
WAMessage ViewSet - BSP Agnostic Message Management

Provides CRUD operations for WhatsApp Messages.
Frontend uses this for conversation view and message sending.
"""

from django_filters import rest_framework as filters
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from abstract.viewsets.base import BaseTenantModelViewSet
from wa.models import WAMessage, MessageStatus, MessageDirection, MessageType
from wa.serializers import (
    WAMessageSerializer,
    WAMessageListSerializer,
    WAMessageCreateSerializer,
)


class WAMessageFilter(filters.FilterSet):
    """Filter for WAMessage listing."""
    
    wa_app = filters.UUIDFilter(field_name='wa_app__id')
    contact = filters.NumberFilter(field_name='contact__id')
    direction = filters.ChoiceFilter(choices=MessageDirection.choices)
    message_type = filters.ChoiceFilter(choices=MessageType.choices)
    status = filters.ChoiceFilter(choices=MessageStatus.choices)
    template = filters.UUIDFilter(field_name='template__id')
    
    # Date range filters
    created_after = filters.DateTimeFilter(field_name='created_at', lookup_expr='gte')
    created_before = filters.DateTimeFilter(field_name='created_at', lookup_expr='lte')
    sent_after = filters.DateTimeFilter(field_name='sent_at', lookup_expr='gte')
    sent_before = filters.DateTimeFilter(field_name='sent_at', lookup_expr='lte')
    
    # Boolean filters
    is_billable = filters.BooleanFilter()
    has_error = filters.BooleanFilter(method='filter_has_error')
    
    def filter_has_error(self, queryset, name, value):
        if value:
            return queryset.exclude(error_message__isnull=True).exclude(error_message='')
        return queryset.filter(error_message__isnull=True) | queryset.filter(error_message='')
    
    class Meta:
        model = WAMessage
        fields = [
            'wa_app', 'contact', 'direction', 'message_type', 'status',
            'template', 'is_billable', 'created_after', 'created_before',
        ]


class WAMessageViewSet(BaseTenantModelViewSet):
    """
    ViewSet for managing WhatsApp Messages.
    
    Provides endpoints to:
    - List messages for a conversation or contact
    - Send new outbound messages (text or template)
    - Retrieve message details and delivery status
    - View message statistics
    
    All operations are tenant-scoped based on authenticated user.
    """
    
    queryset = WAMessage.objects.select_related('wa_app', 'contact', 'template').all()
    serializer_class = WAMessageSerializer
    filterset_class = WAMessageFilter
    search_fields = ['text', 'wa_message_id', 'contact__phone_number']
    ordering_fields = ['created_at', 'sent_at', 'delivered_at', 'read_at']
    ordering = ['-created_at']
    http_method_names = ['get', 'post', 'patch']
    required_permissions = {
        "list": "inbox.view",
        "retrieve": "inbox.view",
        "create": "inbox.reply",
        "partial_update": "inbox.reply",
        "statistics": "inbox.view",
        "conversation": "inbox.view",
        "default": "inbox.view",
    }
    
    def get_serializer_class(self):
        """Use appropriate serializer based on action."""
        if self.action == 'list':
            return WAMessageListSerializer
        if self.action == 'create':
            return WAMessageCreateSerializer
        return WAMessageSerializer
    
    @swagger_auto_schema(
        operation_description="List WhatsApp messages with filtering support",
        operation_summary="List Messages",
        operation_id="list_wa_messages",
        tags=["WhatsApp Messages (v2)"],
        manual_parameters=[
            openapi.Parameter(
                'wa_app',
                openapi.IN_QUERY,
                description="Filter by WA App ID",
                type=openapi.TYPE_STRING,
                format='uuid',
                required=False
            ),
            openapi.Parameter(
                'contact',
                openapi.IN_QUERY,
                description="Filter by contact ID",
                type=openapi.TYPE_INTEGER,
                required=False
            ),
            openapi.Parameter(
                'direction',
                openapi.IN_QUERY,
                description="Filter by message direction",
                type=openapi.TYPE_STRING,
                enum=['INBOUND', 'OUTBOUND'],
                required=False
            ),
            openapi.Parameter(
                'message_type',
                openapi.IN_QUERY,
                description="Filter by message type",
                type=openapi.TYPE_STRING,
                enum=['TEXT', 'TEMPLATE', 'IMAGE', 'VIDEO', 'DOCUMENT', 'AUDIO', 'LOCATION', 'CONTACTS', 'STICKER', 'INTERACTIVE', 'REACTION'],
                required=False
            ),
            openapi.Parameter(
                'status',
                openapi.IN_QUERY,
                description="Filter by delivery status",
                type=openapi.TYPE_STRING,
                enum=['PENDING', 'SENT', 'DELIVERED', 'READ', 'FAILED', 'EXPIRED'],
                required=False
            ),
            openapi.Parameter(
                'created_after',
                openapi.IN_QUERY,
                description="Filter messages created after this datetime",
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_DATETIME,
                required=False
            ),
            openapi.Parameter(
                'created_before',
                openapi.IN_QUERY,
                description="Filter messages created before this datetime",
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_DATETIME,
                required=False
            ),
            openapi.Parameter(
                'has_error',
                openapi.IN_QUERY,
                description="Filter messages with errors",
                type=openapi.TYPE_BOOLEAN,
                required=False
            ),
            openapi.Parameter(
                'search',
                openapi.IN_QUERY,
                description="Search in text, wa_message_id, contact phone",
                type=openapi.TYPE_STRING,
                required=False
            ),
            openapi.Parameter(
                'ordering',
                openapi.IN_QUERY,
                description="Order results by field",
                type=openapi.TYPE_STRING,
                enum=['created_at', '-created_at', 'sent_at', '-sent_at'],
                required=False
            ),
        ],
        responses={
            200: openapi.Response(
                description="List of messages",
                schema=WAMessageListSerializer(many=True)
            ),
            401: openapi.Response(description="Authentication required"),
        }
    )
    def list(self, request, *args, **kwargs):
        """List WhatsApp messages with filtering support."""
        return super().list(request, *args, **kwargs)
    
    @swagger_auto_schema(
        operation_description="Send a new outbound WhatsApp message",
        operation_summary="Send Message",
        operation_id="create_wa_message",
        tags=["WhatsApp Messages (v2)"],
        request_body=WAMessageCreateSerializer,
        responses={
            201: openapi.Response(description="Message queued successfully", schema=WAMessageSerializer()),
            400: openapi.Response(description="Validation error"),
            401: openapi.Response(description="Authentication required"),
        }
    )
    def create(self, request, *args, **kwargs):
        """Send a new outbound WhatsApp message."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # Set direction to outbound for new messages
        message = serializer.save(
            direction=MessageDirection.OUTBOUND,
            status=MessageStatus.PENDING,
        )
        
        # TODO: Trigger async task to send message via BSP adapter
        # send_whatsapp_message.delay(str(message.id))
        
        return Response(
            WAMessageSerializer(message).data,
            status=status.HTTP_201_CREATED
        )
    
    @swagger_auto_schema(
        operation_description="Retrieve a specific message by ID",
        operation_summary="Get Message",
        operation_id="retrieve_wa_message",
        tags=["WhatsApp Messages (v2)"],
        responses={
            200: openapi.Response(description="Message details", schema=WAMessageSerializer()),
            401: openapi.Response(description="Authentication required"),
            404: openapi.Response(description="Message not found")
        }
    )
    def retrieve(self, request, *args, **kwargs):
        """Retrieve a specific message by ID."""
        return super().retrieve(request, *args, **kwargs)
    
    @swagger_auto_schema(
        operation_description="Get message delivery statistics",
        operation_summary="Message Statistics",
        operation_id="wa_message_statistics",
        tags=["WhatsApp Messages (v2)"],
        manual_parameters=[
            openapi.Parameter(
                'wa_app',
                openapi.IN_QUERY,
                description="Filter by WA App ID",
                type=openapi.TYPE_STRING,
                format='uuid',
                required=False
            ),
            openapi.Parameter(
                'date_from',
                openapi.IN_QUERY,
                description="Start date for statistics",
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_DATE,
                required=False
            ),
            openapi.Parameter(
                'date_to',
                openapi.IN_QUERY,
                description="End date for statistics",
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_DATE,
                required=False
            ),
        ],
        responses={
            200: openapi.Response(
                description="Message statistics",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'total': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'inbound': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'outbound': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'by_status': openapi.Schema(
                            type=openapi.TYPE_OBJECT,
                            properties={
                                'PENDING': openapi.Schema(type=openapi.TYPE_INTEGER),
                                'SENT': openapi.Schema(type=openapi.TYPE_INTEGER),
                                'DELIVERED': openapi.Schema(type=openapi.TYPE_INTEGER),
                                'READ': openapi.Schema(type=openapi.TYPE_INTEGER),
                                'FAILED': openapi.Schema(type=openapi.TYPE_INTEGER),
                            }
                        ),
                    }
                )
            ),
            401: openapi.Response(description="Authentication required"),
        }
    )
    @action(detail=False, methods=['get'], url_path='statistics')
    def statistics(self, request):
        """Get message delivery statistics."""
        from django.db.models import Count, Q
        
        queryset = self.filter_queryset(self.get_queryset())
        
        # Apply date filters if provided
        date_from = request.query_params.get('date_from')
        date_to = request.query_params.get('date_to')
        
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)
        
        # Calculate statistics
        stats = queryset.aggregate(
            total=Count('id'),
            inbound=Count('id', filter=Q(direction=MessageDirection.INBOUND)),
            outbound=Count('id', filter=Q(direction=MessageDirection.OUTBOUND)),
            pending=Count('id', filter=Q(status=MessageStatus.PENDING)),
            sent=Count('id', filter=Q(status=MessageStatus.SENT)),
            delivered=Count('id', filter=Q(status=MessageStatus.DELIVERED)),
            read=Count('id', filter=Q(status=MessageStatus.READ)),
            failed=Count('id', filter=Q(status=MessageStatus.FAILED)),
        )
        
        return Response({
            'total': stats['total'],
            'inbound': stats['inbound'],
            'outbound': stats['outbound'],
            'by_status': {
                'PENDING': stats['pending'],
                'SENT': stats['sent'],
                'DELIVERED': stats['delivered'],
                'READ': stats['read'],
                'FAILED': stats['failed'],
            }
        })
    
    @swagger_auto_schema(
        operation_description="Get conversation thread for a contact",
        operation_summary="Get Conversation",
        operation_id="wa_message_conversation",
        tags=["WhatsApp Messages (v2)"],
        manual_parameters=[
            openapi.Parameter(
                'contact_id',
                openapi.IN_QUERY,
                description="Contact ID",
                type=openapi.TYPE_INTEGER,
                required=True
            ),
            openapi.Parameter(
                'wa_app',
                openapi.IN_QUERY,
                description="WA App ID",
                type=openapi.TYPE_STRING,
                format='uuid',
                required=True
            ),
        ],
        responses={
            200: openapi.Response(
                description="Conversation messages",
                schema=WAMessageSerializer(many=True)
            ),
            400: openapi.Response(description="Missing required parameters"),
            401: openapi.Response(description="Authentication required"),
        }
    )
    @action(detail=False, methods=['get'], url_path='conversation')
    def conversation(self, request):
        """Get conversation thread for a contact."""
        contact_id = request.query_params.get('contact_id')
        wa_app_id = request.query_params.get('wa_app')
        
        if not contact_id or not wa_app_id:
            return Response(
                {'error': 'Both contact_id and wa_app are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        queryset = self.get_queryset().filter(
            contact_id=contact_id,
            wa_app_id=wa_app_id
        ).order_by('created_at')
        
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = WAMessageSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        
        serializer = WAMessageSerializer(queryset, many=True)
        return Response(serializer.data)
