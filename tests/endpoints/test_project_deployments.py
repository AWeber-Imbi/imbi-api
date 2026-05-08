"""Tests for the project deployment plugin endpoints."""

import datetime
import typing
import unittest
from unittest import mock

from fastapi import testclient
from imbi_common import graph
from imbi_common.plugins.base import (
    Commit,
    CompareResult,
    DeploymentPlugin,
    DeploymentRun,
    PluginManifest,
    Ref,
)
from imbi_common.plugins.registry import RegistryEntry

from imbi_api import app, models
from imbi_api.auth import password, permissions
from imbi_api.plugins.resolution import ResolvedPlugin


class _FakeDeploymentPlugin(DeploymentPlugin):
    manifest = PluginManifest(
        slug='github-deployment',
        name='GitHub Deployment',
        plugin_type='deployment',
    )

    async def list_refs(  # type: ignore[override]
        self, ctx, credentials, kind='all', query=None
    ):
        return [
            Ref(name='main', kind='default', sha='m-sha', is_default=True),
            Ref(name='feature/x', kind='branch', sha='fx'),
        ]

    async def list_commits(  # type: ignore[override]
        self, ctx, credentials, ref, limit=25
    ):
        return [
            Commit(
                sha='abc1234567',
                short_sha='abc1234',
                message='Top',
                is_head=True,
            ),
            Commit(sha='def5678901', short_sha='def5678', message='prev'),
        ]

    async def resolve_committish(  # type: ignore[override]
        self, ctx, credentials, committish
    ):
        return Commit(sha=committish, short_sha=committish[:7], message='hi')

    async def compare(  # type: ignore[override]
        self, ctx, credentials, base, head
    ):
        return CompareResult(base_sha=base, head_sha=head, ahead=1, behind=0)

    async def trigger_deployment(  # type: ignore[override]
        self, ctx, credentials, ref_or_sha, inputs=None
    ):
        return DeploymentRun(
            run_id='42',
            run_url='https://gh/runs/42',
            status='queued',
        )

    async def get_deployment_status(  # type: ignore[override]
        self, ctx, credentials, run_id
    ):
        return DeploymentRun(run_id=run_id, status='in_progress')


def _entry() -> RegistryEntry:
    return RegistryEntry(
        handler_cls=_FakeDeploymentPlugin,
        manifest=_FakeDeploymentPlugin.manifest,
        package_name='imbi-plugin-github',
        package_version='0.1.0',
    )


_MODULE = 'imbi_api.endpoints.project_deployments'


