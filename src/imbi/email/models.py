"""Data models for email sending and audit logging."""

import datetime
import typing

import pydantic


class EmailMessage(pydantic.BaseModel):
    """Email message to be sent via SMTP."""

    model_config = pydantic.ConfigDict(extra='ignore')

    to_email: pydantic.EmailStr
    subject: str
    html_body: str
    text_body: str
    template_name: str
    context: dict[str, typing.Any] = {}


class EmailAudit(pydantic.BaseModel):
    """Audit log entry for sent emails (stored in ClickHouse)."""

    model_config = pydantic.ConfigDict(extra='ignore')

    to_email: pydantic.EmailStr
    template_name: str
    subject: str
    status: typing.Literal['sent', 'failed', 'skipped', 'dry_run']
    error_message: str | None = None
    sent_at: datetime.datetime

    # Metadata for analytics
    user_id: str | None = None
    related_entity_type: str | None = None
    related_entity_id: str | None = None
