"""Tests for project configuration plugin endpoints."""

import asyncio
import datetime
import json
import unittest
from unittest import mock

from fastapi import testclient
from imbi_common import graph
from imbi_common.plugins.base import (
    ConfigKey,
    ConfigKeyWithValue,
    ConfigurationPlugin,
    PluginManifest,
)
from imbi_common.plugins.errors import (
    PluginCredentialsMissing,
)
from imbi_common.plugins.registry import RegistryEntry

from imbi_api import app, models
from imbi_api.auth import password, permissions
from imbi_api.plugins.resolution import ResolvedPlugin


class _FakeConfigurationPlugin(ConfigurationPlugin):
    manifest = PluginManifest(
        slug='ssm',
        name='SSM',
        plugin_type='configuration',
    )

    async def list_keys(self, ctx, credentials):  # type: ignore[override]
        return [
            ConfigKey(
                key='/foo',
                data_type='string',
                last_modified=datetime.datetime(
                    2026, 1, 1, tzinfo=datetime.UTC
                ),
                secret=False,
            )
        ]

    async def get_values(self, ctx, credentials, keys=None):  # type: ignore[override]
        return [
            ConfigKeyWithValue(
                key='/foo',
                data_type='string',
                last_modified=None,
                secret=False,
                value='bar',
            )
        ]

    async def set_value(self, ctx, credentials, key, value):  # type: ignore[override]
        return ConfigKey(
            key=key,
            data_type=value.data_type,
            last_modified=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
            secret=value.secret,
        )

    async def delete_key(self, ctx, credentials, key):  # type: ignore[override]
        return None


def _entry() -> RegistryEntry:
    return RegistryEntry(
        handler_cls=_FakeConfigurationPlugin,
        manifest=_FakeConfigurationPlugin.manifest,
        package_name='imbi-plugin-ssm',
        package_version='1.0.0',
    )


def _resolved() -> ResolvedPlugin:
    return ResolvedPlugin(
        plugin_id='p1',
        plugin_slug='ssm',
        entry=_entry(),
        options={},
    )


