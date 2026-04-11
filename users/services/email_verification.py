import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


class EmailVerificationService:
    """
    Service to handle email verification for user registration.

    Deep Linking Support:
    - Uses Universal Links (iOS) and App Links (Android) format
    - If mobile app is installed, link opens directly in the app
    - If app is not installed, falls back to web page with options
    """

    @staticmethod
    def get_verification_url(token, use_deep_link=True):
        """
        Generate the verification URL.

        Args:
            token: The verification token string
            use_deep_link: If True, uses backend URL (recommended - backend handles redirect)
                          If False, uses direct frontend URL

        Returns:
            str: The verification URL

        Flow:
            1. Email contains link to backend: http://localhost:8000/verify-email/{token}/
            2. If mobile app installed: App intercepts via Universal/App Links
            3. If web/mobile browser: Backend redirects to http://localhost:3000/verify-email?token={token}
        """
        if use_deep_link:
            # Use the backend site URL - backend handles redirect logic
            site_url = getattr(settings, "SITE_URL", "http://localhost:8000")
            return f"{site_url}/verify-email/{token}/"
        else:
            # Direct frontend URL format (bypasses backend redirect logic)
            frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
            return f"{frontend_url}/verify-email?token={token}"

    @staticmethod
    def send_verification_email(user, token):
        """
        Send verification email to user.

        Args:
            user: User instance
            token: EmailVerificationToken instance

        Returns:
            bool: True if email sent successfully, False otherwise
        """
        verification_url = EmailVerificationService.get_verification_url(token.token)

        subject = "Verify your email address - Jina Connect"

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
                .warning {{ color: #9CA3AF; font-size: 12px; margin-top: 20px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>Welcome to Jina Connect!</h1>
                </div>
                <div class="content">
                    <h2>Hi {user.first_name},</h2>
                    <p>Thank you for registering with Jina Connect. To complete your registration and activate your account, please verify your email address by clicking the button below:</p>

                    <p style="text-align: center;">
                        <a href="{verification_url}" class="button" style="display: inline-block; background-color: #4F46E5; color: #ffffff !important; padding: 12px 24px; text-decoration: none; border-radius: 6px; margin: 20px 0;">Verify Email Address</a>
                    </p>

                    <p>Or copy and paste this link into your browser:</p>
                    <p style="word-break: break-all; color: #4F46E5;">{verification_url}</p>

                    <p class="warning">This link will expire in 24 hours. If you didn't create an account with Jina Connect, please ignore this email.</p>
                </div>
                <div class="footer">
                    <p>&copy; 2024 Jina Connect. All rights reserved.</p>
                </div>
            </div>
        </body>
        </html>
        """

        # Plain text fallback
        plain_message = f"""
Hi {user.first_name},

Thank you for registering with Jina Connect. To complete your registration and activate your account, please verify your email address by clicking the link below:

{verification_url}

This link will expire in 24 hours.

If you didn't create an account with Jina Connect, please ignore this email.

Best regards,
The Jina Connect Team
        """

        try:
            from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@jinaconnect.com")

            send_mail(
                subject=subject,
                message=plain_message,
                from_email=from_email,
                recipient_list=[user.email],
                html_message=html_message,
                fail_silently=False,
            )
            logger.info(f"Verification email sent to {user.email}")
            return True
        except Exception as e:
            logger.error(
                f"Failed to send verification email to {user.email}: [{type(e).__name__}] {str(e)}",
                exc_info=True,
            )
            return False

    @staticmethod
    def resend_verification_email(user):
        """
        Resend verification email to user.
        Creates a new token and sends email.

        Args:
            user: User instance

        Returns:
            tuple: (success: bool, message: str)
        """
        from users.models import EmailVerificationToken

        if user.is_active:
            return False, "Email is already verified."

        # Create new token
        token = EmailVerificationToken.create_for_user(user)

        # Send email
        if EmailVerificationService.send_verification_email(user, token):
            return True, "Verification email sent successfully."
        else:
            return False, "Failed to send verification email. Please try again later."
