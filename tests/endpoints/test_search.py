"""Tests for the vector similarity search endpoint."""

import datetime
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from imbi_common import graph
from imbi_common.graph.client import SearchResult

from imbi_api import app, models
from imbi_api.auth import permissions


class SearchEndpointTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.test_app = app.create_app()

        admin_user = models.User(
            email='admin@example.com',
            display_name='Admin User',
            password_hash='$argon2id$hashed',
            is_active=True,
            is_admin=True,
            is_service_account=False,
            created_at=datetime.datetime.now(datetime.UTC),
        )
        auth_context = permissions.AuthContext(
            user=admin_user,
            session_id='test-session',
            auth_method='jwt',
            permissions={'search:read'},
        )

        async def mock_get_current_user():
            return auth_context

        self.test_app.dependency_overrides[permissions.get_current_user] = (
            mock_get_current_user
        )

        self.mock_db = mock.AsyncMock(spec=graph.Graph)
        self.test_app.dependency_overrides[graph._inject_graph] = lambda: (
            self.mock_db
        )

        self.client = TestClient(self.test_app)

    def _make_result(
        self,
        node_label: str = 'Project',
        node_id: str = 'proj-1',
        attribute: str = 'description',
        chunk_text: str = 'a sample description',
        distance: float = 0.12,
    ) -> SearchResult:
        return SearchResult(
            node_label=node_label,
            node_id=node_id,
            attribute=attribute,
            chunk_text=chunk_text,
            distance=distance,
        )

    def test_basic_search(self) -> None:
        self.mock_db.search.return_value = [
            self._make_result(),
        ]
        response = self.client.get('/search?q=api+gateway')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['node_label'], 'Project')
        self.assertEqual(data[0]['node_id'], 'proj-1')
        self.assertEqual(data[0]['attribute'], 'description')
        self.assertAlmostEqual(data[0]['distance'], 0.12)

    def test_passes_query_to_db_search(self) -> None:
        self.mock_db.search.return_value = []
        self.client.get('/search?q=hello+world')
        self.mock_db.search.assert_awaited_once()
        call_args = self.mock_db.search.call_args
        self.assertEqual(call_args.args[0], 'hello world')

    def test_node_label_filter(self) -> None:
        self.mock_db.search.return_value = []
        self.client.get('/search?q=foo&node_label=Team')
        call_kwargs = self.mock_db.search.call_args.kwargs
        self.assertEqual(call_kwargs['node_label'], 'Team')

    def test_attribute_filter(self) -> None:
        self.mock_db.search.return_value = []
        self.client.get('/search?q=foo&attribute=name')
        call_kwargs = self.mock_db.search.call_args.kwargs
        self.assertEqual(call_kwargs['attribute'], 'name')

    def test_limit_param(self) -> None:
        self.mock_db.search.return_value = []
        self.client.get('/search?q=foo&limit=25')
        call_kwargs = self.mock_db.search.call_args.kwargs
        self.assertEqual(call_kwargs['limit'], 25)

    def test_threshold_param(self) -> None:
        self.mock_db.search.return_value = []
        self.client.get('/search?q=foo&threshold=0.5')
        call_kwargs = self.mock_db.search.call_args.kwargs
        self.assertAlmostEqual(call_kwargs['distance_threshold'], 0.5)

    def test_model_param(self) -> None:
        self.mock_db.search.return_value = []
        self.client.get('/search?q=foo&model=code')
        call_kwargs = self.mock_db.search.call_args.kwargs
        self.assertEqual(call_kwargs['model_name'], 'code')

    def test_empty_query_rejected(self) -> None:
        response = self.client.get('/search?q=')
        self.assertEqual(response.status_code, 422)

    def test_limit_too_large_rejected(self) -> None:
        response = self.client.get('/search?q=foo&limit=101')
        self.assertEqual(response.status_code, 422)

    def test_limit_zero_rejected(self) -> None:
        response = self.client.get('/search?q=foo&limit=0')
        self.assertEqual(response.status_code, 422)

    def test_missing_query_param_rejected(self) -> None:
        response = self.client.get('/search')
        self.assertEqual(response.status_code, 422)

    def test_multiple_results_returned(self) -> None:
        self.mock_db.search.return_value = [
            self._make_result(node_id='a', distance=0.05),
            self._make_result(node_id='b', distance=0.15),
            self._make_result(node_id='c', distance=0.30),
        ]
        response = self.client.get('/search?q=test')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 3)
        self.assertEqual(data[0]['node_id'], 'a')
        self.assertEqual(data[2]['node_id'], 'c')

    def test_no_filter_defaults(self) -> None:
        self.mock_db.search.return_value = []
        self.client.get('/search?q=test')
        call_kwargs = self.mock_db.search.call_args.kwargs
        self.assertIsNone(call_kwargs['node_label'])
        self.assertIsNone(call_kwargs['attribute'])
        self.assertIsNone(call_kwargs['distance_threshold'])
        self.assertEqual(call_kwargs['limit'], 10)
        self.assertEqual(call_kwargs['model_name'], 'text')


if __name__ == '__main__':
    unittest.main()
