"""Tests for authorization and permission checking."""

import datetime
import unittest
from unittest import mock

from fastapi import testclient

from imbi import app, models, settings
from imbi.auth import core, permissions


class PermissionLoadingTestCase(unittest.IsolatedAsyncioTestCase):
    """Test permission loading from roles and groups."""

    async def test_load_user_permissions_direct_role(self) -> None:
        """Test loading permissions from direct role assignment."""
        mock_result = mock.AsyncMock()
        mock_result.data.return_value = [
            {'permissions': ['blueprint:read', 'blueprint:write']}
        ]
        mock_result.__aenter__.return_value = mock_result
        mock_result.__aexit__.return_value = None

        with mock.patch('imbi.neo4j.run', return_value=mock_result):
            perms = await permissions.load_user_permissions('testuser')

        self.assertEqual(perms, {'blueprint:read', 'blueprint:write'})

    async def test_load_user_permissions_empty(self) -> None:
        """Test loading permissions for user with no roles."""
        mock_result = mock.AsyncMock()
        mock_result.data.return_value = []
        mock_result.__aenter__.return_value = mock_result
        mock_result.__aexit__.return_value = None

        with mock.patch('imbi.neo4j.run', return_value=mock_result):
            perms = await permissions.load_user_permissions('testuser')

        self.assertEqual(perms, set())


class AuthenticateJWTTestCase(unittest.IsolatedAsyncioTestCase):
    """Test JWT authentication."""

    async def asyncSetUp(self) -> None:
        self.auth_settings = settings.Auth(
            jwt_secret='test-secret-key-32-characters!',
            jwt_algorithm='HS256',
            access_token_expire_seconds=3600,
        )
        self.test_user = models.User(
            username='testuser',
            email='test@example.com',
            display_name='Test User',
            password_hash=core.hash_password('TestPassword123!'),
            is_active=True,
            is_admin=False,
            is_service_account=False,
            created_at=datetime.datetime.now(datetime.UTC),
        )

    async def test_authenticate_jwt_success(self) -> None:
        """Test successful JWT authentication."""
        token, jti = core.create_access_token('testuser', self.auth_settings)

        # Mock Neo4j queries
        mock_token_result = mock.AsyncMock()
        mock_token_result.data.return_value = [{'revoked': False}]
        mock_token_result.__aenter__.return_value = mock_token_result
        mock_token_result.__aexit__.return_value = None

        mock_user_result = mock.AsyncMock()
        mock_user_result.data.return_value = [
            {
                'u': {
                    'username': 'testuser',
                    'email': 'test@example.com',
                    'display_name': 'Test User',
                    'password_hash': self.test_user.password_hash,
                    'is_active': True,
                    'is_admin': False,
                    'is_service_account': False,
                    'created_at': self.test_user.created_at,
                }
            }
        ]
        mock_user_result.__aenter__.return_value = mock_user_result
        mock_user_result.__aexit__.return_value = None

        mock_update_result = mock.AsyncMock()
        mock_update_result.__aenter__.return_value = mock_update_result
        mock_update_result.__aexit__.return_value = None

        mock_perm_result = mock.AsyncMock()
        mock_perm_result.data.return_value = [
            {'permissions': ['blueprint:read']}
        ]
        mock_perm_result.__aenter__.return_value = mock_perm_result
        mock_perm_result.__aexit__.return_value = None

        with mock.patch(
            'imbi.neo4j.run',
            side_effect=[
                mock_token_result,
                mock_user_result,
                mock_update_result,
                mock_perm_result,
            ],
        ):
            auth_context = await permissions.authenticate_jwt(
                token, self.auth_settings
            )

        self.assertEqual(auth_context.user.username, 'testuser')
        self.assertEqual(auth_context.auth_method, 'jwt')
        self.assertEqual(auth_context.session_id, jti)
        self.assertIn('blueprint:read', auth_context.permissions)

    async def test_authenticate_jwt_expired(self) -> None:
        """Test authentication with expired token."""
        expired_settings = settings.Auth(
            jwt_secret='test-secret-key-32-characters!',
            access_token_expire_seconds=-1,  # Already expired
        )
        token, _ = core.create_access_token('testuser', expired_settings)

        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            await permissions.authenticate_jwt(token, expired_settings)

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn('expired', str(ctx.exception.detail).lower())

    async def test_authenticate_jwt_invalid_token(self) -> None:
        """Test authentication with invalid token."""
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            await permissions.authenticate_jwt(
                'invalid.token.here', self.auth_settings
            )

        self.assertEqual(ctx.exception.status_code, 401)

    async def test_authenticate_jwt_revoked_token(self) -> None:
        """Test authentication with revoked token."""
        token, _ = core.create_access_token('testuser', self.auth_settings)

        # Mock token as revoked
        mock_result = mock.AsyncMock()
        mock_result.data.return_value = [{'revoked': True}]
        mock_result.__aenter__.return_value = mock_result
        mock_result.__aexit__.return_value = None

        from fastapi import HTTPException

        with (
            mock.patch('imbi.neo4j.run', return_value=mock_result),
            self.assertRaises(HTTPException) as ctx,
        ):
            await permissions.authenticate_jwt(token, self.auth_settings)

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn('revoked', str(ctx.exception.detail).lower())

    async def test_authenticate_jwt_inactive_user(self) -> None:
        """Test authentication with inactive user."""
        token, _jti = core.create_access_token('testuser', self.auth_settings)

        # Mock Neo4j queries
        mock_token_result = mock.AsyncMock()
        mock_token_result.data.return_value = [{'revoked': False}]
        mock_token_result.__aenter__.return_value = mock_token_result
        mock_token_result.__aexit__.return_value = None

        inactive_user = models.User(
            username='testuser',
            email='test@example.com',
            display_name='Test User',
            password_hash=core.hash_password('TestPassword123!'),
            is_active=False,  # Inactive
            is_admin=False,
            is_service_account=False,
            created_at=datetime.datetime.now(datetime.UTC),
        )

        mock_user_result = mock.AsyncMock()
        mock_user_result.data.return_value = [
            {
                'u': {
                    'username': 'testuser',
                    'email': 'test@example.com',
                    'display_name': 'Test User',
                    'password_hash': inactive_user.password_hash,
                    'is_active': False,
                    'is_admin': False,
                    'is_service_account': False,
                    'created_at': inactive_user.created_at,
                }
            }
        ]
        mock_user_result.__aenter__.return_value = mock_user_result
        mock_user_result.__aexit__.return_value = None

        from fastapi import HTTPException

        with (
            mock.patch(
                'imbi.neo4j.run',
                side_effect=[mock_token_result, mock_user_result],
            ),
            self.assertRaises(HTTPException) as ctx,
        ):
            await permissions.authenticate_jwt(token, self.auth_settings)

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn('inactive', str(ctx.exception.detail).lower())


