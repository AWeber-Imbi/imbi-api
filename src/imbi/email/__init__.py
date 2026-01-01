"""Email sending module for Imbi transactional emails.

This module provides email sending capabilities including:
- Password reset emails
- Welcome emails for new users
- Email verification
- Security alerts

The module uses SMTP for email delivery with retry logic and dead letter queue
for failed emails. All emails are rendered using Jinja2 templates with both
HTML and plain text versions.
"""

import logging

from imbi import clickhouse

from . import client, models, templates

LOGGER = logging.getLogger(__name__)

__all__ = [
    'aclose',
    'initialize',
    'send_welcome_email',
]


async def initialize() -> None:
    """Initialize the email module.

    This should be called during application startup to:
    - Validate email settings
    - Initialize the email client singleton
    - Initialize the template manager singleton

    """
    LOGGER.info('Initializing email module')

    # Initialize singletons by getting instances
    client.EmailClient.get_instance()
    templates.TemplateManager.get_instance()

    LOGGER.info('Email module initialized')


async def aclose() -> None:
    """Clean up email module resources.

    This should be called during application shutdown.

    """
    LOGGER.info('Closing email module')

    # Reset singletons
    client.EmailClient._instance = None
    templates.TemplateManager._instance = None

    LOGGER.info('Email module closed')


async def send_welcome_email(
    username: str,
    email: str,
    display_name: str,
    login_url: str,
) -> models.EmailAudit:
    """Send a welcome email to a new user.

    Args:
        username: User's username
        email: User's email address
        display_name: User's display name for personalization
        login_url: URL for user to log in

    Returns:
        EmailAudit record with send status

    """
    LOGGER.info('Sending welcome email to %s', email)

    # Render template
    template_manager = templates.TemplateManager.get_instance()
    message = template_manager.render_email(
        'welcome',
        {
            'to_email': email,
            'username': username,
            'display_name': display_name,
            'login_url': login_url,
        },
    )

    # Send email
    email_client = client.EmailClient.get_instance()
    audit = await email_client.send_email(message)

    # Save audit to ClickHouse
    await _save_audit(audit)

    LOGGER.info(
        'Welcome email to %s: status=%s',
        email,
        audit.status,
    )

    return audit


async def _save_audit(audit: models.EmailAudit) -> None:
    """Save email audit record to ClickHouse.

    Args:
        audit: Email audit record to save

    """
    try:
        await clickhouse.insert('email_audit', [audit])
        LOGGER.debug('Email audit saved to ClickHouse: %s', audit.to_email)
    except clickhouse.client.DatabaseError as err:
        # Log error but don't fail the email send
        LOGGER.warning(
            'Failed to save email audit to ClickHouse: %s',
            err,
        )
