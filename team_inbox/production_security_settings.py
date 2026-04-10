"""
Production security settings for WebSocket connections
Add these to your Django settings.py
"""

# WebSocket Security Configuration
WEBSOCKET_SECURITY = {
    # Authentication
    'AUTH_METHOD': 'session',  # 'jwt', 'session', 'hybrid'
    'SESSION_TIMEOUT': 3600,  # 1 hour
    'ALLOW_QUERY_TOKEN': False,  # Disable for production web apps
    
    # Origin Security
    'ALLOWED_ORIGINS': [
        'https://yourdomain.com',
        'https://app.yourdomain.com',
        # Don't include localhost in production!
    ],
    'STRICT_ORIGIN_CHECK': True,
    
    # Rate Limiting
    'RATE_LIMIT': {
        'CONNECTIONS_PER_USER': 3,
        'REQUESTS_PER_MINUTE': 60,
        'REQUESTS_PER_HOUR': 1000,
    },
    
    # IP Security
    'IP_WHITELIST': {
        # Optional: restrict sensitive tenants to specific IPs
        'enabled': False,
        'tenant_restrictions': {
            # tenant_id: [allowed_ips]
            # 1: ['192.168.1.100', '10.0.0.50']
        }
    },
    
    # Monitoring
    'LOG_FAILED_ATTEMPTS': True,
    'ALERT_ON_SUSPICIOUS_ACTIVITY': True,
    'MAX_FAILED_ATTEMPTS': 5,  # Before temporary IP ban
}

# Redis Configuration for Sessions & Rate Limiting
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': 'redis://127.0.0.1:6379/1',
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'PARSER_CLASS': 'redis.connection.HiredisParser',
            'CONNECTION_POOL_KWARGS': {
                'max_connections': 50,
                'retry_on_timeout': True,
            },
        },
    }
}

# Logging for Security Events
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'security_file': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': '/var/log/websocket_security.log',
            'maxBytes': 1024*1024*15,  # 15MB
            'backupCount': 10,
        },
    },
    'loggers': {
        'websocket.security': {
            'handlers': ['security_file'],
            'level': 'INFO',
            'propagate': True,
        },
    },
}

# HTTPS/WSS Configuration (Production)
SECURE_WEBSOCKET = {
    'USE_WSS': True,  # Force secure WebSocket connections
    'WSS_PORT': 443,
    'CERTIFICATE_PATH': '/path/to/ssl/cert.pem',
    'PRIVATE_KEY_PATH': '/path/to/ssl/private.key',
}

# Content Security Policy for WebSocket
CSP_CONNECT_SRC = [
    "'self'",
    "wss://yourdomain.com",
    "wss://app.yourdomain.com",
]

# Additional Security Headers
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'