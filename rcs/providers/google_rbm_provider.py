"""Google RBM (RCS Business Messaging) provider implementation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

import requests
from django.core.cache import cache

from rcs.providers.base import (
    BaseRCSProvider,
    RCSCapability,
    RCSEventReport,
    RCSInboundMessage,
    RCSSendResult,
)

logger = logging.getLogger(__name__)


class GoogleRBMProvider(BaseRCSProvider):
    PROVIDER_NAME = "GOOGLE_RBM"
    API_BASE = "https://rcsbusinessmessaging.googleapis.com"

    def _get_access_token(self) -> str:
        """Get cached OAuth2 access token from service account."""
        cache_key = f"rcs:google:token:{self.rcs_app.pk}"
        token = cache.get(cache_key)
        if token:
            return token

        import google.auth.transport.requests
        from google.oauth2 import service_account as sa

        creds = sa.Credentials.from_service_account_info(
            self.credentials.get("service_account_json", {}),
            scopes=["https://www.googleapis.com/auth/rcsbusinessmessaging"],
        )
        creds.refresh(google.auth.transport.requests.Request())
        cache.set(cache_key, creds.token, timeout=3300)  # 55 min
        return creds.token

    def send_message(
        self,
        to_phone: str,
        content_message: Dict[str, Any],
        *,
        message_id: Optional[str] = None,
        traffic_type: str = "TRANSACTION",
        ttl: Optional[str] = None,
        **kwargs,
    ) -> RCSSendResult:
        msg_id = message_id or str(uuid.uuid4())
        url = f"{self.API_BASE}/v1/phones/{to_phone}/agentMessages"
        params = {"messageId": msg_id, "agentId": self.rcs_app.agent_id}
        body: Dict[str, Any] = {"contentMessage": content_message}
        if traffic_type:
            body["messageTrafficType"] = traffic_type
        if ttl:
            body["ttl"] = ttl

        headers = {"Authorization": f"Bearer {self._get_access_token()}"}

        try:
            resp = requests.post(url, json=body, params=params, headers=headers, timeout=30)
        except requests.RequestException as exc:
            logger.exception("Google RBM send failed: %s", exc)
            return RCSSendResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=str(exc),
            )

        if resp.status_code == 404:
            return RCSSendResult(
                success=False,
                provider=self.PROVIDER_NAME,
                is_rcs_capable=False,
                error_code="404",
                error_message="User not RCS-capable",
            )
        if resp.status_code >= 400:
            return RCSSendResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_code=str(resp.status_code),
                error_message=resp.text,
                raw_response=resp.json() if resp.text else None,
            )

        return RCSSendResult(
            success=True,
            provider=self.PROVIDER_NAME,
            message_id=msg_id,
            raw_response=resp.json(),
        )

    def send_event(
        self,
        to_phone: str,
        event_type: str,
        *,
        message_id: Optional[str] = None,
    ) -> RCSSendResult:
        event_id = str(uuid.uuid4())
        url = f"{self.API_BASE}/v1/phones/{to_phone}/agentEvents"
        params = {"eventId": event_id, "agentId": self.rcs_app.agent_id}
        body: Dict[str, Any] = {"eventType": event_type}
        if message_id:
            body["messageId"] = message_id

        headers = {"Authorization": f"Bearer {self._get_access_token()}"}

        try:
            resp = requests.post(url, json=body, params=params, headers=headers, timeout=15)
        except requests.RequestException as exc:
            logger.exception("Google RBM event send failed: %s", exc)
            return RCSSendResult(success=False, provider=self.PROVIDER_NAME, error_message=str(exc))

        if resp.status_code >= 400:
            return RCSSendResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_code=str(resp.status_code),
                error_message=resp.text,
            )

        return RCSSendResult(success=True, provider=self.PROVIDER_NAME, message_id=event_id)

    def revoke_message(self, to_phone: str, message_id: str) -> RCSSendResult:
        url = f"{self.API_BASE}/v1/phones/{to_phone}/agentMessages/{message_id}"
        params = {"agentId": self.rcs_app.agent_id}
        headers = {"Authorization": f"Bearer {self._get_access_token()}"}

        try:
            resp = requests.delete(url, params=params, headers=headers, timeout=15)
        except requests.RequestException as exc:
            logger.exception("Google RBM revoke failed: %s", exc)
            return RCSSendResult(success=False, provider=self.PROVIDER_NAME, error_message=str(exc))

        if resp.status_code >= 400:
            return RCSSendResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_code=str(resp.status_code),
                error_message=resp.text,
            )

        return RCSSendResult(success=True, provider=self.PROVIDER_NAME, message_id=message_id, status="REVOKED")

    def check_capability(self, phone: str) -> RCSCapability:
        url = f"{self.API_BASE}/v1/phones/{phone}/capabilities"
        params = {"requestId": str(uuid.uuid4()), "agentId": self.rcs_app.agent_id}
        headers = {"Authorization": f"Bearer {self._get_access_token()}"}

        try:
            resp = requests.get(url, params=params, headers=headers, timeout=15)
        except requests.RequestException as exc:
            logger.exception("Google RBM capability check failed: %s", exc)
            return RCSCapability(phone=phone, is_rcs_enabled=False)

        if resp.status_code == 404:
            return RCSCapability(phone=phone, is_rcs_enabled=False)

        data = resp.json()
        features = data.get("features", [])
        return RCSCapability(phone=phone, is_rcs_enabled=True, features=features, raw_response=data)

    def batch_check_capability(self, phones: List[str]) -> Dict[str, RCSCapability]:
        if not phones:
            return {}

        url = f"{self.API_BASE}/v1/users:batchGet"
        params = {"requestId": str(uuid.uuid4()), "agentId": self.rcs_app.agent_id}
        headers = {"Authorization": f"Bearer {self._get_access_token()}"}
        body = {"users": [{"phoneNumber": p} for p in phones]}

        try:
            resp = requests.post(url, json=body, params=params, headers=headers, timeout=30)
            if resp.status_code >= 400:
                logger.error("Batch capability check failed: %s %s", resp.status_code, resp.text)
                return {p: RCSCapability(phone=p, is_rcs_enabled=False) for p in phones}

            data = resp.json()
            results = {}
            for user in data.get("users", []):
                phone = user["phoneNumber"]
                features = user.get("capabilities", [])
                results[phone] = RCSCapability(
                    phone=phone,
                    is_rcs_enabled=len(features) > 0,
                    features=features,
                    raw_response=user,
                )
            # Phones not in response → assume not capable
            for phone in phones:
                if phone not in results:
                    results[phone] = RCSCapability(phone=phone, is_rcs_enabled=False)
            return results
        except Exception:
            logger.exception("Batch capability check exception")
            return {p: RCSCapability(phone=p, is_rcs_enabled=False) for p in phones}

    def upload_file(self, file_url: str, thumbnail_url: Optional[str] = None) -> Dict[str, str]:
        url = f"{self.API_BASE}/upload/v1/files"
        headers = {"Authorization": f"Bearer {self._get_access_token()}"}
        body: Dict[str, Any] = {"fileUrl": file_url}
        if thumbnail_url:
            body["thumbnailUrl"] = thumbnail_url

        try:
            resp = requests.post(url, json=body, headers=headers, timeout=30)
            if resp.status_code >= 400:
                return {}
            return resp.json()
        except requests.RequestException:
            logger.exception("Google RBM file upload failed")
            return {}

    def parse_inbound_webhook(self, payload: Dict) -> RCSInboundMessage:
        sender_phone = payload.get("senderPhoneNumber", "")
        message_id = payload.get("messageId", "")

        if "text" in payload:
            return RCSInboundMessage(
                sender_phone=sender_phone,
                message_id=message_id,
                message_type="text",
                text=payload["text"],
                raw_payload=payload,
            )
        if "suggestionResponse" in payload:
            sr = payload["suggestionResponse"]
            return RCSInboundMessage(
                sender_phone=sender_phone,
                message_id=message_id,
                message_type="suggestion_response",
                postback_data=sr.get("postbackData", ""),
                suggestion_text=sr.get("text", ""),
                raw_payload=payload,
            )
        if "location" in payload:
            return RCSInboundMessage(
                sender_phone=sender_phone,
                message_id=message_id,
                message_type="location",
                location=payload["location"],
                raw_payload=payload,
            )
        if "userFile" in payload:
            return RCSInboundMessage(
                sender_phone=sender_phone,
                message_id=message_id,
                message_type="file",
                file_info=payload["userFile"],
                raw_payload=payload,
            )

        return RCSInboundMessage(
            sender_phone=sender_phone,
            message_id=message_id,
            message_type="unknown",
            raw_payload=payload,
        )

    def parse_event_webhook(self, payload: Dict) -> RCSEventReport:
        return RCSEventReport(
            sender_phone=payload.get("senderPhoneNumber", ""),
            message_id=payload.get("messageId", ""),
            event_type=payload.get("eventType", "UNKNOWN"),
            event_id=payload.get("eventId", ""),
            raw_payload=payload,
        )

    def validate_webhook_signature(self, request) -> bool:
        client_token = self.rcs_app.webhook_client_token
        if not client_token:
            return False
        signature = request.headers.get("X-Goog-Signature", "")
        if not signature:
            return False

        # Pub/Sub push: body has {"message": {"data": "<base64>"}}
        try:
            body = json.loads(request.body or b"{}")
            encoded_data = body.get("message", {}).get("data", "")
            decoded_data = base64.b64decode(encoded_data)
        except Exception:
            return False

        computed = base64.b64encode(
            hmac.new(client_token.encode("utf-8"), decoded_data, hashlib.sha512).digest()
        ).decode("utf-8")

        return hmac.compare_digest(signature, computed)