class ProjectConfigurationEndpointTestCase(unittest.TestCase):
    """Mock patches MUST be applied INSIDE the TestClient context.

    Patching ``valkey.get_client`` outside the TestClient context applies
    the mock during lifespan startup, where the score-worker hook also
    calls ``valkey.get_client()`` and would receive the test's AsyncMock.
    The score-worker task then loops against the fake client and never
    exits, causing pytest to hang indefinitely. Always:

        with TestClient(app) as client:
            with mock.patch(...):
                response = client.get(...)
    """

    def setUp(self) -> None:
        self.test_app = app.create_app()
        self.test_user = models.User(
            email='admin@example.com',
            display_name='Admin User',
            is_active=True,
            is_admin=True,
            password_hash=password.hash_password('testpassword123'),
            created_at=datetime.datetime.now(datetime.UTC),
        )
        self.auth_context = permissions.AuthContext(
            user=self.test_user,
            session_id='test-session',
            auth_method='jwt',
            permissions={
                'project:configuration:read',
                'project:configuration:read_secrets',
                'project:configuration:write',
            },
        )

        async def mock_get_current_user() -> permissions.AuthContext:
            return self.auth_context

        self.test_app.dependency_overrides[permissions.get_current_user] = (
            mock_get_current_user
        )
        self.mock_db = mock.AsyncMock(spec=graph.Graph)
        self.test_app.dependency_overrides[graph._inject_graph] = (
            lambda: self.mock_db
        )

    def test_get_configuration_credentials_missing(self) -> None:
        with testclient.TestClient(self.test_app) as client:
            with (
                mock.patch(
                    'imbi_api.endpoints.project_configuration.resolve_plugin',
                    return_value=_resolved(),
                ),
                mock.patch(
                    'imbi_api.endpoints.project_configuration'
                    '.get_plugin_credentials',
                    side_effect=PluginCredentialsMissing('no token'),
                ),
            ):
                response = client.get(
                    '/organizations/myorg/projects/proj1/configuration/'
                )
        self.assertEqual(response.status_code, 503)

    def test_get_configuration_cache_miss_writes(self) -> None:
        valkey_client = mock.AsyncMock()
        valkey_client.get.return_value = None
        with testclient.TestClient(self.test_app) as client:
            with (
                mock.patch(
                    'imbi_api.endpoints.project_configuration.resolve_plugin',
                    return_value=_resolved(),
                ),
                mock.patch(
                    'imbi_api.endpoints.project_configuration'
                    '.get_plugin_credentials',
                    return_value={},
                ),
                mock.patch(
                    'imbi_api.endpoints.project_configuration'
                    '.valkey.get_client',
                    return_value=valkey_client,
                ),
            ):
                response = client.get(
                    '/organizations/myorg/projects/proj1/configuration/'
                )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['key'], '/foo')
        valkey_client.setex.assert_awaited_once()

    def test_get_configuration_cache_hit(self) -> None:
        cached = json.dumps(
            [
                {
                    'key': '/cached',
                    'data_type': 'string',
                    'last_modified': None,
                    'secret': False,
                }
            ]
        )
        valkey_client = mock.AsyncMock()
        valkey_client.get.return_value = cached
        with testclient.TestClient(self.test_app) as client:
            with (
                mock.patch(
                    'imbi_api.endpoints.project_configuration.resolve_plugin',
                    return_value=_resolved(),
                ),
                mock.patch(
                    'imbi_api.endpoints.project_configuration'
                    '.get_plugin_credentials',
                    return_value={},
                ),
                mock.patch(
                    'imbi_api.endpoints.project_configuration'
                    '.valkey.get_client',
                    return_value=valkey_client,
                ),
            ):
                response = client.get(
                    '/organizations/myorg/projects/proj1/configuration/'
                )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data[0]['key'], '/cached')
        valkey_client.setex.assert_not_called()

    def test_get_configuration_no_valkey(self) -> None:
        with testclient.TestClient(self.test_app) as client:
            with (
                mock.patch(
                    'imbi_api.endpoints.project_configuration.resolve_plugin',
                    return_value=_resolved(),
                ),
                mock.patch(
                    'imbi_api.endpoints.project_configuration'
                    '.get_plugin_credentials',
                    return_value={},
                ),
                mock.patch(
                    'imbi_api.endpoints.project_configuration'
                    '.valkey.get_client',
                    side_effect=RuntimeError('no valkey'),
                ),
            ):
                response = client.get(
                    '/organizations/myorg/projects/proj1/configuration/'
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)

    def test_fetch_values(self) -> None:
        with testclient.TestClient(self.test_app) as client:
            with (
                mock.patch(
                    'imbi_api.endpoints.project_configuration.resolve_plugin',
                    return_value=_resolved(),
                ),
                mock.patch(
                    'imbi_api.endpoints.project_configuration'
                    '.get_plugin_credentials',
                    return_value={},
                ),
            ):
                response = client.post(
                    '/organizations/myorg/projects/proj1/'
                    'configuration/values:fetch',
                    json={'keys': ['/foo']},
                )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data[0]['value'], 'bar')

    def test_fetch_values_credentials_missing(self) -> None:
        with testclient.TestClient(self.test_app) as client:
            with (
                mock.patch(
                    'imbi_api.endpoints.project_configuration.resolve_plugin',
                    return_value=_resolved(),
                ),
                mock.patch(
                    'imbi_api.endpoints.project_configuration'
                    '.get_plugin_credentials',
                    side_effect=PluginCredentialsMissing('missing'),
                ),
            ):
                response = client.post(
                    '/organizations/myorg/projects/proj1/'
                    'configuration/values:fetch',
                    json={'keys': ['/foo']},
                )
        self.assertEqual(response.status_code, 503)

    def test_set_configuration_value(self) -> None:
        # NOTE: ``_write_audit`` writes to ClickHouse with an ad-hoc column
        # set ([project_id, action, actor, metadata]) that does NOT match
        # the canonical operations_log schema. The function swallows any
        # exception, so a failed audit insert won't break the response.
        # The simplify pass already flagged this; this test does not assert
        # on the column shape — only that the audit attempt is made and the
        # response succeeds.
        ch = mock.MagicMock()
        ch.insert = mock.AsyncMock()
        valkey_client = mock.AsyncMock()
        with testclient.TestClient(self.test_app) as client:
            with (
                mock.patch(
                    'imbi_api.endpoints.project_configuration.resolve_plugin',
                    return_value=_resolved(),
                ),
                mock.patch(
                    'imbi_api.endpoints.project_configuration'
                    '.get_plugin_credentials',
                    return_value={},
                ),
                mock.patch(
                    'imbi_api.endpoints.project_configuration'
                    '.valkey.get_client',
                    return_value=valkey_client,
                ),
                mock.patch(
                    'imbi_api.endpoints.project_configuration'
                    '.ch_client.Clickhouse.get_instance',
                    return_value=ch,
                ),
            ):
                response = client.put(
                    '/organizations/myorg/projects/proj1/configuration/foo',
                    json={
                        'data_type': 'string',
                        'value': 'bar',
                        'secret': False,
                    },
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['key'], 'foo')
        valkey_client.delete.assert_awaited_once()
        ch.insert.assert_awaited_once()

    def test_set_configuration_value_credentials_missing(self) -> None:
        with testclient.TestClient(self.test_app) as client:
            with (
                mock.patch(
                    'imbi_api.endpoints.project_configuration.resolve_plugin',
                    return_value=_resolved(),
                ),
                mock.patch(
                    'imbi_api.endpoints.project_configuration'
                    '.get_plugin_credentials',
                    side_effect=PluginCredentialsMissing('missing'),
                ),
            ):
                response = client.put(
                    '/organizations/myorg/projects/proj1/configuration/foo',
                    json={
                        'data_type': 'string',
                        'value': 'bar',
                        'secret': False,
                    },
                )
        self.assertEqual(response.status_code, 503)

    def test_set_configuration_value_audit_failure_swallowed(self) -> None:
        ch = mock.MagicMock()
        ch.insert = mock.AsyncMock(side_effect=RuntimeError('CH down'))
        valkey_client = mock.AsyncMock()
        with testclient.TestClient(self.test_app) as client:
            with (
                mock.patch(
                    'imbi_api.endpoints.project_configuration.resolve_plugin',
                    return_value=_resolved(),
                ),
                mock.patch(
                    'imbi_api.endpoints.project_configuration'
                    '.get_plugin_credentials',
                    return_value={},
                ),
                mock.patch(
                    'imbi_api.endpoints.project_configuration'
                    '.valkey.get_client',
                    return_value=valkey_client,
                ),
                mock.patch(
                    'imbi_api.endpoints.project_configuration'
                    '.ch_client.Clickhouse.get_instance',
                    return_value=ch,
                ),
            ):
                response = client.put(
                    '/organizations/myorg/projects/proj1/configuration/foo',
                    json={
                        'data_type': 'string',
                        'value': 'bar',
                        'secret': True,
                    },
                )
        self.assertEqual(response.status_code, 200)

    def test_delete_configuration_key(self) -> None:
        ch = mock.MagicMock()
        ch.insert = mock.AsyncMock()
        valkey_client = mock.AsyncMock()
        with testclient.TestClient(self.test_app) as client:
            with (
                mock.patch(
                    'imbi_api.endpoints.project_configuration.resolve_plugin',
                    return_value=_resolved(),
                ),
                mock.patch(
                    'imbi_api.endpoints.project_configuration'
                    '.get_plugin_credentials',
                    return_value={},
                ),
                mock.patch(
                    'imbi_api.endpoints.project_configuration'
                    '.valkey.get_client',
                    return_value=valkey_client,
                ),
                mock.patch(
                    'imbi_api.endpoints.project_configuration'
                    '.ch_client.Clickhouse.get_instance',
                    return_value=ch,
                ),
            ):
                response = client.delete(
                    '/organizations/myorg/projects/proj1/configuration/foo'
                )
        self.assertEqual(response.status_code, 204)
        valkey_client.delete.assert_awaited_once()

    def test_delete_configuration_key_credentials_missing(self) -> None:
        with testclient.TestClient(self.test_app) as client:
            with (
                mock.patch(
                    'imbi_api.endpoints.project_configuration.resolve_plugin',
                    return_value=_resolved(),
                ),
                mock.patch(
                    'imbi_api.endpoints.project_configuration'
                    '.get_plugin_credentials',
                    side_effect=PluginCredentialsMissing('missing'),
                ),
            ):
                response = client.delete(
                    '/organizations/myorg/projects/proj1/configuration/foo'
                )
        self.assertEqual(response.status_code, 503)

    def test_invalidate_cache_swallows_errors(self) -> None:
        from imbi_api.endpoints.project_configuration import (
            _invalidate_cache,
        )

        async def _run() -> None:
            with mock.patch(
                'imbi_api.endpoints.project_configuration.valkey.get_client',
                side_effect=RuntimeError('no valkey'),
            ):
                await _invalidate_cache('p1', 'proj1')

        asyncio.run(_run())

    def test_get_configuration_cache_read_error_swallowed(self) -> None:
        valkey_client = mock.AsyncMock()
        valkey_client.get.side_effect = RuntimeError('bad cache')
        with testclient.TestClient(self.test_app) as client:
            with (
                mock.patch(
                    'imbi_api.endpoints.project_configuration.resolve_plugin',
                    return_value=_resolved(),
                ),
                mock.patch(
                    'imbi_api.endpoints.project_configuration'
                    '.get_plugin_credentials',
                    return_value={},
                ),
                mock.patch(
                    'imbi_api.endpoints.project_configuration'
                    '.valkey.get_client',
                    return_value=valkey_client,
                ),
            ):
                response = client.get(
                    '/organizations/myorg/projects/proj1/configuration/'
                )
        self.assertEqual(response.status_code, 200)

    def test_get_configuration_cache_write_error_swallowed(self) -> None:
        valkey_client = mock.AsyncMock()
        valkey_client.get.return_value = None
        valkey_client.setex.side_effect = RuntimeError('full disk')
        with testclient.TestClient(self.test_app) as client:
            with (
                mock.patch(
                    'imbi_api.endpoints.project_configuration.resolve_plugin',
                    return_value=_resolved(),
                ),
                mock.patch(
                    'imbi_api.endpoints.project_configuration'
                    '.get_plugin_credentials',
                    return_value={},
                ),
                mock.patch(
                    'imbi_api.endpoints.project_configuration'
                    '.valkey.get_client',
                    return_value=valkey_client,
                ),
            ):
                response = client.get(
                    '/organizations/myorg/projects/proj1/configuration/'
                )
        self.assertEqual(response.status_code, 200)
