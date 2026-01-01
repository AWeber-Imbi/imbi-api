import unittest
from unittest import mock

from fastapi import testclient

from imbi import app, settings
from imbi.auth import models as auth_models


class AuthProvidersEndpointTestCase(unittest.TestCase):
    """Test cases for GET /auth/providers endpoint."""

    def setUp(self) -> None:
        """Set up test client and mock settings."""
        # Reset settings singleton
        settings._auth_settings = None
        self.client = testclient.TestClient(app.create_app())

    def tearDown(self) -> None:
        """Reset settings singleton after tests."""
        settings._auth_settings = None

    def test_get_providers_default_config(self) -> None:
        """Test /auth/providers with default config (local auth only)."""
        response = self.client.get('/auth/providers')
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Validate response structure
        self.assertIn('providers', data)
        self.assertIn('default_redirect', data)
        self.assertEqual(data['default_redirect'], '/dashboard')

        # Only local auth should be enabled by default
        self.assertEqual(len(data['providers']), 1)
        local_provider = data['providers'][0]
        self.assertEqual(local_provider['id'], 'local')
        self.assertEqual(local_provider['type'], 'password')
        self.assertEqual(local_provider['name'], 'Email/Password')
        self.assertTrue(local_provider['enabled'])
        self.assertEqual(local_provider['icon'], 'lock')

    @mock.patch.dict('os.environ', {'IMBI_AUTH_OAUTH_GOOGLE_ENABLED': 'true'})
    def test_get_providers_google_enabled(self) -> None:
        """Test /auth/providers with Google OAuth enabled."""
        # Reset settings to pick up env vars
        settings._auth_settings = None

        response = self.client.get('/auth/providers')
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should have local and Google
        self.assertEqual(len(data['providers']), 2)

        # Find Google provider
        google_provider = next(
            (p for p in data['providers'] if p['id'] == 'google'), None
        )
        self.assertIsNotNone(google_provider)
        self.assertEqual(google_provider['type'], 'oauth')
        self.assertEqual(google_provider['name'], 'Google')
        self.assertTrue(google_provider['enabled'])
        self.assertEqual(google_provider['auth_url'], '/auth/oauth/google')
        self.assertEqual(google_provider['icon'], 'google')

    @mock.patch.dict('os.environ', {'IMBI_AUTH_OAUTH_GITHUB_ENABLED': 'true'})
    def test_get_providers_github_enabled(self) -> None:
        """Test /auth/providers with GitHub OAuth enabled."""
        # Reset settings to pick up env vars
        settings._auth_settings = None

        response = self.client.get('/auth/providers')
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should have local and GitHub
        self.assertEqual(len(data['providers']), 2)

        # Find GitHub provider
        github_provider = next(
            (p for p in data['providers'] if p['id'] == 'github'), None
        )
        self.assertIsNotNone(github_provider)
        self.assertEqual(github_provider['type'], 'oauth')
        self.assertEqual(github_provider['name'], 'GitHub')
        self.assertTrue(github_provider['enabled'])
        self.assertEqual(github_provider['auth_url'], '/auth/oauth/github')
        self.assertEqual(github_provider['icon'], 'github')

    @mock.patch.dict(
        'os.environ',
        {
            'IMBI_AUTH_OAUTH_OIDC_ENABLED': 'true',
            'IMBI_AUTH_OAUTH_OIDC_NAME': 'Custom OIDC',
        },
    )
    def test_get_providers_oidc_enabled(self) -> None:
        """Test /auth/providers with OIDC enabled and custom name."""
        # Reset settings to pick up env vars
        settings._auth_settings = None

        response = self.client.get('/auth/providers')
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should have local and OIDC
        self.assertEqual(len(data['providers']), 2)

        # Find OIDC provider
        oidc_provider = next(
            (p for p in data['providers'] if p['id'] == 'oidc'), None
        )
        self.assertIsNotNone(oidc_provider)
        self.assertEqual(oidc_provider['type'], 'oauth')
        self.assertEqual(oidc_provider['name'], 'Custom OIDC')
        self.assertTrue(oidc_provider['enabled'])
        self.assertEqual(oidc_provider['auth_url'], '/auth/oauth/oidc')
        self.assertEqual(oidc_provider['icon'], 'key')

    @mock.patch.dict(
        'os.environ',
        {
            'IMBI_AUTH_OAUTH_GOOGLE_ENABLED': 'true',
            'IMBI_AUTH_OAUTH_GITHUB_ENABLED': 'true',
            'IMBI_AUTH_OAUTH_OIDC_ENABLED': 'true',
        },
    )
    def test_get_providers_all_enabled(self) -> None:
        """Test /auth/providers with all OAuth providers enabled."""
        # Reset settings to pick up env vars
        settings._auth_settings = None

        response = self.client.get('/auth/providers')
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should have all 4 providers (local, Google, GitHub, OIDC)
        self.assertEqual(len(data['providers']), 4)

        provider_ids = {p['id'] for p in data['providers']}
        self.assertEqual(provider_ids, {'local', 'google', 'github', 'oidc'})

    @mock.patch.dict('os.environ', {'IMBI_AUTH_LOCAL_AUTH_ENABLED': 'false'})
    def test_get_providers_local_auth_disabled(self) -> None:
        """Test /auth/providers with local auth disabled."""
        # Reset settings to pick up env vars
        settings._auth_settings = None

        response = self.client.get('/auth/providers')
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should have no providers
        self.assertEqual(len(data['providers']), 0)

    def test_get_providers_response_model(self) -> None:
        """Test /auth/providers returns valid AuthProvidersResponse model."""
        response = self.client.get('/auth/providers')
        self.assertEqual(response.status_code, 200)

        # Validate the response against the model
        providers_response = auth_models.AuthProvidersResponse(
            **response.json()
        )
        self.assertIsInstance(
            providers_response, auth_models.AuthProvidersResponse
        )
        self.assertIsInstance(providers_response.providers, list)
        for provider in providers_response.providers:
            self.assertIsInstance(provider, auth_models.AuthProvider)


