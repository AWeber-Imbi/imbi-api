"""Tests for scoring history, rollup, and rescore endpoints."""

from __future__ import annotations

import datetime
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from imbi_common import graph

from imbi_api import app, models
from imbi_api import scoring as scoring_di


class ScoringEndpointsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        from imbi_api.auth import permissions

        self.test_app = app.create_app()

        self.user = models.User(
            email='admin@example.com',
            display_name='Admin',
            is_active=True,
            is_admin=True,
            is_service_account=False,
            created_at=datetime.datetime.now(datetime.UTC),
        )
        self.auth = permissions.AuthContext(
            user=self.user,
            session_id='s',
            auth_method='jwt',
            permissions=set(),
        )

        async def mock_user() -> permissions.AuthContext:
            return self.auth

        self.test_app.dependency_overrides[permissions.get_current_user] = (
            mock_user
        )

        self.mock_db = mock.AsyncMock(spec=graph.Graph)
        self.mock_valkey = mock.AsyncMock()
        self.mock_valkey.set = mock.AsyncMock(return_value=True)
        self.mock_valkey.xadd = mock.AsyncMock()

        self.test_app.dependency_overrides[graph._inject_graph] = (
            lambda: self.mock_db
        )
        self.test_app.dependency_overrides[
            scoring_di._inject_optional_client
        ] = lambda: self.mock_valkey
        self.client = TestClient(self.test_app)

    def test_history_raw(self) -> None:
        self.mock_db.execute = mock.AsyncMock(return_value=[{'id': 'p1'}])
        with mock.patch(
            'imbi_api.endpoints.scoring.clickhouse.query',
            mock.AsyncMock(
                return_value=[
                    {
                        'timestamp': '2026-04-01T00:00:00',
                        'score': 80.0,
                        'previous_score': 70.0,
                        'change_reason': 'attribute_change',
                    }
                ]
            ),
        ):
            response = self.client.get(
                '/organizations/eng/projects/p1/score/history',
            )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['granularity'], 'raw')
        self.assertEqual(len(body['points']), 1)

    def test_history_404(self) -> None:
        self.mock_db.execute = mock.AsyncMock(return_value=[])
        response = self.client.get(
            '/organizations/eng/projects/missing/score/history',
        )
        self.assertEqual(response.status_code, 404)

    def test_rollup(self) -> None:
        with mock.patch(
            'imbi_api.endpoints.scoring.clickhouse.query',
            mock.AsyncMock(
                return_value=[
                    {
                        'key': 'platform',
                        'latest_score': 90.0,
                        'avg_score': 85.0,
                        'last_updated': '2026-04-01',
                    }
                ]
            ),
        ):
            response = self.client.get(
                '/scores/rollup', params={'dimension': 'team'}
            )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body[0]['key'], 'platform')

    def test_rescore_all(self) -> None:
        self.mock_db.execute = mock.AsyncMock(
            return_value=[{'id': 'p1'}, {'id': 'p2'}]
        )
        response = self.client.post('/scoring/rescore', json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['enqueued'], 2)
        self.assertEqual(self.mock_valkey.xadd.await_count, 2)

    def test_rescore_debounce_blocks_duplicate(self) -> None:
        self.mock_db.execute = mock.AsyncMock(
            return_value=[{'id': 'p1'}, {'id': 'p1'}]
        )
        self.mock_valkey.set = mock.AsyncMock(side_effect=[True, False])
        response = self.client.post('/scoring/rescore', json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['enqueued'], 1)
