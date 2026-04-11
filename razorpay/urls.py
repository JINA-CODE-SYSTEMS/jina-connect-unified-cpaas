from django.urls import include, path
from rest_framework.routers import DefaultRouter

from razorpay.viewsets.razor_pay import RazorPayViewSet
from razorpay.viewsets.razor_pay_webhook import RazorpayWebhookViewSet

router = DefaultRouter()
router.register(r"razor-pay", RazorPayViewSet, basename="razorpay")
router.register(r"razor-webhook", RazorpayWebhookViewSet, basename="razorwebhook")


urlpatterns = [
    path("", include(router.urls)),
]
