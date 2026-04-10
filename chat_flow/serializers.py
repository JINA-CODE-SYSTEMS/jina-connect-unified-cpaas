from abstract.serializers import BaseSerializer
from rest_framework import serializers
from wa.models import StatusChoices as S
from wa.models import WATemplate

from .models import ChatFlow, ChatFlowEdge, ChatFlowNode


class ChatFlowSerializer(BaseSerializer):
    """
    Serializer for ChatFlow model with ReactFlow JSON support.
    """
    start_template_name = serializers.SlugRelatedField(
        slug_field='element_name',
        source='start_template',
        read_only=True
    )
    nodes_count = serializers.SerializerMethodField()
    edges_count = serializers.SerializerMethodField()
    active_session_count = serializers.SerializerMethodField()
    
    class Meta:
        model = ChatFlow
        fields = '__all__'
        
    def get_nodes_count(self, obj):
        return obj.nodes.count()
        
    def get_edges_count(self, obj):
        return obj.edges.count()

    def get_active_session_count(self, obj):
        from .models import UserChatFlowSession
        return UserChatFlowSession.objects.filter(
            flow=obj,
            is_active=True
        ).count()    
    
    def validate_flow_data(self, value):
        """
        Validate ReactFlow JSON structure.
        """
        if not isinstance(value, dict):
            raise serializers.ValidationError("Flow data must be a valid JSON object.")
            
        required_keys = ['nodes', 'edges']
        for key in required_keys:
            if key not in value:
                raise serializers.ValidationError(f"Flow data must contain '{key}' field.")
                
        if not isinstance(value['nodes'], list):
            raise serializers.ValidationError("Flow data 'nodes' must be a list.")
            
        if not isinstance(value['edges'], list):
            raise serializers.ValidationError("Flow data 'edges' must be a list.")
            
        return value

class ChatFlowNodeSerializer(BaseSerializer):
    """
    Serializer for ChatFlowNode model.
    """
    template_name = serializers.SlugRelatedField(
        queryset=WATemplate.objects.filter(status=S.APPROVED),
        slug_field='element_name',
        source='template'
    )
    template_buttons = serializers.SerializerMethodField()

    def get_template_buttons(self, obj):
        if obj.template:
            return obj.template.buttons
        return []
    
    class Meta:
        model = ChatFlowNode
        fields = '__all__'
    

class ChatFlowEdgeSerializer(BaseSerializer):
    """
    Serializer for ChatFlowEdge model.
    """
    source_template_name = serializers.SlugRelatedField(
        slug_field='element_name',
        source='source_node.template',
        read_only=True
    )
    target_template_name = serializers.SlugRelatedField(
        slug_field='element_name',
        source='target_node.template',
        read_only=True
    )

    class Meta:
        model = ChatFlowEdge
        fields = '__all__'
        
    def validate(self, data):
        """
        Validate that the button exists in the source template.
        """
        source_node = data['source_node']
        button_text = data['button_text']
        
        # Check if the button exists in the source template
        template_buttons = source_node.template.buttons or []
        
        button_exists = any(
            button.get('text') == button_text 
            for button in template_buttons
        )
        
        if not button_exists:
            raise serializers.ValidationError(
                f"Button '{button_text}' does not exist in source template '{source_node.template.element_name}'"
            )
            
        return data


class ApprovedTemplateSerializer(BaseSerializer):
    """
    Serializer for approved templates that can be used in chat flows.
    """
    buttons_count = serializers.SerializerMethodField()
    
    class Meta:
        model = WATemplate
        fields = (
            'id', 'name', 'element_name', 'language_code', 'category', 
            'template_type', 'status', 'buttons', 'content', 'buttons_count'
        )
        
    def get_buttons_count(self, obj):
        return len(obj.buttons) if obj.buttons else 0