class ProtectedEndpointTestCase(unittest.TestCase):
    """Test protected endpoints require authentication and permissions."""

    def setUp(self) -> None:
        self.client = testclient.TestClient(app.create_app())
        self.auth_settings = settings.Auth(
            jwt_secret='test-secret-key-32-characters!',
            jwt_algorithm='HS256',
            access_token_expire_seconds=3600,
        )

    def test_blueprint_list_without_auth(self) -> None:
        """Test accessing blueprint list without authentication."""
        response = self.client.get('/blueprints')
        self.assertEqual(response.status_code, 401)

    def test_blueprint_list_with_valid_token(self) -> None:
        """Test accessing blueprint list with valid token and permission."""
        token, _jti = core.create_access_token('testuser', self.auth_settings)

        test_user = models.User(
            username='testuser',
            email='test@example.com',
            display_name='Test User',
            password_hash=core.hash_password('TestPassword123!'),
            is_active=True,
            is_admin=False,
            is_service_account=False,
            created_at=datetime.datetime.now(datetime.UTC),
        )

        # Mock Neo4j queries for authentication
        mock_token_result = mock.AsyncMock()
        mock_token_result.data.return_value = [{'revoked': False}]
        mock_token_result.__aenter__.return_value = mock_token_result
        mock_token_result.__aexit__.return_value = None

        mock_user_result = mock.AsyncMock()
        mock_user_result.data.return_value = [
            {
                'u': {
                    'username': 'testuser',
                    'email': 'test@example.com',
                    'display_name': 'Test User',
                    'password_hash': test_user.password_hash,
                    'is_active': True,
                    'is_service_account': False,
                    'created_at': test_user.created_at,
                }
            }
        ]
        mock_user_result.__aenter__.return_value = mock_user_result
        mock_user_result.__aexit__.return_value = None

        mock_update_result = mock.AsyncMock()
        mock_update_result.__aenter__.return_value = mock_update_result
        mock_update_result.__aexit__.return_value = None

        mock_perm_result = mock.AsyncMock()
        mock_perm_result.data.return_value = [
            {'permissions': ['blueprint:read']}
        ]
        mock_perm_result.__aenter__.return_value = mock_perm_result
        mock_perm_result.__aexit__.return_value = None

        # Mock fetch_nodes for blueprint listing
        async def mock_fetch_nodes(*args, **kwargs):
            yield models.Blueprint(
                name='Test Blueprint',
                type='Organization',
                json_schema={'type': 'object'},
            )

        with (
            mock.patch('imbi.settings.get_auth_settings') as mock_settings,
            mock.patch(
                'imbi.neo4j.run',
                side_effect=[
                    mock_token_result,
                    mock_user_result,
                    mock_update_result,
                    mock_perm_result,
                ],
            ),
            mock.patch('imbi.neo4j.fetch_nodes', side_effect=mock_fetch_nodes),
        ):
            mock_settings.return_value = self.auth_settings

            response = self.client.get(
                '/blueprints', headers={'Authorization': f'Bearer {token}'}
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsInstance(data, list)

    def test_blueprint_list_without_permission(self) -> None:
        """Test accessing blueprint list without required permission."""
        token, _jti = core.create_access_token('testuser', self.auth_settings)

        test_user = models.User(
            username='testuser',
            email='test@example.com',
            display_name='Test User',
            password_hash=core.hash_password('TestPassword123!'),
            is_active=True,
            is_admin=False,
            is_service_account=False,
            created_at=datetime.datetime.now(datetime.UTC),
        )

        # Mock Neo4j queries
        mock_token_result = mock.AsyncMock()
        mock_token_result.data.return_value = [{'revoked': False}]
        mock_token_result.__aenter__.return_value = mock_token_result
        mock_token_result.__aexit__.return_value = None

        mock_user_result = mock.AsyncMock()
        mock_user_result.data.return_value = [
            {
                'u': {
                    'username': 'testuser',
                    'email': 'test@example.com',
                    'display_name': 'Test User',
                    'password_hash': test_user.password_hash,
                    'is_active': True,
                    'is_service_account': False,
                    'created_at': test_user.created_at,
                }
            }
        ]
        mock_user_result.__aenter__.return_value = mock_user_result
        mock_user_result.__aexit__.return_value = None

        mock_update_result = mock.AsyncMock()
        mock_update_result.__aenter__.return_value = mock_update_result
        mock_update_result.__aexit__.return_value = None

        # Mock permissions - user has NO permissions
        mock_perm_result = mock.AsyncMock()
        mock_perm_result.data.return_value = [{'permissions': []}]
        mock_perm_result.__aenter__.return_value = mock_perm_result
        mock_perm_result.__aexit__.return_value = None

        with (
            mock.patch('imbi.settings.get_auth_settings') as mock_settings,
            mock.patch(
                'imbi.neo4j.run',
                side_effect=[
                    mock_token_result,
                    mock_user_result,
                    mock_update_result,
                    mock_perm_result,
                ],
            ),
        ):
            mock_settings.return_value = self.auth_settings

            response = self.client.get(
                '/blueprints', headers={'Authorization': f'Bearer {token}'}
            )

        self.assertEqual(response.status_code, 403)
        self.assertIn('Permission denied', response.json()['detail'])


class ResourcePermissionTestCase(unittest.IsolatedAsyncioTestCase):
    """Test resource-level permission checking."""

    async def test_check_resource_permission_granted(self) -> None:
        """Test checking resource permission when granted."""
        mock_result = mock.AsyncMock()
        mock_result.data.return_value = [{'actions': ['read', 'write']}]
        mock_result.__aenter__.return_value = mock_result
        mock_result.__aexit__.return_value = None

        with mock.patch('imbi.neo4j.run', return_value=mock_result):
            has_access = await permissions.check_resource_permission(
                'testuser', 'Blueprint', 'test-blueprint', 'read'
            )

        self.assertTrue(has_access)

    async def test_check_resource_permission_denied(self) -> None:
        """Test checking resource permission when denied."""
        mock_result = mock.AsyncMock()
        mock_result.data.return_value = [{'actions': ['read']}]
        mock_result.__aenter__.return_value = mock_result
        mock_result.__aexit__.return_value = None

        with mock.patch('imbi.neo4j.run', return_value=mock_result):
            has_access = await permissions.check_resource_permission(
                'testuser', 'Blueprint', 'test-blueprint', 'delete'
            )

        self.assertFalse(has_access)

    async def test_check_resource_permission_no_access(self) -> None:
        """Test checking resource permission with no CAN_ACCESS."""
        mock_result = mock.AsyncMock()
        mock_result.data.return_value = []
        mock_result.__aenter__.return_value = mock_result
        mock_result.__aexit__.return_value = None

        with mock.patch('imbi.neo4j.run', return_value=mock_result):
            has_access = await permissions.check_resource_permission(
                'testuser', 'Blueprint', 'test-blueprint', 'read'
            )

        self.assertFalse(has_access)
