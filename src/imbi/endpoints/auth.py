"""Authentication endpoints for login, token refresh, and logout."""

import datetime
import logging
import secrets
import typing

import fastapi
import jwt

from imbi import models, neo4j, settings
from imbi.auth import core, oauth
from imbi.auth import models as auth_models

LOGGER = logging.getLogger(__name__)

auth_router = fastapi.APIRouter(prefix='/auth', tags=['Authentication'])


@auth_router.get(
    '/providers', response_model=auth_models.AuthProvidersResponse
)
async def get_auth_providers() -> auth_models.AuthProvidersResponse:
    """Get available authentication providers configuration.

    Returns a list of enabled authentication providers to allow the UI
    to dynamically configure the login interface.

    Returns:
        AuthProvidersResponse: List of providers with configuration

    """
    auth_settings = settings.get_auth_settings()
    providers = []

    # Local password authentication
    if auth_settings.local_auth_enabled:
        providers.append(
            auth_models.AuthProvider(
                id='local',
                type='password',
                name='Username/Password',
                enabled=True,
                icon='lock',
            )
        )

    # Google OAuth
    if auth_settings.oauth_google_enabled:
        providers.append(
            auth_models.AuthProvider(
                id='google',
                type='oauth',
                name='Google',
                enabled=True,
                auth_url='/auth/oauth/google',
                icon='google',
            )
        )

    # GitHub OAuth
    if auth_settings.oauth_github_enabled:
        providers.append(
            auth_models.AuthProvider(
                id='github',
                type='oauth',
                name='GitHub',
                enabled=True,
                auth_url='/auth/oauth/github',
                icon='github',
            )
        )

    # Generic OIDC
    if auth_settings.oauth_oidc_enabled:
        providers.append(
            auth_models.AuthProvider(
                id='oidc',
                type='oauth',
                name=auth_settings.oauth_oidc_name,
                enabled=True,
                auth_url='/auth/oauth/oidc',
                icon='key',
            )
        )

    return auth_models.AuthProvidersResponse(
        providers=providers,
        default_redirect='/dashboard',
    )


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
    auth_settings = settings.get_auth_settings()
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
    auth_settings = settings.get_auth_settings()

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


@auth_router.get('/oauth/{provider}')
async def oauth_login(
    provider: str,
    request: fastapi.Request,
    redirect_uri: str = fastapi.Query(default='/dashboard'),
) -> fastapi.responses.RedirectResponse:
    """Initiate OAuth login flow.

    Args:
        provider: OAuth provider ('google', 'github', 'oidc')
        request: FastAPI request object
        redirect_uri: Where to redirect after successful auth

    Returns:
        Redirect to OAuth provider's authorization page

    Raises:
        HTTPException: 400 if provider not enabled or invalid

    """
    auth_settings = settings.get_auth_settings()

    # Validate provider is enabled
    if provider not in ['google', 'github', 'oidc']:
        raise fastapi.HTTPException(
            status_code=400, detail=f'Invalid provider: {provider}'
        )

    if provider == 'google' and not auth_settings.oauth_google_enabled:
        raise fastapi.HTTPException(
            status_code=400, detail='Google OAuth not enabled'
        )
    elif provider == 'github' and not auth_settings.oauth_github_enabled:
        raise fastapi.HTTPException(
            status_code=400, detail='GitHub OAuth not enabled'
        )
    elif provider == 'oidc' and not auth_settings.oauth_oidc_enabled:
        raise fastapi.HTTPException(
            status_code=400, detail='OIDC OAuth not enabled'
        )

    # Generate OAuth state for CSRF protection
    state_token, _ = oauth.generate_oauth_state(
        provider, redirect_uri, auth_settings
    )

    # Build callback URL
    base_url = auth_settings.oauth_callback_base_url
    callback_url = f'{base_url}/auth/oauth/{provider}/callback'

    # Build authorization URL based on provider
    if provider == 'google':
        auth_url = (
            'https://accounts.google.com/o/oauth2/v2/auth'
            f'?client_id={auth_settings.oauth_google_client_id}'
            f'&redirect_uri={callback_url}'
            f'&response_type=code'
            f'&scope=openid email profile'
            f'&state={state_token}'
        )
    elif provider == 'github':
        auth_url = (
            'https://github.com/login/oauth/authorize'
            f'?client_id={auth_settings.oauth_github_client_id}'
            f'&redirect_uri={callback_url}'
            f'&scope=read:user user:email'
            f'&state={state_token}'
        )
    elif provider == 'oidc':
        issuer = (auth_settings.oauth_oidc_issuer_url or '').rstrip('/')
        auth_url = (
            f'{issuer}/protocol/openid-connect/auth'
            f'?client_id={auth_settings.oauth_oidc_client_id}'
            f'&redirect_uri={callback_url}'
            f'&response_type=code'
            f'&scope=openid email profile'
            f'&state={state_token}'
        )

    LOGGER.info('OAuth login initiated for provider %s', provider)
    return fastapi.responses.RedirectResponse(url=auth_url)


