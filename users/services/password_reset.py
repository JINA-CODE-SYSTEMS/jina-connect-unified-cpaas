"""
Password Reset Email Service for handling forgot password functionality.

Deep Linking Support:
- Uses Universal Links (iOS) and App Links (Android) format
- If mobile app is installed, link opens directly in the app
- If app is not installed, falls back to web page with options
"""

import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


class PasswordResetService:
    """
    Service to handle password reset emails.
    
    Deep Linking Support:
    - Uses Universal Links (iOS) and App Links (Android) format
    - If mobile app is installed, link opens directly in the app
    - If app is not installed, falls back to web page with options
    """
    
    @staticmethod
    def get_reset_url(token, use_deep_link=True):
        """
        Generate the password reset URL.
        
        Args:
            token: The password reset token string
            use_deep_link: If True, uses backend URL (recommended - backend handles redirect)
                          If False, uses direct frontend URL
        
        Returns:
            str: The password reset URL
        
        Flow:
            1. Email contains link to backend: http://localhost:8000/reset-password/{token}/
            2. If mobile app installed: App intercepts via Universal/App Links
            3. If web/mobile browser: Backend redirects to http://localhost:3000/reset-password?token={token}
        """
        if use_deep_link:
            # Use the backend site URL - backend handles redirect logic
            site_url = getattr(settings, 'SITE_URL', 'http://localhost:8000')
            return f"{site_url}/reset-password/{token}/"
        else:
            # Direct frontend URL format (bypasses backend redirect logic)
            frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
            return f"{frontend_url}/reset-password?token={token}"
    
    @staticmethod
    def send_password_reset_email(user, token):
        """
        Send password reset email to user.
        
        Args:
            user: User instance
            token: PasswordResetToken instance
            
        Returns:
            bool: True if email sent successfully, False otherwise
        """
        reset_url = PasswordResetService.get_reset_url(token.token)
        
        subject = "Reset your password - Jina Connect"
        
        # HTML message
        html_message = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background-color: #4F46E5; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0; }}
                .content {{ background-color: #f9fafb; padding: 30px; border: 1px solid #e5e7eb; }}
                .button {{ display: inline-block; background-color: #4F46E5; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; margin: 20px 0; }}
                .button:hover {{ background-color: #4338CA; }}
                .footer {{ text-align: center; padding: 20px; color: #6b7280; font-size: 12px; }}
                .warning {{ color: #DC2626; font-size: 14px; margin-top: 20px; padding: 15px; background-color: #FEF2F2; border-radius: 6px; }}
                .security-note {{ color: #9CA3AF; font-size: 12px; margin-top: 20px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>Password Reset Request</h1>
                </div>
                <div class="content">
                    <h2>Hi {user.first_name},</h2>
                    <p>We received a request to reset the password for your Jina Connect account associated with this email address.</p>
                    
                    <p>Click the button below to reset your password:</p>
                    
                    <p style="text-align: center;">
                        <a href="{reset_url}" class="button">Reset Password</a>
                    </p>
                    
                    <p>Or copy and paste this link into your browser:</p>
                    <p style="word-break: break-all; color: #4F46E5;">{reset_url}</p>
                    
                    <div class="warning">
                        <strong>⚠️ This link will expire in 1 hour.</strong>
                    </div>
                    
                    <p class="security-note">
                        If you didn't request a password reset, please ignore this email or contact support if you have concerns about your account security. Your password will remain unchanged.
                    </p>
                </div>
                <div class="footer">
                    <p>&copy; 2024 Jina Connect. All rights reserved.</p>
                    <p>This is an automated message. Please do not reply to this email.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Plain text fallback
        plain_message = f"""
Hi {user.first_name},

We received a request to reset the password for your Jina Connect account.

Click the link below to reset your password:
{reset_url}

This link will expire in 1 hour.

If you didn't request a password reset, please ignore this email. Your password will remain unchanged.

Best regards,
The Jina Connect Team
        """
        
        try:
            send_mail(
                subject=subject,
                message=plain_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                html_message=html_message,
                fail_silently=False,
            )
            logger.info(f"Password reset email sent to {user.email}")
            return True
        except Exception as e:
            logger.error(f"Failed to send password reset email to {user.email}: {str(e)}")
            return False
