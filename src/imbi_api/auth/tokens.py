"""Token issuance helpers.

Centralizes the access+refresh token minting flow used by the
login, refresh, OAuth callback, and client_credentials endpoints.
The JWT claims are recovered via a non-verifying decode because the
tokens were just generated locally and the signature is trusted.
"""

import datetime
import logging
import typing

import jwt
from imbi_common import graph
from imbi_common.auth import core

from imbi_api import settings

LOGGER = logging.getLogger(__name__)

PrincipalType = typing.Literal['user', 'service_account']


class PrincipalNotFoundError(Exception):
    """Raised when no principal node matches the given id."""


_USER_CHECK: typing.LiteralString = (
    'MATCH (p:User {{email: {principal_id}}}) RETURN p LIMIT 1'
)
_SERVICE_ACCOUNT_CHECK: typing.LiteralString = (
    'MATCH (p:ServiceAccount {{slug: {principal_id}}}) RETURN p LIMIT 1'
)
_USER_CREATE: typing.LiteralString = (
    'MATCH (p:User {{email: {principal_id}}}) '
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
_SERVICE_ACCOUNT_CREATE: typing.LiteralString = (
    'MATCH (p:ServiceAccount {{slug: {principal_id}}}) '
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

_PRINCIPAL_QUERIES: dict[
    PrincipalType, tuple[typing.LiteralString, typing.LiteralString]
] = {
    'user': (_USER_CHECK, _USER_CREATE),
    'service_account': (_SERVICE_ACCOUNT_CHECK, _SERVICE_ACCOUNT_CREATE),
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

    Raises:
        PrincipalNotFoundError: No principal matched ``principal_id``.
            Raised before any JWT is signed so tokens are never issued
            without a corresponding ``TokenMetadata``/``ISSUED_TO`` row.

    """
    check_query, create_query = _PRINCIPAL_QUERIES[principal_type]

    existing = await db.execute(
        check_query,
        {'principal_id': principal_id},
        columns=['p'],
    )
    if not existing:
        LOGGER.warning(
            'issue_token_pair: no %s matching %r',
            principal_type,
            principal_id,
        )
        raise PrincipalNotFoundError(
            f'No {principal_type} found for {principal_id!r}'
        )

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

    await db.execute(
        create_query,
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