@auth_router.get('/oauth/{provider}/callback')
async def oauth_callback(
    provider: str,
    code: str | None = fastapi.Query(default=None),
    state: str | None = fastapi.Query(default=None),
    error: str | None = fastapi.Query(default=None),
    error_description: str | None = fastapi.Query(default=None),
) -> fastapi.responses.RedirectResponse:
    """Handle OAuth provider callback.

    After user authorizes on OAuth provider, they're redirected here with
    an authorization code. We exchange it for tokens and create/login user.

    Args:
        provider: OAuth provider
        code: Authorization code from provider
        state: State parameter for CSRF protection
        error: Error code if auth failed
        error_description: Human-readable error description

    Returns:
        Redirect to frontend with token or error

    """
    auth_settings = settings.get_auth_settings()

    # Handle OAuth errors
    if error:
        LOGGER.warning(
            'OAuth callback error: %s - %s', error, error_description
        )
        return fastapi.responses.RedirectResponse(
            url=f'/auth/callback?error={error}'
        )

    try:
        # Validate required parameters
        if not code or not state:
            raise ValueError('Missing required parameters: code and state')

        # Verify state parameter
        state_data = oauth.verify_oauth_state(state, auth_settings)

        if state_data.provider != provider:
            raise ValueError('Provider mismatch')

        # Exchange code for tokens
        base_url = auth_settings.oauth_callback_base_url
        callback_url = f'{base_url}/auth/oauth/{provider}/callback'
        token_response = await oauth.exchange_oauth_code(
            provider, code, callback_url, auth_settings
        )

        # Fetch user profile
        profile = await oauth.fetch_oauth_profile(
            provider, token_response['access_token'], auth_settings
        )

        # Find or create OAuth identity
        oauth_identity = await find_or_create_oauth_identity(
            provider, profile, token_response, auth_settings
        )

        # Get associated user
        await neo4j.refresh_relationship(oauth_identity, 'user')
        user = oauth_identity.user

        # Create JWT tokens (reusing existing token creation logic)
        access_token, access_jti = core.create_access_token(
            user.username, auth_settings
        )
        _, refresh_jti = core.create_refresh_token(
            user.username, auth_settings
        )

        # Store token metadata
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

        # Update user last_login
        user.last_login = now
        await neo4j.upsert(user, {'username': user.username})

        # Update OAuth identity last_used
        oauth_identity.last_used = now
        await neo4j.upsert(
            oauth_identity,
            {'provider': provider, 'provider_user_id': profile['id']},
        )

        LOGGER.info(
            'OAuth login successful for user %s via %s',
            user.username,
            provider,
        )

        # Redirect to frontend with token
        return fastapi.responses.RedirectResponse(
            url=f'{state_data.redirect_uri}?access_token={access_token}'
        )

    except Exception as e:
        LOGGER.exception('OAuth callback failed: %s', e)
        return fastapi.responses.RedirectResponse(
            url='/auth/callback?error=authentication_failed'
        )


async def find_or_create_oauth_identity(
    provider: str,
    profile: dict[str, typing.Any],
    token_response: dict[str, typing.Any],
    auth_settings: settings.Auth,
) -> models.OAuthIdentity:
    """Find existing or create new OAuth identity and user.

    Logic:
    1. Check if OAuth identity exists (by provider + provider_user_id)
    2. If exists, return it (with updated tokens)
    3. If not exists:
       a. Check if auto-link by email is enabled and user exists
       b. Otherwise create new user
       c. Create OAuth identity linked to user

    Args:
        provider: OAuth provider identifier
        profile: Normalized user profile from OAuth provider
        token_response: Token response from OAuth provider
        auth_settings: Auth settings instance

    Returns:
        OAuthIdentity with linked user

    Raises:
        ValueError: If user auto-creation disabled and no user found

    """
    # Try to find existing OAuth identity
    identity = await neo4j.fetch_node(
        models.OAuthIdentity,
        {'provider': provider, 'provider_user_id': profile['id']},
    )

    if identity:
        # Update tokens
        identity.access_token = token_response['access_token']
        identity.refresh_token = token_response.get('refresh_token')
        identity.token_expires_at = datetime.datetime.now(
            datetime.UTC
        ) + datetime.timedelta(seconds=token_response.get('expires_in', 3600))
        await neo4j.upsert(
            identity, {'provider': provider, 'provider_user_id': profile['id']}
        )

        return identity

    # OAuth identity doesn't exist - need to create it

    # Check if we should auto-link to existing user by email
    user = None
    if auth_settings.oauth_auto_link_by_email:
        user = await neo4j.fetch_node(models.User, {'email': profile['email']})

    # Create new user if doesn't exist
    if not user:
        if not auth_settings.oauth_auto_create_users:
            raise ValueError('User auto-creation disabled')

        # Generate username from email
        username = profile['email'].split('@')[0].lower()

        # Ensure username is unique
        existing = await neo4j.fetch_node(models.User, {'username': username})
        if existing:
            username = f'{username}_{secrets.token_hex(4)}'

        user = models.User(
            username=username,
            email=profile['email'],
            display_name=profile['name'],
            password_hash=None,  # OAuth-only user
            is_active=True,
            is_admin=False,
            is_service_account=False,
            created_at=datetime.datetime.now(datetime.UTC),
            avatar_url=profile.get('avatar_url'),
        )

        await neo4j.create_node(user)
        LOGGER.info(
            'Created new user %s via OAuth %s', user.username, provider
        )

    # Create OAuth identity
    now = datetime.datetime.now(datetime.UTC)
    identity = models.OAuthIdentity(
        provider=typing.cast(
            typing.Literal['google', 'github', 'oidc'], provider
        ),
        provider_user_id=profile['id'],
        email=profile['email'],
        display_name=profile['name'],
        avatar_url=profile.get('avatar_url'),
        access_token=token_response['access_token'],
        refresh_token=token_response.get('refresh_token'),
        token_expires_at=now
        + datetime.timedelta(seconds=token_response.get('expires_in', 3600)),
        linked_at=now,
        last_used=now,
        raw_profile=profile,
        user=user,
    )

    await neo4j.create_node(identity)
    await neo4j.create_relationship(identity, user, rel_type='OAUTH_IDENTITY')

    LOGGER.info(
        'Created OAuth identity for user %s via %s', user.username, provider
    )

    return identity
