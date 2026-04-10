from djmoney.models.fields import MoneyField as DjangoMoneyField
from moneyed import Money
from rest_framework import serializers
from rest_framework.request import HttpRequest


class BaseSerializer(serializers.ModelSerializer):
    """
    The base serializer to be used by our apps.
    """
    created_at = serializers.DateTimeField(read_only=True, format="%d %b %Y, %H:%M")
    updated_at = serializers.DateTimeField(read_only=True, format="%d %b %Y, %H:%M")
    # share user name for created by and updated by
    created_by = serializers.SlugRelatedField(
        read_only=True, slug_field="username"
    )
    updated_by = serializers.SlugRelatedField(
        read_only=True, slug_field="username"
    )


    class Meta:
        model = None
        fields = "__all__"
        read_only_fields = ["created_at", "updated_at", "id", "created_by", "updated_by"]
        extra_kwargs = {
            "id": {"read_only": True},
        }

    def create(self, validated_data):
        """
        Override create method to set created_by field.
        """
        request: HttpRequest = self.context.get("request")
        if request and hasattr(request, "user") and request.user.is_authenticated:
            validated_data["created_by"] = request.user
        return super().create(validated_data)

    def update(self, instance, validated_data):
        """
        Override update method to set updated_by field.
        """
        request: HttpRequest = self.context.get("request")
        if request and hasattr(request, "user") and request.user.is_authenticated:
            validated_data["updated_by"] = request.user
        return super().update(instance, validated_data)


class BaseWalletSerializer(serializers.ModelSerializer):
    class Meta:
        model = None
        fields = "__all__"
        read_only_fields = ["created_at", "updated_at", "id", "created_by", "updated_by"]
        extra_kwargs = {
            "id": {"read_only": True},
        }