"""Tests for identity endpoint helpers and route bodies."""

import unittest
from unittest import mock

import fastapi

from imbi_api.identity import endpoints, errors


class IsSafeReturnToTestCase(unittest.TestCase):
    """Verify the open-redirect guard rejects unsafe URLs."""

    def test_none_is_unsafe(self) -> None:
        self.assertFalse(endpoints._is_safe_return_to(None))

    def test_empty_is_unsafe(self) -> None:
        self.assertFalse(endpoints._is_safe_return_to(''))

    def test_absolute_url_is_unsafe(self) -> None:
        self.assertFalse(
            endpoints._is_safe_return_to('https://attacker.example/x')
        )

    def test_protocol_relative_is_unsafe(self) -> None:
        self.assertFalse(endpoints._is_safe_return_to('//attacker.example/x'))

    def test_path_without_leading_slash_is_unsafe(self) -> None:
        self.assertFalse(endpoints._is_safe_return_to('projects/x'))

    def test_in_app_path_is_safe(self) -> None:
        self.assertTrue(endpoints._is_safe_return_to('/projects/x'))


class BuildRedirectUriTestCase(unittest.TestCase):
    """Verify _build_redirect_uri prefers configured base URL."""

    def test_uses_configured_base_url(self) -> None:
        request = mock.MagicMock()
        with mock.patch.object(
            endpoints.settings,
            'get_server_config',
            return_value=mock.MagicMock(public_base_url='https://imbi.test/'),
        ):
            result = endpoints._build_redirect_uri(request, 'plugin-1')
        self.assertEqual(
            result,
            'https://imbi.test/me/identities/plugin-1/callback',
        )

    def test_falls_back_to_request_when_base_unset(self) -> None:
        request = mock.MagicMock()
        request.url.scheme = 'https'
        request.url.netloc = 'imbi.test'
        with mock.patch.object(
            endpoints.settings,
            'get_server_config',
            return_value=mock.MagicMock(public_base_url=''),
        ):
            result = endpoints._build_redirect_uri(request, 'plugin-1')
        self.assertEqual(
            result,
            'https://imbi.test/me/identities/plugin-1/callback',
        )

    def test_falls_back_to_request_when_config_raises(self) -> None:
        request = mock.MagicMock()
        request.url.scheme = 'http'
        request.url.netloc = 'localhost:8000'
        with mock.patch.object(
            endpoints.settings,
            'get_server_config',
            side_effect=RuntimeError('settings unavailable'),
        ):
            result = endpoints._build_redirect_uri(request, 'p')
        self.assertEqual(
            result, 'http://localhost:8000/me/identities/p/callback'
        )


class RefreshEndpointTestCase(unittest.IsolatedAsyncioTestCase):
    """Cover the refresh endpoint's exception mapping."""

    def setUp(self) -> None:
        self.db = mock.AsyncMock()
        self.auth = mock.MagicMock()
        self.auth.require_user = mock.MagicMock(id='user-1')

    async def test_returns_refreshed_on_success(self) -> None:
        with mock.patch.object(
            endpoints.flows,
            'refresh_connection',
            new=mock.AsyncMock(),
        ):
            result = await endpoints.refresh('p', self.db, self.auth)
        self.assertEqual(result, {'status': 'refreshed'})

    async def test_maps_identity_required_to_401(self) -> None:
        with mock.patch.object(
            endpoints.flows,
            'refresh_connection',
            new=mock.AsyncMock(
                side_effect=errors.IdentityRequiredError(
                    plugin_id='p', start_url='/me/identities/p/start'
                )
            ),
        ):
            with self.assertRaises(fastapi.HTTPException) as ctx:
                await endpoints.refresh('p', self.db, self.auth)
        self.assertEqual(ctx.exception.status_code, 401)

    async def test_maps_refresh_failed_to_502(self) -> None:
        with mock.patch.object(
            endpoints.flows,
            'refresh_connection',
            new=mock.AsyncMock(
                side_effect=errors.IdentityRefreshFailed('idp boom')
            ),
        ):
            with self.assertRaises(fastapi.HTTPException) as ctx:
                await endpoints.refresh('p', self.db, self.auth)
        self.assertEqual(ctx.exception.status_code, 502)


class DisconnectEndpointTestCase(unittest.IsolatedAsyncioTestCase):
    """Cover the disconnect endpoint's success path."""

    async def test_returns_204_after_revoke(self) -> None:
        db = mock.AsyncMock()
        auth = mock.MagicMock()
        auth.require_user = mock.MagicMock(id='user-1')
        with mock.patch.object(
            endpoints.flows,
            'revoke_connection',
            new=mock.AsyncMock(),
        ) as revoke:
            response = await endpoints.disconnect('p', db, auth)
        self.assertEqual(response.status_code, 204)
        revoke.assert_awaited_once()


class ListMyIdentitiesTestCase(unittest.IsolatedAsyncioTestCase):
    """Cover the list endpoint's row-mapping branch."""

    async def test_returns_response_models(self) -> None:
        db = mock.AsyncMock()
        auth = mock.MagicMock()
        auth.require_user = mock.MagicMock(id='user-1')
        rows = [
            {
                'id': 'conn-1',
                'plugin_id': 'plugin-1',
                'plugin_slug': 'oidc',
                'plugin_label': 'OIDC',
                'subject': 'sub-1',
                'status': 'active',
                'expires_at': None,
                'scopes': ['openid'],
                'last_used_at': None,
                'connects_users_to': None,
                'metadata': {},
            }
        ]
        with mock.patch.object(
            endpoints.repository,
            'list_for_user',
            new=mock.AsyncMock(return_value=rows),
        ):
            result = await endpoints.list_my_identities(db, auth)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, 'conn-1')
        self.assertEqual(result[0].plugin_slug, 'oidc')
