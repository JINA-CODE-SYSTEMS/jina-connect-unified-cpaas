import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


class TemplateNotificationService:
    """
    Service to send email notifications for template-related events.
    """
    
    @staticmethod
    def send_category_change_notification(template, old_category: str, new_category: str):
        """
        Send email notification when a template's category changes.
        This is important as category changes affect pricing.
        
        Args:
            template: WATemplate instance
            old_category: Previous category (e.g., 'UTILITY')
            new_category: New category (e.g., 'MARKETING')
            
        Returns:
            bool: True if email sent successfully, False otherwise
        """
        try:
            # Get tenant users to notify
            tenant = template.wa_app.tenant
            tenant_users = tenant.tenant_users.select_related('user').all()
            
            if not tenant_users:
                logger.warning(f"No tenant users found for tenant {tenant.pk}")
                return False
            
            # Get recipient emails
            recipient_emails = [
                tu.user.email for tu in tenant_users 
                if tu.user.email and tu.user.is_active
            ]
            
            if not recipient_emails:
                logger.warning(f"No active users with email found for tenant {tenant.pk}")
                return False
            
            # Determine pricing impact message
            pricing_impact = ""
            if old_category == "UTILITY" and new_category == "MARKETING":
                pricing_impact = """
                <p style="background-color: #FEF3C7; border: 1px solid #F59E0B; padding: 12px; border-radius: 6px; color: #92400E;">
                    <strong>⚠️ Pricing Impact:</strong> Marketing messages typically cost more than Utility messages. 
                    Please review your messaging costs accordingly.
                </p>
                """
            elif old_category == "MARKETING" and new_category == "UTILITY":
                pricing_impact = """
                <p style="background-color: #D1FAE5; border: 1px solid #10B981; padding: 12px; border-radius: 6px; color: #065F46;">
                    <strong>✅ Pricing Impact:</strong> Utility messages typically cost less than Marketing messages. 
                    Your messaging costs may decrease.
                </p>
                """
            
            subject = f"Template Category Changed: {template.element_name}"
            
            html_message = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                    .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                    .header {{ background-color: #F59E0B; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0; }}
                    .content {{ background-color: #f9fafb; padding: 30px; border: 1px solid #e5e7eb; }}
                    .info-box {{ background-color: white; border: 1px solid #e5e7eb; padding: 15px; border-radius: 6px; margin: 15px 0; }}
                    .label {{ color: #6b7280; font-size: 12px; text-transform: uppercase; }}
                    .value {{ font-size: 16px; font-weight: bold; color: #111827; }}
                    .change {{ display: flex; align-items: center; justify-content: center; gap: 10px; margin: 20px 0; }}
                    .category {{ padding: 8px 16px; border-radius: 20px; font-weight: bold; }}
                    .old {{ background-color: #FEE2E2; color: #991B1B; }}
                    .new {{ background-color: #D1FAE5; color: #065F46; }}
                    .arrow {{ font-size: 24px; color: #6b7280; }}
                    .footer {{ text-align: center; padding: 20px; color: #6b7280; font-size: 12px; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>📋 Template Category Changed</h1>
                    </div>
                    <div class="content">
                        <p>A WhatsApp template category has been changed by Meta. This may affect your messaging costs.</p>
                        
                        <div class="info-box">
                            <p class="label">Template Name</p>
                            <p class="value">{template.element_name}</p>
                        </div>
                        
                        <div class="info-box">
                            <p class="label">Category Change</p>
                            <div class="change">
                                <span class="category old">{old_category}</span>
                                <span class="arrow">→</span>
                                <span class="category new">{new_category}</span>
                            </div>
                        </div>
                        
                        {pricing_impact}
                        
                        <div class="info-box">
                            <p class="label">Template Details</p>
                            <p><strong>Language:</strong> {template.language_code}</p>
                            <p><strong>Type:</strong> {template.template_type}</p>
                            <p><strong>Status:</strong> {template.status}</p>
                        </div>
                        
                        <p style="color: #6b7280; font-size: 14px; margin-top: 20px;">
                            This change was made by Meta/WhatsApp and is automatically synced to your account.
                            No action is required on your part unless you wish to update your template.
                        </p>
                    </div>
                    <div class="footer">
                        <p>&copy; 2024 Jina Connect. All rights reserved.</p>
                        <p>Tenant: {tenant.name}</p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            plain_message = f"""
Template Category Changed: {template.element_name}

A WhatsApp template category has been changed by Meta. This may affect your messaging costs.

Template Name: {template.element_name}
Category Change: {old_category} → {new_category}
Language: {template.language_code}
Type: {template.template_type}
Status: {template.status}

This change was made by Meta/WhatsApp and is automatically synced to your account.

Best regards,
The Jina Connect Team
            """
            
            from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@jinaconnect.com')
            
            send_mail(
                subject=subject,
                message=plain_message,
                from_email=from_email,
                recipient_list=recipient_emails,
                html_message=html_message,
                fail_silently=False,
            )
            
            logger.info(f"Category change notification sent to {len(recipient_emails)} users for template {template.element_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send category change notification: {str(e)}")
            return False
    
    @staticmethod
    def send_status_change_notification(template, old_status: str, new_status: str, reason: str = None):
        """
        Send email notification when a template's status changes (approved/rejected).
        
        Args:
            template: WATemplate instance
            old_status: Previous status
            new_status: New status (APPROVED, REJECTED, etc.)
            reason: Rejection/failure reason if any
            
        Returns:
            bool: True if email sent successfully, False otherwise
        """
        try:
            tenant = template.wa_app.tenant
            tenant_users = tenant.tenant_users.select_related('user').all()
            
            if not tenant_users:
                return False
            
            recipient_emails = [
                tu.user.email for tu in tenant_users 
                if tu.user.email and tu.user.is_active
            ]
            
            if not recipient_emails:
                return False
            
            # Determine header color and icon based on status
            if new_status == "APPROVED":
                header_color = "#10B981"
                icon = "✅"
                status_message = "Your template has been approved and is ready to use."
            elif new_status == "REJECTED":
                header_color = "#EF4444"
                icon = "❌"
                status_message = "Your template has been rejected. Please review the reason below and make necessary changes."
            elif new_status == "FAILED":
                header_color = "#EF4444"
                icon = "⚠️"
                status_message = "Your template submission failed. Please review the error and try again."
            else:
                header_color = "#6B7280"
                icon = "📋"
                status_message = f"Your template status has changed to {new_status}."
            
            reason_html = ""
            if reason:
                reason_html = f"""
                <div style="background-color: #FEE2E2; border: 1px solid #EF4444; padding: 15px; border-radius: 6px; margin: 15px 0;">
                    <p style="margin: 0; color: #991B1B;"><strong>Reason:</strong></p>
                    <p style="margin: 5px 0 0 0; color: #7F1D1D;">{reason}</p>
                </div>
                """
            
            subject = f"Template {new_status}: {template.element_name}"
            
            html_message = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                    .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                    .header {{ background-color: {header_color}; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0; }}
                    .content {{ background-color: #f9fafb; padding: 30px; border: 1px solid #e5e7eb; }}
                    .info-box {{ background-color: white; border: 1px solid #e5e7eb; padding: 15px; border-radius: 6px; margin: 15px 0; }}
                    .footer {{ text-align: center; padding: 20px; color: #6b7280; font-size: 12px; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>{icon} Template {new_status}</h1>
                    </div>
                    <div class="content">
                        <p>{status_message}</p>
                        
                        <div class="info-box">
                            <p><strong>Template Name:</strong> {template.element_name}</p>
                            <p><strong>Language:</strong> {template.language_code}</p>
                            <p><strong>Category:</strong> {template.category}</p>
                            <p><strong>Type:</strong> {template.template_type}</p>
                        </div>
                        
                        {reason_html}
                    </div>
                    <div class="footer">
                        <p>&copy; 2024 Jina Connect. All rights reserved.</p>
                        <p>Tenant: {tenant.name}</p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            plain_message = f"""
Template {new_status}: {template.element_name}

{status_message}

Template Name: {template.element_name}
Language: {template.language_code}
Category: {template.category}
Type: {template.template_type}
{f"Reason: {reason}" if reason else ""}

Best regards,
The Jina Connect Team
            """
            
            from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@jinaconnect.com')
            
            send_mail(
                subject=subject,
                message=plain_message,
                from_email=from_email,
                recipient_list=recipient_emails,
                html_message=html_message,
                fail_silently=False,
            )
            
            logger.info(f"Status change notification sent for template {template.element_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send status change notification: {str(e)}")
            return False