class ProjectDeploymentsTestCase(unittest.TestCase):
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
                'project:deployment:read',
                'project:deployment:write',
            },
        )

        async def mock_get_current_user() -> permissions.AuthContext:
            return self.auth_context

        self.test_app.dependency_overrides[permissions.get_current_user] = (
            mock_get_current_user
        )
        self.mock_db = mock.AsyncMock(spec=graph.Graph)
        self.test_app.dependency_overrides[graph._inject_graph] = lambda: (
            self.mock_db
        )

        self.mocks = {
            'resolve_plugin': self._start(
                mock.patch(
                    f'{_MODULE}.resolve_plugin', return_value=self._resolved()
                )
            ),
            'lookup_project_slugs': self._start(
                mock.patch(
                    f'{_MODULE}.lookup_project_slugs',
                    return_value=('proj', 'team'),
                )
            ),
            'attach_identity': self._start(
                mock.patch(
                    f'{_MODULE}.attach_identity',
                    side_effect=lambda db, ctx, resolved, auth: ctx,
                )
            ),
            'get_plugin_credentials': self._start(
                mock.patch(
                    f'{_MODULE}.get_plugin_credentials',
                    return_value={'access_token': 'gho_test'},
                )
            ),
        }

    def _start(self, patcher: typing.Any) -> mock.MagicMock:
        m = patcher.start()
        self.addCleanup(patcher.stop)
        return m

    def _resolved(self) -> ResolvedPlugin:
        return ResolvedPlugin(
            plugin_id='p-1',
            plugin_slug='github-deployment',
            entry=_entry(),
            options={'owner': 'octo', 'repo': 'demo'},
        )

    def test_list_refs(self) -> None:
        with testclient.TestClient(self.test_app) as client:
            response = client.get(
                '/organizations/myorg/projects/proj1/deployments/refs'
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]['name'], 'main')
        self.assertTrue(data[0]['is_default'])

    def test_list_commits(self) -> None:
        with testclient.TestClient(self.test_app) as client:
            response = client.get(
                '/organizations/myorg/projects/proj1/deployments/'
                'refs/main/commits'
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 2)
        self.assertTrue(data[0]['is_head'])

    def test_resolve_commit(self) -> None:
        with testclient.TestClient(self.test_app) as client:
            response = client.get(
                '/organizations/myorg/projects/proj1/deployments/'
                'commits/abc1234'
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['sha'], 'abc1234')

    def test_compare(self) -> None:
        with testclient.TestClient(self.test_app) as client:
            response = client.get(
                '/organizations/myorg/projects/proj1/deployments/'
                'compare?base=v1&head=v2'
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['ahead'], 1)
        self.assertEqual(data['head_sha'], 'v2')

    def test_compare_missing_query_param_400(self) -> None:
        with testclient.TestClient(self.test_app) as client:
            response = client.get(
                '/organizations/myorg/projects/proj1/deployments/compare'
            )
        self.assertEqual(response.status_code, 422)

    def test_trigger_deploy(self) -> None:
        with testclient.TestClient(self.test_app) as client:
            response = client.post(
                '/organizations/myorg/projects/proj1/deployments',
                json={
                    'action': 'deploy',
                    'environment': 'testing',
                    'committish': 'main',
                    'ref_label': 'main',
                },
            )
        self.assertEqual(response.status_code, 202)
        data = response.json()
        self.assertEqual(data['plugin_slug'], 'github-deployment')
        self.assertEqual(data['run']['run_id'], '42')
        self.assertEqual(data['run']['status'], 'queued')

    def test_trigger_redeploy(self) -> None:
        with testclient.TestClient(self.test_app) as client:
            response = client.post(
                '/organizations/myorg/projects/proj1/deployments',
                json={
                    'action': 'redeploy',
                    'environment': 'staging',
                    'committish': 'v1.2.3',
                },
            )
        self.assertEqual(response.status_code, 202)

    def test_trigger_invalid_action(self) -> None:
        with testclient.TestClient(self.test_app) as client:
            response = client.post(
                '/organizations/myorg/projects/proj1/deployments',
                json={
                    'action': 'promote',
                    'environment': 'staging',
                    'committish': 'v1',
                },
            )
        self.assertEqual(response.status_code, 422)

    def test_no_credentials_returns_503(self) -> None:
        self.mocks['get_plugin_credentials'].return_value = {}
        with testclient.TestClient(self.test_app) as client:
            response = client.get(
                '/organizations/myorg/projects/proj1/deployments/refs'
            )
        self.assertEqual(response.status_code, 503)

    def test_write_permission_required_for_post(self) -> None:
        non_admin = models.User(
            email='dev@example.com',
            display_name='Dev',
            is_active=True,
            is_admin=False,
            password_hash=password.hash_password('testpassword123'),
            created_at=datetime.datetime.now(datetime.UTC),
        )
        self.auth_context = permissions.AuthContext(
            user=non_admin,
            session_id='test-session',
            auth_method='jwt',
            permissions={'project:deployment:read'},
        )

        async def mock_get_current_user() -> permissions.AuthContext:
            return self.auth_context

        self.test_app.dependency_overrides[permissions.get_current_user] = (
            mock_get_current_user
        )
        with testclient.TestClient(self.test_app) as client:
            response = client.post(
                '/organizations/myorg/projects/proj1/deployments',
                json={
                    'action': 'deploy',
                    'environment': 'testing',
                    'committish': 'main',
                },
            )
        self.assertEqual(response.status_code, 403)
