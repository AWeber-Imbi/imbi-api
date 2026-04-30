"""Tests for OAuth provider admin endpoints and ``/auth/providers``."""

from __future__ import annotations

import datetime
import typing
import unittest
from unittest import mock

from fastapi import testclient
from imbi_common import graph

from imbi_api import app, models
from imbi_api.auth import providers as oauth_providers
from imbi_api.domain import models as domain_models


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
    permissions_set: set[str],
    *,
    is_admin: bool = False,
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


class _Rows:
    """Configurable provider store used by the mock DB."""

    def __init__(self) -> None:
        self.by_slug: dict[str, domain_models.OAuthProvider] = {}

    async def match(
        self,
        model: typing.Any,
        criteria: dict[str, typing.Any] | None = None,
        order_by: str | None = None,
    ) -> list[domain_models.OAuthProvider]:
        rows = list(self.by_slug.values())
        if criteria:
            for key, value in criteria.items():
                rows = [r for r in rows if getattr(r, key) == value]
        if order_by:
            rows.sort(key=lambda r: getattr(r, order_by))
        return rows

    async def merge(
        self,
        node: domain_models.OAuthProvider,
        match_on: list[str] | None = None,
    ) -> None:
        self.by_slug[node.slug] = node

    async def execute(
        self,
        query: typing.Any,
        params: dict[str, typing.Any],
        columns: list[str] | None = None,
    ) -> list[dict[str, typing.Any]]:
        slug = params.get('slug')
        existed = slug in self.by_slug
        if existed:
            del self.by_slug[slug]
        return [{'deleted': 1 if existed else 0}]


def _wire_db(db: mock.AsyncMock, rows: _Rows) -> None:
    db.match.side_effect = rows.match
    db.merge.side_effect = rows.merge
    db.execute.side_effect = rows.execute


class AuthProvidersEndpointTestCase(unittest.TestCase):
    """``GET /auth/providers`` returns the DB-driven list."""

    def setUp(self) -> None:
        oauth_providers._provider_cache.clear()
        oauth_providers._list_cache.clear()
        self.test_app, self.db = _build_app(set())
        self.rows = _Rows()
        _wire_db(self.db, self.rows)
        self.client = testclient.TestClient(self.test_app)

    def test_returns_local_when_no_providers(self) -> None:
        response = self.client.get('/auth/providers')
        self.assertEqual(response.status_code, 200)
        ids = [p['id'] for p in response.json()['providers']]
        self.assertIn('local', ids)

    def test_returns_enabled_providers(self) -> None:
        self.rows.by_slug['google'] = domain_models.OAuthProvider(
            slug='google',
            type='google',
            name='Google',
            enabled=True,
            icon='google',
            client_id='cid',
        )
        self.rows.by_slug['github'] = domain_models.OAuthProvider(
            slug='github',
            type='github',
            name='GitHub',
            enabled=False,
        )
        response = self.client.get('/auth/providers')
        self.assertEqual(response.status_code, 200)
        ids = [p['id'] for p in response.json()['providers']]
        self.assertIn('google', ids)
        self.assertNotIn('github', ids)
        google = next(
            p for p in response.json()['providers'] if p['id'] == 'google'
        )
        self.assertEqual(google['auth_url'], '/auth/oauth/google')


class AdminOAuthProvidersEndpointTestCase(unittest.TestCase):
    """``/admin/oauth-providers`` CRUD + permission gating."""

    def setUp(self) -> None:
        oauth_providers._provider_cache.clear()
        oauth_providers._list_cache.clear()
        self.test_app, self.db = _build_app(
            {'oauth_providers:read', 'oauth_providers:write'}
        )
        self.rows = _Rows()
        _wire_db(self.db, self.rows)
        self.client = testclient.TestClient(self.test_app)

    def test_list_requires_permission(self) -> None:
        # New app + client with no permissions
        no_perm_app, no_perm_db = _build_app(set())
        rows = _Rows()
        _wire_db(no_perm_db, rows)
        client = testclient.TestClient(no_perm_app)
        response = client.get('/admin/oauth-providers')
        self.assertEqual(response.status_code, 403)

    def test_list_empty(self) -> None:
        response = self.client.get('/admin/oauth-providers')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_put_creates_provider_and_redacts_secret(self) -> None:
        with _patch_encryptor():
            response = self.client.put(
                '/admin/oauth-providers/google',
                json={
                    'type': 'google',
                    'name': 'Google',
                    'enabled': True,
                    'client_id': 'cid',
                    'client_secret': 'shh',
                    'allowed_domains': ['example.com'],
                    'icon': 'google',
                },
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertNotIn('client_secret', body)
        self.assertNotIn('client_secret_encrypted', body)
        self.assertTrue(body['has_secret'])
        # Persisted row was encrypted
        self.assertEqual(
            self.rows.by_slug['google'].client_secret_encrypted,
            'enc:shh',
        )

    def test_put_blank_secret_preserves_existing(self) -> None:
        self.rows.by_slug['google'] = domain_models.OAuthProvider(
            slug='google',
            type='google',
            name='Google',
            enabled=True,
            client_secret_encrypted='enc:keep',
        )
        with _patch_encryptor():
            response = self.client.put(
                '/admin/oauth-providers/google',
                json={
                    'type': 'google',
                    'name': 'Google Renamed',
                    'enabled': True,
                    'client_id': 'cid',
                    'client_secret': '',
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.rows.by_slug['google'].client_secret_encrypted,
            'enc:keep',
        )

    def test_get_redacts_secret(self) -> None:
        self.rows.by_slug['google'] = domain_models.OAuthProvider(
            slug='google',
            type='google',
            name='Google',
            enabled=True,
            client_secret_encrypted='enc:hidden',
        )
        response = self.client.get('/admin/oauth-providers/google')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertNotIn('client_secret', body)
        self.assertNotIn('client_secret_encrypted', body)
        self.assertTrue(body['has_secret'])

    def test_get_missing_returns_404(self) -> None:
        response = self.client.get('/admin/oauth-providers/google')
        self.assertEqual(response.status_code, 404)

    def test_delete_provider(self) -> None:
        self.rows.by_slug['google'] = domain_models.OAuthProvider(
            slug='google',
            type='google',
            name='Google',
            enabled=True,
        )
        response = self.client.delete('/admin/oauth-providers/google')
        self.assertEqual(response.status_code, 204)
        self.assertNotIn('google', self.rows.by_slug)

    def test_delete_missing_returns_404(self) -> None:
        response = self.client.delete('/admin/oauth-providers/google')
        self.assertEqual(response.status_code, 404)

    def test_write_requires_write_permission(self) -> None:
        read_only_app, read_only_db = _build_app({'oauth_providers:read'})
        rows = _Rows()
        _wire_db(read_only_db, rows)
        client = testclient.TestClient(read_only_app)
        response = client.put(
            '/admin/oauth-providers/google',
            json={
                'type': 'google',
                'name': 'Google',
                'enabled': True,
            },
        )
        self.assertEqual(response.status_code, 403)
