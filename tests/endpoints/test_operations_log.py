"""Tests for operations log endpoints."""

import datetime
import typing
import unittest
from unittest import mock

from fastapi import testclient
from imbi_common import clickhouse as imbi_clickhouse

from imbi_api import app
from imbi_api import models as api_models
from imbi_api.auth import permissions as api_permissions
from imbi_api.endpoints import operations_log

ALL_OPSLOG_PERMS: set[str] = {
    'operations_log:create',
    'operations_log:read',
    'operations_log:update',
    'operations_log:delete',
}


def _sample_row(**overrides: typing.Any) -> dict[str, typing.Any]:
    """Return a dict matching the ClickHouse row shape for an opslog entry."""
    base: dict[str, typing.Any] = {
        'id': 'entry-abc',
        'occurred_at': datetime.datetime(
            2026, 4, 17, 14, 22, 31, 412000, tzinfo=datetime.UTC
        ),
        'recorded_at': datetime.datetime(
            2026, 4, 17, 14, 22, 33, 1000, tzinfo=datetime.UTC
        ),
        'recorded_by': 'alice@example.com',
        'performed_by': 'alice@example.com',
        'completed_at': None,
        'project_id': 'proj-xyz',
        'project_slug': 'imbi-api',
        'environment_slug': 'production',
        'entry_type': 'Deployed',
        'description': 'Rolled out v2.4.0',
        'link': None,
        'notes': None,
        'ticket_slug': None,
        'version': '2.4.0',
        '_row_version': 1,
        'is_deleted': 0,
    }
    base.update(overrides)
    return base


class _OpsLogTestBase(unittest.IsolatedAsyncioTestCase):
    """Shared setup for operations-log endpoint tests."""

    permissions_granted: set[str] = ALL_OPSLOG_PERMS

    def setUp(self) -> None:
        self.test_app = app.create_app()
        self.admin = api_models.User(
            email='alice@example.com',
            display_name='Alice',
            password_hash='$argon2id$hash',
            is_active=True,
            is_admin=True,
            is_service_account=False,
            created_at=datetime.datetime.now(datetime.UTC),
        )
        self.auth_context = api_permissions.AuthContext(
            user=self.admin,
            session_id='test-session',
            auth_method='jwt',
            permissions=self.permissions_granted,
        )

        async def _current_user() -> api_permissions.AuthContext:
            return self.auth_context

        self.test_app.dependency_overrides[
            api_permissions.get_current_user
        ] = _current_user

        self.client = testclient.TestClient(self.test_app)

        # Patch ClickHouse:
        #   * query() is the module-level wrapper used for reads; returns
        #     list[dict].
        #   * insert is the class method Clickhouse.insert called directly
        #     with explicit column names/values (the module-level wrapper
        #     only accepts pydantic models and strips the alias).
        self.insert_patcher = mock.patch.object(
            imbi_clickhouse.client.Clickhouse,
            'insert',
            new_callable=mock.AsyncMock,
        )
        self.query_patcher = mock.patch(
            'imbi_common.clickhouse.query',
            new_callable=mock.AsyncMock,
        )
        self.mock_insert = self.insert_patcher.start()
        self.mock_query = self.query_patcher.start()
        self.addCleanup(self.insert_patcher.stop)
        self.addCleanup(self.query_patcher.stop)


class CursorCodecTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        ts = datetime.datetime(
            2026, 4, 17, 14, 22, 31, 412000, tzinfo=datetime.UTC
        )
        entry_id = 'V1StGXR8_Z5jdHi6B-myT'
        encoded = operations_log._encode_cursor(ts, entry_id)
        self.assertIsInstance(encoded, str)
        decoded = operations_log._decode_cursor(encoded)
        self.assertIsNotNone(decoded)
        assert decoded is not None
        decoded_ts, decoded_id = decoded
        self.assertEqual(decoded_ts, ts)
        self.assertEqual(decoded_id, entry_id)

    def test_decode_malformed_returns_none(self) -> None:
        self.assertIsNone(operations_log._decode_cursor('!!!not-base64!!!'))

    def test_decode_wrong_format_returns_none(self) -> None:
        import base64

        payload = base64.urlsafe_b64encode(b'missing-separator').decode()
        self.assertIsNone(operations_log._decode_cursor(payload))

    def test_decode_empty_string_returns_none(self) -> None:
        self.assertIsNone(operations_log._decode_cursor(''))


class PostOperationLogTests(_OpsLogTestBase):
    def _valid_body(self) -> dict[str, typing.Any]:
        return {
            'project_id': 'proj-xyz',
            'project_slug': 'imbi-api',
            'environment_slug': 'production',
            'entry_type': 'Deployed',
            'description': 'Rolled out v2.4.0',
        }

    def test_create_minimum_body_returns_201(self) -> None:
        response = self.client.post(
            '/operations-log/', json=self._valid_body()
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body['entry_type'], 'Deployed')
        self.assertEqual(body['project_slug'], 'imbi-api')
        # Server-stamped fields
        self.assertIsNotNone(body['id'])
        self.assertIsNotNone(body['recorded_at'])
        self.assertEqual(body['recorded_by'], 'alice@example.com')
        self.assertEqual(body['performed_by'], 'alice@example.com')
        # Internal fields excluded
        self.assertNotIn('_row_version', body)
        self.assertNotIn('row_version', body)
        self.assertNotIn('is_deleted', body)
        self.mock_insert.assert_awaited_once()

    def test_create_with_explicit_performed_by(self) -> None:
        body = self._valid_body() | {'performed_by': 'ci-bot'}
        response = self.client.post('/operations-log/', json=body)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['performed_by'], 'ci-bot')

    def test_create_validation_error(self) -> None:
        body = self._valid_body()
        del body['entry_type']
        response = self.client.post('/operations-log/', json=body)
        self.assertEqual(response.status_code, 400)

    def test_create_forbidden_without_permission(self) -> None:
        non_admin = api_models.User(
            email='bob@example.com',
            display_name='Bob',
            password_hash='$argon2id$hash',
            is_active=True,
            is_admin=False,
            is_service_account=False,
            created_at=datetime.datetime.now(datetime.UTC),
        )
        self.auth_context = api_permissions.AuthContext(
            user=non_admin,
            session_id='test-session',
            auth_method='jwt',
            permissions=set(),
        )

        async def _current_user() -> api_permissions.AuthContext:
            return self.auth_context

        self.test_app.dependency_overrides[
            api_permissions.get_current_user
        ] = _current_user

        response = self.client.post(
            '/operations-log/', json=self._valid_body()
        )
        self.assertEqual(response.status_code, 403)
        self.mock_insert.assert_not_awaited()
