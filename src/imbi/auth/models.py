"""Request and response models for authentication endpoints."""

import pydantic


class LoginRequest(pydantic.BaseModel):
    """Login request with username and password."""

    username: str
    password: str


class TokenResponse(pydantic.BaseModel):
    """JWT token response."""

    access_token: str
    refresh_token: str
    token_type: str = 'bearer'
    expires_in: int


class TokenRefreshRequest(pydantic.BaseModel):
    """Request to refresh an access token."""

    refresh_token: str
