"""Tests for service plugin CRUD endpoints."""

import datetime
import json
import unittest
from unittest import mock

from fastapi import testclient
from imbi_common import graph

from imbi_api import app, models
from imbi_api.auth import password, permissions


class ServicePluginsEndpointTestCase(unittest.TestCase):
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
                'third_party_service:read',
                'third_party_service:update',
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

    def test_list_plugins_empty(self) -> None:
        self.mock_db.execute.return_value = []
        with mock.patch(
            'imbi_api.endpoints.service_plugins.list_plugins',
            return_value=[],
        ):
            with testclient.TestClient(self.test_app) as client:
                response = client.get(
                    '/organizations/myorg/third-party-services/github/plugins/'
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_list_plugins_with_results(self) -> None:
        plugin_raw = json.dumps(
            {
                'id': 'abc123',
                'plugin_slug': 'ssm',
                'label': 'SSM Config',
                'options': '{}',
                'api_version': 1,
            }
        )
        svc_raw = json.dumps({'slug': 'github'})
        self.mock_db.execute.return_value = [
            {
                'plugin': plugin_raw,
                'svc': svc_raw,
            }
        ]
        with mock.patch(
            'imbi_api.endpoints.service_plugins.list_plugins',
            return_value=[],
        ):
            with testclient.TestClient(self.test_app) as client:
                response = client.get(
                    '/organizations/myorg/third-party-services/github/plugins/'
                )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['plugin_slug'], 'ssm')
        self.assertEqual(data[0]['status'], 'unavailable')

    def test_create_plugin_slug_not_installed(self) -> None:
        from imbi_common.plugins.errors import PluginNotFoundError

        self.mock_db.execute.return_value = []
        with mock.patch(
            'imbi_api.endpoints.service_plugins.get_plugin',
            side_effect=PluginNotFoundError('ssm'),
        ):
            with testclient.TestClient(self.test_app) as client:
                response = client.post(
                    '/organizations/myorg/'
                    'third-party-services/github/plugins/',
                    json={
                        'plugin_slug': 'ssm',
                        'label': 'My SSM',
                        'options': {},
                    },
                )
        self.assertEqual(response.status_code, 400)

    def test_delete_plugin_conflict(self) -> None:
        self.mock_db.execute.return_value = [{'cnt': '2'}]
        with testclient.TestClient(self.test_app) as client:
            response = client.delete(
                '/organizations/myorg/'
                'third-party-services/github/plugins/abc123'
            )
        self.assertEqual(response.status_code, 409)

    def test_delete_plugin_not_found(self) -> None:
        self.mock_db.execute.return_value = [{'cnt': '0'}]

        def _side_effect(*args: object, **kwargs: object) -> list[dict]:
            call_count = self.mock_db.execute.call_count
            if call_count == 1:
                return [{'cnt': '0'}]
            return [{'deleted': '0'}]

        self.mock_db.execute.side_effect = _side_effect
        with testclient.TestClient(self.test_app) as client:
            response = client.delete(
                '/organizations/myorg/'
                'third-party-services/github/plugins/abc123'
            )
        self.assertEqual(response.status_code, 404)
