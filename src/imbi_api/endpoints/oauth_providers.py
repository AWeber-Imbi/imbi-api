"""Admin CRUD endpoints for OAuth provider configuration."""

from __future__ import annotations

import logging
import typing

import fastapi
import pydantic
from imbi_common import graph

from imbi_api import settings
from imbi_api.auth import permissions
from imbi_api.auth import providers as oauth_providers
from imbi_api.domain import models

LOGGER = logging.getLogger(__name__)

oauth_providers_router = fastapi.APIRouter(
    prefix='/admin/oauth-providers',
    tags=['Admin', 'OAuth Providers'],
)


_ProviderType = typing.Literal['google', 'github', 'oidc']

_KNOWN_TYPES: tuple[_ProviderType, ...] = ('google', 'github', 'oidc')

_TYPE_DEFAULTS: dict[_ProviderType, tuple[str, str]] = {
    'google': ('Google', 'si-google'),
    'github': ('GitHub', 'si-github'),
    'oidc': ('OIDC', 'key-round'),
}


def _callback_url_for(slug: str) -> str:
    return settings.oauth_callback_url(slug)


class OAuthProviderRead(pydantic.BaseModel):
    """Read-only response model for OAuth providers.

    Never includes the encrypted secret — only ``has_secret`` so the
    UI can show whether a secret is configured.
    """

    slug: _ProviderType
    type: _ProviderType
    name: str
    enabled: bool
    configured: bool
    callback_url: str
    client_id: str | None = None
    issuer_url: str | None = None
    allowed_domains: list[str] = []
    icon: str = 'key-round'
    has_secret: bool = False

    @classmethod
    def from_model(cls, provider: models.OAuthProvider) -> OAuthProviderRead:
        return cls(
            slug=provider.slug,
            type=provider.type,
            name=provider.name,
            enabled=provider.enabled,
            configured=True,
            callback_url=_callback_url_for(provider.slug),
            client_id=provider.client_id,
            issuer_url=provider.issuer_url,
            allowed_domains=list(provider.allowed_domains),
            icon=provider.icon,
            has_secret=bool(provider.client_secret_encrypted),
        )

    @classmethod
    def blank(cls, slug: _ProviderType) -> OAuthProviderRead:
        name, icon = _TYPE_DEFAULTS[slug]
        return cls(
            slug=slug,
            type=slug,
            name=name,
            enabled=False,
            configured=False,
            callback_url=_callback_url_for(slug),
            icon=icon,
        )


class OAuthProviderWrite(pydantic.BaseModel):
    """Request body for ``PUT /admin/oauth-providers/{slug}``.

    ``client_secret`` is optional — when omitted or empty the
    existing encrypted secret on the row is preserved.  Otherwise
    the plaintext value is encrypted via ``TokenEncryption`` before
    being stored.
    """

    type: _ProviderType
    name: str = pydantic.Field(min_length=1, max_length=128)
    enabled: bool = False
    client_id: str | None = None
    client_secret: str | None = None
    issuer_url: str | None = None
    allowed_domains: list[str] = pydantic.Field(default_factory=list)
    icon: str = 'key'


@oauth_providers_router.get('', response_model=list[OAuthProviderRead])
async def list_oauth_providers(
    db: graph.Pool,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('oauth_providers:read')
        ),
    ],
) -> list[OAuthProviderRead]:
    """List every supported OAuth provider type.

    Always returns one row per known type, synthesizing a blank
    placeholder (``configured=False``) for any type that has not been
    persisted yet so the admin UI can render a uniform card grid and
    surface the redirect URL even before the provider is saved.
    """
    rows = await oauth_providers.list_providers(db)
    by_slug = {r.slug: r for r in rows}
    return [
        OAuthProviderRead.from_model(by_slug[slug])
        if slug in by_slug
        else OAuthProviderRead.blank(slug)
        for slug in _KNOWN_TYPES
    ]


@oauth_providers_router.get('/{slug}', response_model=OAuthProviderRead)
async def get_oauth_provider(
    slug: _ProviderType,
    db: graph.Pool,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('oauth_providers:read')
        ),
    ],
) -> OAuthProviderRead:
    """Fetch a single OAuth provider by slug."""
    row = await oauth_providers.get_provider(db, slug)
    if row is None:
        raise fastapi.HTTPException(
            status_code=404,
            detail=f'OAuth provider {slug!r} not found',
        )
    return OAuthProviderRead.from_model(row)


@oauth_providers_router.put('/{slug}', response_model=OAuthProviderRead)
async def upsert_oauth_provider(
    slug: _ProviderType,
    data: OAuthProviderWrite,
    db: graph.Pool,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('oauth_providers:write')
        ),
    ],
) -> OAuthProviderRead:
    """Create or update an OAuth provider.

    ``client_secret`` is optional in the request body; when omitted
    or blank the existing encrypted secret is preserved.
    """
    existing = await oauth_providers.get_provider(db, slug)

    secret_plaintext: str | None = (
        data.client_secret if data.client_secret else None
    )

    provider = models.OAuthProvider(
        slug=slug,
        type=data.type,
        name=data.name,
        enabled=data.enabled,
        client_id=data.client_id,
        client_secret_encrypted=(
            existing.client_secret_encrypted if existing else None
        ),
        issuer_url=data.issuer_url,
        allowed_domains=list(data.allowed_domains),
        icon=data.icon,
    )

    saved = await oauth_providers.upsert_provider(
        db, provider, secret_plaintext=secret_plaintext
    )
    LOGGER.info(
        'OAuth provider %s upserted by %s',
        slug,
        auth.principal_name,
    )
    return OAuthProviderRead.from_model(saved)


@oauth_providers_router.delete('/{slug}', status_code=204)
async def delete_oauth_provider(
    slug: _ProviderType,
    db: graph.Pool,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('oauth_providers:write')
        ),
    ],
) -> None:
    """Delete an OAuth provider by slug."""
    deleted = await oauth_providers.delete_provider(db, slug)
    if not deleted:
        raise fastapi.HTTPException(
            status_code=404,
            detail=f'OAuth provider {slug!r} not found',
        )
    LOGGER.info('OAuth provider %s deleted by %s', slug, auth.principal_name)