class OAuthFlowTestCase(unittest.TestCase):
    """Test cases for OAuth login flow endpoints."""

    def setUp(self) -> None:
        """Set up test client."""
        settings._auth_settings = None
        self.client = testclient.TestClient(app.create_app())

    def tearDown(self) -> None:
        """Reset settings singleton after tests."""
        settings._auth_settings = None

    def test_oauth_login_invalid_provider(self) -> None:
        """Test OAuth login with invalid provider."""
        response = self.client.get('/auth/oauth/invalid')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Invalid provider', response.json()['detail'])

    def test_oauth_login_disabled_provider(self) -> None:
        """Test OAuth login with disabled provider."""
        response = self.client.get('/auth/oauth/google')
        self.assertEqual(response.status_code, 400)
        self.assertIn('not enabled', response.json()['detail'])

    @mock.patch.dict(
        'os.environ',
        {
            'IMBI_AUTH_OAUTH_GOOGLE_ENABLED': 'true',
            'IMBI_AUTH_OAUTH_GOOGLE_CLIENT_ID': 'test-id',
        },
    )
    def test_oauth_login_google_redirect(self) -> None:
        """Test OAuth login redirects to Google."""
        settings._auth_settings = None
        response = self.client.get(
            '/auth/oauth/google', follow_redirects=False
        )
        self.assertEqual(response.status_code, 307)

        # Verify redirect URL contains Google OAuth endpoint
        location = response.headers['location']
        self.assertIn('accounts.google.com/o/oauth2/v2/auth', location)
        self.assertIn('client_id=test-id', location)
        self.assertIn('response_type=code', location)
        self.assertIn('state=', location)

    @mock.patch.dict(
        'os.environ',
        {
            'IMBI_AUTH_OAUTH_GITHUB_ENABLED': 'true',
            'IMBI_AUTH_OAUTH_GITHUB_CLIENT_ID': 'github-id',
        },
    )
    def test_oauth_login_github_redirect(self) -> None:
        """Test OAuth login redirects to GitHub."""
        settings._auth_settings = None
        response = self.client.get(
            '/auth/oauth/github', follow_redirects=False
        )
        self.assertEqual(response.status_code, 307)

        # Verify redirect URL contains GitHub OAuth endpoint
        location = response.headers['location']
        self.assertIn('github.com/login/oauth/authorize', location)
        self.assertIn('client_id=github-id', location)

    def test_oauth_callback_error_handling(self) -> None:
        """Test OAuth callback handles provider errors."""
        url = (
            '/auth/oauth/google/callback'
            '?error=access_denied&error_description=User denied'
        )
        response = self.client.get(url, follow_redirects=False)
        self.assertEqual(response.status_code, 307)

        # Should redirect to error page
        location = response.headers['location']
        self.assertIn('error=access_denied', location)

    def test_oauth_callback_missing_code(self) -> None:
        """Test OAuth callback with missing code parameter."""
        url = '/auth/oauth/google/callback?state=test-state'
        response = self.client.get(url, follow_redirects=False)
        # Should redirect to error page
        self.assertEqual(response.status_code, 307)
        location = response.headers['location']
        self.assertIn('error=authentication_failed', location)

    def test_oauth_callback_missing_state(self) -> None:
        """Test OAuth callback with missing state parameter."""
        url = '/auth/oauth/google/callback?code=test-code'
        response = self.client.get(url, follow_redirects=False)
        # Should redirect to error page
        self.assertEqual(response.status_code, 307)
        location = response.headers['location']
        self.assertIn('error=authentication_failed', location)