class ChatFlowCreateUpdateSerializer(BaseSerializer):
    """
    Serializer for creating/updating chat flows from ReactFlow JSON.
    
    All create/update operations go through ChatFlowProcessor which:
    1. Validates the ReactFlow JSON structure with Pydantic
    2. Validates all referenced templates are APPROVED
    3. Creates/recreates ChatFlowNode and ChatFlowEdge records
    
    Update Strategy:
    When a flow is updated, all existing nodes and edges are deleted and recreated.
    This "delete and recreate" approach ensures consistency and works with the 
    "reset sessions on flow change" approach where active sessions are invalidated
    when they detect flow.updated_at has changed.
    """
    # start_template is inferred from first node in flow_data by the processor
    start_template = serializers.PrimaryKeyRelatedField(
        queryset=WATemplate.objects.filter(status=S.APPROVED),
        required=False,
        write_only=True  # Not needed in input, processor handles it
    )
    
    class Meta:
        model = ChatFlow
        fields = '__all__'
    
    def validate(self, data):
        """
        Validate flow_data. If explicitly set to empty, flow will be marked inactive.
        If not provided at all (PATCH), don't touch it.
        """
        # Check if flow_data was actually provided in the request
        # For PATCH requests, missing fields should not be processed
        if 'flow_data' not in self.initial_data:
            # flow_data not in request - don't modify anything related to it
            return data
        
        flow_data = data.get('flow_data')
        
        # flow_data explicitly set to empty/null - mark inactive
        if not flow_data:
            data['is_active'] = False
            data['_clear_flow'] = True  # Flag for update method
            return data
        
        if not isinstance(flow_data, dict):
            raise serializers.ValidationError({
                'flow_data': 'Must be a valid JSON object.'
            })
        
        # Empty nodes means incomplete flow - mark inactive
        nodes = flow_data.get('nodes', [])
        
        if not nodes:
            data['is_active'] = False
            data['_clear_flow'] = True
            return data
        
        return data
        
    def create(self, validated_data):
        """
        Create flow from ReactFlow JSON data using ChatFlowProcessor.
        
        If flow_data is empty/missing, creates an inactive empty flow.
        Uses the processor for full Pydantic validation, template approval checks,
        and consistent node/edge creation.
        """
        from .services.flow_processor import ChatFlowProcessor

        # Remove internal flag if present
        validated_data.pop('_clear_flow', None)
        
        flow_data = validated_data.get('flow_data')
        request = self.context.get('request')
        
        if not request:
            raise serializers.ValidationError("Request context is required")
        
        # Handle empty flow_data - create inactive flow without nodes/edges
        if not flow_data or not flow_data.get('nodes'):
            flow = ChatFlow.objects.create(
                name=validated_data.get('name', 'Untitled Flow'),
                description=validated_data.get('description', ''),
                flow_data=flow_data or {'nodes': [], 'edges': [], 'viewport': {'x': 0, 'y': 0, 'zoom': 1}},
                start_template=None,
                tenant=request.user.tenant,
                created_by=request.user,
                is_active=False
            )
            return flow
        
        try:
            return ChatFlowProcessor.create_flow_from_reactflow(
                flow_data=flow_data,
                tenant=request.user.tenant,
                created_by=request.user,
                name=validated_data.get('name'),
                description=validated_data.get('description'),
            )
        except ValueError as e:
            raise serializers.ValidationError({'flow_data': str(e)})

    def update(self, instance, validated_data):
        """
        Update flow from ReactFlow JSON data using ChatFlowProcessor.
        
        - If flow_data not in request (PATCH without flow_data): only update other fields
        - If flow_data explicitly empty: marks flow as inactive and clears nodes/edges
        - If flow_data has content: recreates nodes/edges from the new flow_data
        """
        from .services.flow_processor import ChatFlowProcessor

        # Check if we should clear the flow (explicitly set to empty)
        should_clear_flow = validated_data.pop('_clear_flow', False)
        
        # Check if flow_data was provided in request
        flow_data_in_request = 'flow_data' in self.initial_data
        
        request = self.context.get('request')
        
        # Update simple fields (name, description, is_active) regardless
        if 'name' in validated_data:
            instance.name = validated_data['name']
        if 'description' in validated_data:
            instance.description = validated_data['description']
        if 'is_active' in validated_data:
            instance.is_active = validated_data['is_active']
        
        # If flow_data wasn't in the request, just save other fields and return
        if not flow_data_in_request:
            instance.save()
            return instance
        
        flow_data = validated_data.get('flow_data')
        
        # Handle explicitly empty flow_data - mark inactive and clear nodes/edges
        if should_clear_flow or not flow_data or not flow_data.get('nodes'):
            instance.nodes.all().delete()  # Cascade deletes edges too
            instance.flow_data = flow_data or {'nodes': [], 'edges': [], 'viewport': {'x': 0, 'y': 0, 'zoom': 1}}
            instance.start_template = None
            instance.is_active = False
            instance.save()
            return instance
        
        # flow_data has content - use processor to update
        if instance.start_template:
            tenant = instance.start_template.wa_app.tenant
        else:   
            tenant = request.user.tenant
        
        try:
            updated = ChatFlowProcessor.update_flow_from_reactflow(
                flow=instance,
                flow_data=flow_data,
                tenant=tenant
            )
            try:
                from notifications.signals import create_automation_notification
                create_automation_notification(updated, 'updated')
            except Exception:
                pass
            return updated
        except ValueError as e:
            raise serializers.ValidationError({'flow_data': str(e)})


class ButtonClickSerializer(serializers.Serializer):
    """
    Serializer for validating button click requests.
    
    This serializer handles validation for the process_button_click endpoint,
    ensuring all required fields are present and valid before processing
    the button click action.
    """
    user_phone = serializers.CharField(
        max_length=20,
        help_text="User's WhatsApp phone number in international format (e.g., +1234567890)"
    )
    button_id = serializers.CharField(
        max_length=255,
        help_text="Unique identifier for the button that was clicked"
    )
    button_text = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        help_text="Optional text of the button for analytics purposes"
    )
    
    def validate_user_phone(self, value):
        """
        Validate phone number format.
        """
        if not value.startswith('+'):
            raise serializers.ValidationError(
                "Phone number must start with '+' and include country code"
            )
        
        # Remove + and check if remaining characters are digits
        phone_digits = value[1:]
        if not phone_digits.isdigit():
            raise serializers.ValidationError(
                "Phone number must contain only digits after the '+' sign"
            )
            
        if len(phone_digits) < 10 or len(phone_digits) > 15:
            raise serializers.ValidationError(
                "Phone number must be between 10 and 15 digits (excluding country code)"
            )
            
        return value
    
    def validate_button_id(self, value):
        """
        Validate button_id format.
        """
        if not value.strip():
            raise serializers.ValidationError("Button ID cannot be empty or whitespace")
            
        # Optional: Add pattern validation for button IDs
        # if not re.match(r'^[a-zA-Z0-9_-]+$', value):
        #     raise serializers.ValidationError(
        #         "Button ID can only contain letters, numbers, hyphens, and underscores"
        #     )
            
        return value.strip()
    
    def validate(self, attrs):
        """
        Cross-field validation.
        """
        # If button_text is provided, ensure it's not just whitespace
        button_text = attrs.get('button_text')
        if button_text and not button_text.strip():
            attrs['button_text'] = None  # Treat whitespace as None
            
        return attrs
