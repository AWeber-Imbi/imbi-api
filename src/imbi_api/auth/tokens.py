"""Token issuance helpers.

Centralizes the access+refresh token minting flow used by the
login, refresh, OAuth callback, and client_credentials endpoints.
The JWT claims are recovered via a non-verifying decode because the
tokens were just generated locally and the signature is trusted.
"""

import datetime
import typing

import jwt
from imbi_common import graph
from imbi_common.auth import core

from imbi_api import settings

PrincipalType = typing.Literal['user', 'service_account']

_PRINCIPAL_CYPHER: dict[PrincipalType, tuple[str, str]] = {
    'user': ('User', 'email'),
    'service_account': ('ServiceAccount', 'slug'),
}


def _decode_claims(token: str) -> dict[str, typing.Any]:
    """Decode JWT claims without signature verification.

    Safe because the token was just produced by this process; the
    signature is already trusted. Avoids a second HMAC round trip.
    """
    return jwt.decode(token, options={'verify_signature': False})


async def issue_token_pair(
    db: graph.Graph,
    principal_type: PrincipalType,
    principal_id: str,
    auth_settings: settings.Auth,
    extra_claims: dict[str, typing.Any] | None = None,
) -> tuple[str, str, dict[str, typing.Any]]:
    """Mint an access+refresh pair and persist TokenMetadata nodes.

    Args:
        db: Graph database connection.
        principal_type: ``'user'`` or ``'service_account'``.
        principal_id: Email for users, slug for service accounts.
        auth_settings: Auth settings for JWT configuration.
        extra_claims: Optional additional JWT claims.

    Returns:
        ``(access_token, refresh_token, meta)`` where ``meta``
        contains ``access_jti``, ``refresh_jti``, ``issued_at``,
        ``access_expires_at``, and ``refresh_expires_at``.

    """
    access_token = core.create_access_token(
        principal_id,
        extra_claims=extra_claims,
        auth_settings=auth_settings,
    )
    refresh_token = core.create_refresh_token(
        principal_id,
        extra_claims=extra_claims,
        auth_settings=auth_settings,
    )

    access_claims = _decode_claims(access_token)
    refresh_claims = _decode_claims(refresh_token)

    now = datetime.datetime.now(datetime.UTC)
    access_expires_at = now + datetime.timedelta(
        seconds=auth_settings.access_token_expire_seconds
    )
    refresh_expires_at = now + datetime.timedelta(
        seconds=auth_settings.refresh_token_expire_seconds
    )

    label, match_prop = _PRINCIPAL_CYPHER[principal_type]
    query = (
        f'MATCH (p:{label} {{{{{match_prop}: {{principal_id}}}}}}) '
        'CREATE (at:TokenMetadata {{'
        'jti: {access_jti}, '
        "token_type: 'access', "
        'issued_at: {issued_at}, '
        'expires_at: {access_exp}, '
        'revoked: false'
        '}})-[:ISSUED_TO]->(p) '
        'CREATE (rt:TokenMetadata {{'
        'jti: {refresh_jti}, '
        "token_type: 'refresh', "
        'issued_at: {issued_at}, '
        'expires_at: {refresh_exp}, '
        'revoked: false'
        '}})-[:ISSUED_TO]->(p)'
    )
    await db.execute(
        query,
        {
            'principal_id': principal_id,
            'access_jti': access_claims['jti'],
            'refresh_jti': refresh_claims['jti'],
            'issued_at': now.isoformat(),
            'access_exp': access_expires_at.isoformat(),
            'refresh_exp': refresh_expires_at.isoformat(),
        },
    )

    return (
        access_token,
        refresh_token,
        {
            'access_jti': access_claims['jti'],
            'refresh_jti': refresh_claims['jti'],
            'issued_at': now,
            'access_expires_at': access_expires_at,
            'refresh_expires_at': refresh_expires_at,
        },
    )
