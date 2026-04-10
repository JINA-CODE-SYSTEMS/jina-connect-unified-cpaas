"""
Alternative authentication methods for WebSocket connections
"""

import secrets
import string
from datetime import datetime, timedelta

from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.core.cache import cache

User = get_user_model()


class WebSocketSessionManager:
    """
    Session-based WebSocket authentication as alternative to JWT
    """
    
    def generate_websocket_session(self, user, tenant_id, expires_in=3600):
        """
        Generate a temporary WebSocket session token
        More secure than long-lived JWT tokens
        """
        # Generate cryptographically secure session token
        session_token = ''.join(secrets.choice(
            string.ascii_letters + string.digits
        ) for _ in range(32))
        
        # Store session data in Redis with expiration
        session_data = {
            'user_id': user.id,
            'tenant_id': tenant_id,
            'created_at': datetime.now().isoformat(),
            'expires_at': (datetime.now() + timedelta(seconds=expires_in)).isoformat(),
            'client_info': {}
        }
        
        cache_key = f"ws_session:{session_token}"
        cache.set(cache_key, session_data, timeout=expires_in)
        
        return session_token
    
    async def authenticate_session(self, session_token):
        """
        Authenticate using session token
        """
        cache_key = f"ws_session:{session_token}"
        session_data = cache.get(cache_key)
        
        if not session_data:
            return None, "Invalid or expired session"
        
        # Check if session is still valid
        expires_at = datetime.fromisoformat(session_data['expires_at'])
        if datetime.now() > expires_at:
            cache.delete(cache_key)
            return None, "Session expired"
        
        # Get user
        user = await self.get_user_by_id(session_data['user_id'])
        if not user:
            return None, "User not found"
        
        return user, None
    
    def revoke_session(self, session_token):
        """
        Immediately revoke a session (for logout)
        """
        cache_key = f"ws_session:{session_token}"
        cache.delete(cache_key)
    
    def refresh_session(self, session_token, extends_by=3600):
        """
        Extend session expiration
        """
        cache_key = f"ws_session:{session_token}"
        session_data = cache.get(cache_key)
        
        if session_data:
            # Extend expiration
            new_expires = datetime.now() + timedelta(seconds=extends_by)
            session_data['expires_at'] = new_expires.isoformat()
            cache.set(cache_key, session_data, timeout=extends_by)
            return True
        
        return False
    
    @database_sync_to_async
    def get_user_by_id(self, user_id):
        try:
            return User.objects.get(id=user_id, is_active=True)
        except User.DoesNotExist:
            return None


class APIKeyAuth:
    """
    API Key based authentication for server-to-server WebSocket connections
    """
    
    @database_sync_to_async
    def authenticate_api_key(self, api_key, tenant_id):
        """
        Authenticate using API key for server connections
        """
        from tenants.models import TenantApiKey
        
        try:
            api_key_obj = TenantApiKey.objects.get(
                key=api_key,
                tenant_id=tenant_id,
                is_active=True
            )
            
            # Check permissions
            if not api_key_obj.has_websocket_permission:
                return None, "API key lacks WebSocket permission"
            
            # Check rate limits for API key
            if not self.check_api_key_rate_limit(api_key):
                return None, "API key rate limit exceeded"
            
            return api_key_obj, None
            
        except TenantApiKey.DoesNotExist:
            return None, "Invalid API key"
    
    def check_api_key_rate_limit(self, api_key):
        """
        Rate limiting for API key connections
        """
        cache_key = f"api_key_rate_limit:{api_key}"
        current_requests = cache.get(cache_key, 0)
        
        max_requests = 1000  # per hour
        if current_requests >= max_requests:
            return False
        
        if current_requests == 0:
            cache.set(cache_key, 1, timeout=3600)
        else:
            cache.incr(cache_key)
        
        return True


# Example REST endpoint to get WebSocket session token
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def get_websocket_session(request):
    '''
    Get a temporary WebSocket session token
    POST /api/websocket-session/
    {
        "tenant_id": 1,
        "expires_in": 3600
    }
    '''
    tenant_id = request.data.get('tenant_id')
    expires_in = request.data.get('expires_in', 3600)  # 1 hour default
    
    # Validate tenant access
    if not TenantUser.objects.filter(
        user=request.user, 
        tenant_id=tenant_id
    ).exists():
        return Response({'error': 'Unauthorized tenant access'}, status=403)
    
    # Generate session token
    session_manager = WebSocketSessionManager()
    session_token = session_manager.generate_websocket_session(
        request.user, 
        tenant_id, 
        expires_in
    )
    
    return Response({
        'session_token': session_token,
        'expires_in': expires_in,
        'websocket_url': f'ws://localhost:8001/ws/team-inbox/{tenant_id}/?session={session_token}'
    })
"""