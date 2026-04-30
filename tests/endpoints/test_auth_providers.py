"""Tests for the /admin/auth-providers admin endpoints."""

from __future__ import annotations

import datetime
import json
import typing
import unittest
from unittest import mock

from fastapi import testclient
from imbi_common import graph

from imbi_api import app, models
from imbi_api.auth import login_providers


class _FakeEncryptor:
    def encrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        return f'enc:{value}'

    def decrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        return value.removeprefix('enc:')


def _patch_encryptor() -> typing.Any:
    return mock.patch(
        'imbi_common.auth.encryption.TokenEncryption.get_instance',
        return_value=_FakeEncryptor(),
    )


def _build_app(
    permissions_set: set[str], *, is_admin: bool = False
) -> tuple[typing.Any, mock.AsyncMock]:
    from imbi_api.auth import permissions

    test_app = app.create_app()
    user = models.User(
        email='admin@example.com',
        display_name='Admin',
        password_hash='$argon2id$hash',
        is_active=True,
        is_admin=is_admin,
        is_service_account=False,
        created_at=datetime.datetime.now(datetime.UTC),
    )
    auth_context = permissions.AuthContext(
        user=user,
        session_id='test',
        auth_method='jwt',
        permissions=permissions_set,
    )

    async def _current_user() -> permissions.AuthContext:
        return auth_context

    test_app.dependency_overrides[permissions.get_current_user] = _current_user

    db = mock.AsyncMock(spec=graph.Graph)
    test_app.dependency_overrides[graph._inject_graph] = lambda: db
    return test_app, db


def _row(
    slug: str = 'google',
    *,
    usage: str = 'login',
    oauth_app_type: str = 'google',
) -> dict[str, typing.Any]:
    return {
        'app': {
            'slug': slug,
            'name': 'Google',
            'usage': usage,
            'oauth_app_type': oauth_app_type,
            'client_id': 'cid',
            'client_secret': 'enc:keep',
            'issuer_url': None,
            'allowed_domains': json.dumps([]),
            'scopes': json.dumps([]),
            'status': 'active',
            'description': None,
        },
        'service': {
            'slug': 'svc',
            'name': 'SVC',
            'authorization_endpoint': 'https://auth/authorize',
            'token_endpoint': 'https://auth/token',
            'revoke_endpoint': None,
        },
        'organization': {'slug': 'eng', 'name': 'Engineering'},
    }


class AuthProvidersEndpointTestCase(unittest.TestCase):
    def setUp(self) -> None:
        login_providers.invalidate_cache()
        self.test_app, self.db = _build_app(
            {'auth_providers:read', 'auth_providers:write'}
        )
        self.client = testclient.TestClient(self.test_app)

    def test_list_requires_permission(self) -> None:
        no_perm_app, _ = _build_app(set())
        client = testclient.TestClient(no_perm_app)
        response = client.get('/admin/auth-providers')
        self.assertEqual(response.status_code, 403)

    def test_list_returns_rows(self) -> None:
        self.db.execute.return_value = [
            _row('google'),
            _row('github', oauth_app_type='github'),
        ]
        with mock.patch(
            'imbi_common.graph.parse_agtype', side_effect=lambda x: x
        ):
            response = self.client.get('/admin/auth-providers')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual({r['slug'] for r in body}, {'google', 'github'})

    def test_get_missing_returns_404(self) -> None:
        self.db.execute.return_value = []
        with mock.patch(
            'imbi_common.graph.parse_agtype', side_effect=lambda x: x
        ):
            response = self.client.get('/admin/auth-providers/nope')
        self.assertEqual(response.status_code, 404)

    def test_get_integration_only_returns_404(self) -> None:
        self.db.execute.return_value = [_row('integ', usage='integration')]
        with mock.patch(
            'imbi_common.graph.parse_agtype', side_effect=lambda x: x
        ):
            response = self.client.get('/admin/auth-providers/integ')
        self.assertEqual(response.status_code, 404)

    def test_delete_refuses_both(self) -> None:
        self.db.execute.return_value = [_row('google', usage='both')]
        with mock.patch(
            'imbi_common.graph.parse_agtype', side_effect=lambda x: x
        ):
            response = self.client.delete('/admin/auth-providers/google')
        self.assertEqual(response.status_code, 409)

    def test_delete_login_succeeds(self) -> None:
        self.db.execute.side_effect = [
            [_row('google', usage='login')],
            [],  # delete query
        ]
        with mock.patch(
            'imbi_common.graph.parse_agtype', side_effect=lambda x: x
        ):
            response = self.client.delete('/admin/auth-providers/google')
        self.assertEqual(response.status_code, 204)

    def test_create_requires_write(self) -> None:
        ro_app, _ = _build_app({'auth_providers:read'})
        client = testclient.TestClient(ro_app)
        response = client.post(
            '/admin/auth-providers',
            json={
                'org_slug': 'eng',
                'third_party_service_slug': 'svc',
                'slug': 'google',
                'name': 'Google',
                'oauth_app_type': 'google',
                'client_id': 'cid',
                'client_secret': 'shh',
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_blank_secret_preserves_existing(self) -> None:
        # PUT with blank client_secret should keep the encrypted value.
        existing = _row('google', usage='login')
        self.db.execute.side_effect = [
            [existing],  # initial fetch
            [],  # update SET
            [existing],  # re-fetch
        ]
        with (
            _patch_encryptor(),
            mock.patch(
                'imbi_common.graph.parse_agtype', side_effect=lambda x: x
            ),
        ):
            response = self.client.put(
                '/admin/auth-providers/google',
                json={
                    'name': 'Google Renamed',
                    'oauth_app_type': 'google',
                    'client_id': 'cid',
                    'client_secret': '',
                    'usage': 'login',
                },
            )
        self.assertEqual(response.status_code, 200)
        # The middle execute call should NOT include a client_secret param,
        # confirming we didn't overwrite it with the empty string.
        update_call_args = self.db.execute.call_args_list[1]
        params = update_call_args.args[1]
        self.assertNotIn('client_secret', params)
