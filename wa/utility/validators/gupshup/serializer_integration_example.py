"""
Example usage of Template Button Validation in Django REST Framework Serializers

This file demonstrates how to integrate the TemplateButtonsInput validation
with Django REST Framework serializers for the GupshupTemplate model.
"""

from rest_framework import serializers
from wa.serializers import (GupshupTemplateCreateSerializer,
                            GupshupTemplateSerializer)


class ExampleUsage:
    """
    Examples of how to use the template button validation in serializers.
    """
    
    @staticmethod
    def validate_template_buttons_example():
        """
        Example 1: Basic button validation
        """
        # Valid button configuration
        valid_buttons = [
            {
                "type": "PHONE_NUMBER",
                "text": "Call Support",
                "phone_number": "+919876543210"
            },
            {
                "type": "URL", 
                "text": "Visit Website",
                "url": "https://example.com/{{1}}",
                "example": ["https://example.com/promo"]
            },
            {
                "type": "OTP",
                "text": "Copy Code", 
                "otp_type": "COPY_CODE"
            }
        ]
        
        # This will be automatically validated by TemplateButtonsField
        template_data = {
            "element_name": "welcome_template",
            "language_code": "en_US",
            "category": "UTILITY", 
            "template_type": "TEXT",
            "buttons": valid_buttons
        }
        
        serializer = GupshupTemplateSerializer(data=template_data, partial=True)
        
        if serializer.is_valid():
            print("✅ Button validation passed!")
            return serializer.validated_data
        else:
            print("❌ Button validation failed:")
            for field, errors in serializer.errors.items():
                print(f"  {field}: {errors}")
            return None
    
    @staticmethod
    def common_validation_errors():
        """
        Example 2: Common validation errors and how they're handled
        """
        
        error_examples = {
            "missing_phone_number": {
                "data": [{"type": "PHONE_NUMBER", "text": "Call"}],
                "expected_error": "phone_number is required for PHONE_NUMBER type buttons"
            },
            
            "missing_url": {
                "data": [{"type": "URL", "text": "Visit"}], 
                "expected_error": "url is required for URL type buttons"
            },
            
            "invalid_url_format": {
                "data": [{"type": "URL", "text": "Visit", "url": "not-a-url"}],
                "expected_error": "URL must start with http:// or https://"
            },
            
            "too_many_buttons": {
                "data": [
                    {"type": "URL", "text": "A", "url": "https://a.com"},
                    {"type": "URL", "text": "B", "url": "https://b.com"}, 
                    {"type": "URL", "text": "C", "url": "https://c.com"},
                    {"type": "URL", "text": "D", "url": "https://d.com"}
                ],
                "expected_error": "Maximum 3 buttons allowed per template"
            },
            
            "missing_otp_type": {
                "data": [{"type": "OTP", "text": "Verify"}],
                "expected_error": "otp_type is required for OTP type buttons"
            },
            
            "one_tap_missing_fields": {
                "data": [{"type": "OTP", "text": "Verify", "otp_type": "ONE_TAP"}],
                "expected_error": "package_name is required for ONE_TAP OTP buttons"
            }
        }
        
        return error_examples
    
    @staticmethod
    def integration_with_views():
        """
        Example 3: How to use in Django REST Framework views
        """
        from rest_framework import status
        from rest_framework.response import Response
        from rest_framework.views import APIView
        
        class TemplateCreateView(APIView):
            def post(self, request):
                serializer = GupshupTemplateCreateSerializer(data=request.data)
                
                if serializer.is_valid():
                    # Button validation passed automatically
                    template = serializer.save()
                    
                    # You can get validation summary
                    button_summary = serializer.get_validation_summary(template)
                    
                    return Response({
                        "message": "Template created successfully",
                        "template_id": template.id,
                        "button_validation": button_summary
                    }, status=status.HTTP_201_CREATED)
                
                else:
                    # Validation errors will include detailed button errors
                    return Response({
                        "message": "Validation failed",
                        "errors": serializer.errors
                    }, status=status.HTTP_400_BAD_REQUEST)
        
        return TemplateCreateView
    
    @staticmethod
    def custom_validation_logic():
        """
        Example 4: Adding custom validation logic
        """
        
        class CustomTemplateSerializer(GupshupTemplateSerializer):
            
            def validate(self, data):
                # Call parent validation first (includes button validation)
                data = super().validate(data)
                
                # Add custom business logic
                category = data.get('category')
                buttons = data.get('buttons', [])
                
                # Custom rule: AUTHENTICATION templates with OTP buttons
                otp_buttons = [btn for btn in buttons if btn.get('type') == 'OTP']
                if otp_buttons and category != 'AUTHENTICATION':
                    raise serializers.ValidationError({
                        'buttons': 'OTP buttons are only allowed in AUTHENTICATION templates'
                    })
                
                # Custom rule: MARKETING templates should have URL buttons
                if category == 'MARKETING':
                    url_buttons = [btn for btn in buttons if btn.get('type') == 'URL']
                    if not url_buttons:
                        raise serializers.ValidationError({
                            'buttons': 'MARKETING templates should include at least one URL button'
                        })
                
                return data
        
        return CustomTemplateSerializer


# Usage examples for testing
if __name__ == "__main__":
    # Test basic validation
    ExampleUsage.validate_template_buttons_example()
    
    # Show common errors
    errors = ExampleUsage.common_validation_errors()
    print("Common validation errors:", list(errors.keys()))
