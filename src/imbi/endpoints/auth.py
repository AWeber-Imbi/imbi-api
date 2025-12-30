"""Authentication endpoints for login, token refresh, and logout."""

import datetime
import logging

import fastapi
import jwt

from imbi import models, neo4j, settings
from imbi.auth import core
from imbi.auth import models as auth_models

LOGGER = logging.getLogger(__name__)

auth_router = fastapi.APIRouter(prefix='/auth', tags=['Authentication'])


@auth_router.post('/login', response_model=auth_models.TokenResponse)
async def login(
    credentials: auth_models.LoginRequest,
    request: fastapi.Request,
) -> auth_models.TokenResponse:
    """Login with username and password.

    Args:
        credentials: Username and password
        request: FastAPI request object

    Returns:
        JWT tokens (access and refresh)

    Raises:
        HTTPException: 401 if credentials are invalid

    """
    # Fetch user from database
    user = await neo4j.fetch_node(
        models.User, {'username': credentials.username}
    )

    if not user or not user.is_active:
        LOGGER.warning(
            'Login failed for user %s: user not found or inactive',
            credentials.username,
        )
        raise fastapi.HTTPException(
            status_code=401,
            detail='Invalid credentials',
        )

    # Check if user has password authentication enabled
    if not user.password_hash:
        LOGGER.warning(
            'Login failed for user %s: password authentication not enabled',
            credentials.username,
        )
        raise fastapi.HTTPException(
            status_code=401,
            detail='Password authentication not available for this account',
        )

    # Verify password
    if not core.verify_password(credentials.password, user.password_hash):
        LOGGER.warning(
            'Login failed for user %s: invalid password', credentials.username
        )
        raise fastapi.HTTPException(
            status_code=401,
            detail='Invalid credentials',
        )

    # Check if password needs rehashing
    if core.password_needs_rehash(user.password_hash):
        user.password_hash = core.hash_password(credentials.password)
        await neo4j.upsert(user, {'username': user.username})
        LOGGER.info('Rehashed password for user %s', user.username)

    # Create tokens
    auth_settings = settings.Auth()
    access_token, access_jti = core.create_access_token(
        user.username, auth_settings
    )
    refresh_token, refresh_jti = core.create_refresh_token(
        user.username, auth_settings
    )

    # Store token metadata for access token
    now = datetime.datetime.now(datetime.UTC)
    access_token_meta = models.TokenMetadata(
        jti=access_jti,
        token_type='access',
        issued_at=now,
        expires_at=now
        + datetime.timedelta(
            seconds=auth_settings.access_token_expire_seconds
        ),
        user=user,
    )
    await neo4j.create_node(access_token_meta)
    await neo4j.create_relationship(
        access_token_meta, user, rel_type='ISSUED_TO'
    )

    # Store token metadata for refresh token
    refresh_token_meta = models.TokenMetadata(
        jti=refresh_jti,
        token_type='refresh',
        issued_at=now,
        expires_at=now
        + datetime.timedelta(
            seconds=auth_settings.refresh_token_expire_seconds
        ),
        user=user,
    )
    await neo4j.create_node(refresh_token_meta)
    await neo4j.create_relationship(
        refresh_token_meta, user, rel_type='ISSUED_TO'
    )

    # Update last login timestamp
    user.last_login = now
    await neo4j.upsert(user, {'username': user.username})

    LOGGER.info('User %s logged in successfully', user.username)

    return auth_models.TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=auth_settings.access_token_expire_seconds,
    )


@auth_router.post('/token/refresh', response_model=auth_models.TokenResponse)
async def refresh_token(
    refresh_request: auth_models.TokenRefreshRequest,
    request: fastapi.Request,
) -> auth_models.TokenResponse:
    """Refresh access token using refresh token.

    Args:
        refresh_request: Refresh token
        request: FastAPI request object

    Returns:
        New JWT access token (and same refresh token)

    Raises:
        HTTPException: 401 if refresh token is invalid or revoked

    """
    auth_settings = settings.Auth()

    # Decode and validate refresh token
    try:
        payload = core.decode_token(
            refresh_request.refresh_token, auth_settings
        )
    except jwt.ExpiredSignatureError as err:
        LOGGER.warning('Token refresh failed: token expired')
        raise fastapi.HTTPException(
            status_code=401, detail='Refresh token expired'
        ) from err
    except jwt.InvalidTokenError as err:
        LOGGER.warning('Token refresh failed: invalid token - %s', err)
        raise fastapi.HTTPException(
            status_code=401, detail='Invalid refresh token'
        ) from err

    # Verify token type
    if payload.get('type') != 'refresh':
        LOGGER.warning('Token refresh failed: wrong token type')
        raise fastapi.HTTPException(
            status_code=401, detail='Invalid token type'
        )

    # Check if refresh token is revoked
    token_meta = await neo4j.fetch_node(
        models.TokenMetadata, {'jti': payload['jti']}
    )
    if token_meta and token_meta.revoked:
        LOGGER.warning(
            'Token refresh failed: token revoked (jti=%s)', payload['jti']
        )
        raise fastapi.HTTPException(
            status_code=401, detail='Refresh token has been revoked'
        )

    # Fetch user
    user = await neo4j.fetch_node(models.User, {'username': payload['sub']})
    if not user or not user.is_active:
        LOGGER.warning(
            'Token refresh failed: user not found or inactive (%s)',
            payload['sub'],
        )
        raise fastapi.HTTPException(
            status_code=401, detail='User not found or inactive'
        )

    # Create new access token
    access_token, access_jti = core.create_access_token(
        user.username, auth_settings
    )

    # Store new access token metadata
    now = datetime.datetime.now(datetime.UTC)
    access_token_meta = models.TokenMetadata(
        jti=access_jti,
        token_type='access',
        issued_at=now,
        expires_at=now
        + datetime.timedelta(
            seconds=auth_settings.access_token_expire_seconds
        ),
        user=user,
    )
    await neo4j.create_node(access_token_meta)
    await neo4j.create_relationship(
        access_token_meta, user, rel_type='ISSUED_TO'
    )

    LOGGER.info('Access token refreshed for user %s', user.username)

    return auth_models.TokenResponse(
        access_token=access_token,
        refresh_token=refresh_request.refresh_token,  # Reuse refresh token
        expires_in=auth_settings.access_token_expire_seconds,
    )


@auth_router.post('/logout', status_code=204)
async def logout(
    request: fastapi.Request,
) -> None:
    """Logout and revoke current token.

    Note: In Phase 1, this is a placeholder. Full logout functionality
    requires the AuthContext dependency from Phase 2 (authorization).
    For now, clients can discard their tokens.

    Args:
        request: FastAPI request object

    """
    # TODO: Implement token revocation in Phase 2 with AuthContext
    # For now, this is a no-op. Clients should discard their tokens.
    LOGGER.info('Logout endpoint called (token revocation in Phase 2)')
