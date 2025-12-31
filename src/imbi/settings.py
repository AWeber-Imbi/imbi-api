import secrets
from urllib import parse

import pydantic
import pydantic_settings


class Neo4j(pydantic_settings.BaseSettings):
    model_config = pydantic_settings.SettingsConfigDict(
        env_prefix='NEO4J_',
        case_sensitive=False,
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',
    )
    url: pydantic.AnyUrl = pydantic.AnyUrl('neo4j://localhost:7687')
    user: str | None = None
    password: str | None = None
    database: str = 'neo4j'
    keep_alive: bool = True
    liveness_check_timeout: int = 60
    max_connection_lifetime: int = 300

    @pydantic.model_validator(mode='after')
    def extract_credentials_from_url(self) -> 'Neo4j':
        """Extract username/password from URL and strip them from the URL.

        If the URL contains embedded credentials (e.g.,
        neo4j://username:password@localhost:7687), extract them and set
        the user and password fields, then clean the URL.

        """
        if self.url.username and not self.user:
            # Decode URL-encoded username
            self.user = parse.unquote(self.url.username)

        if self.url.password and not self.password:
            # Decode URL-encoded password
            self.password = parse.unquote(self.url.password)

        # Strip credentials from URL if present
        if self.url.username or self.url.password:
            # Rebuild URL without credentials
            scheme = self.url.scheme
            host = self.url.host or 'localhost'
            port = self.url.port or 7687
            path = self.url.path or ''

            # Construct clean URL (no trailing slash if no path)
            if path:
                clean_url = f'{scheme}://{host}:{port}{path}'
            else:
                clean_url = f'{scheme}://{host}:{port}'
            self.url = pydantic.AnyUrl(clean_url)

        return self


class ServerConfig(pydantic_settings.BaseSettings):
    model_config = pydantic_settings.SettingsConfigDict(
        env_prefix='IMBI_',
        case_sensitive=False,
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',
    )
    environment: str = 'development'
    host: str = 'localhost'
    port: int = 8000


class Auth(pydantic_settings.BaseSettings):
    """Authentication and authorization settings."""

    model_config = pydantic_settings.SettingsConfigDict(
        env_prefix='IMBI_AUTH_',
        case_sensitive=False,
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',
    )

    # JWT Configuration
    jwt_secret: str = pydantic.Field(
        default_factory=lambda: secrets.token_urlsafe(32),
        description='JWT signing secret (auto-generated if not provided)',
    )
    jwt_algorithm: str = 'HS256'
    access_token_expire_seconds: int = 3600  # 1 hour
    refresh_token_expire_seconds: int = 2592000  # 30 days

    # Password Policy
    min_password_length: int = 12
    require_password_uppercase: bool = True
    require_password_lowercase: bool = True
    require_password_digit: bool = True
    require_password_special: bool = True

    # Session Configuration
    session_timeout_seconds: int = 86400  # 24 hours
    max_concurrent_sessions: int = 5

    # API Key Configuration
    api_key_max_lifetime_days: int = 365


# Module-level singleton for Auth settings to ensure stable JWT secret
_auth_settings: Auth | None = None


def get_auth_settings() -> Auth:
    """Get the singleton Auth settings instance.

    This ensures the JWT secret remains stable across requests when
    auto-generated (i.e., when IMBI_AUTH_JWT_SECRET is not set in env).

    Returns:
        The singleton Auth settings instance.

    """
    global _auth_settings
    if _auth_settings is None:
        _auth_settings = Auth()
    return _auth_settings
