import unittest
from unittest import mock

from imbi_common.age import client


class AGEClientTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()

        # Clear the singleton instance
        client.AGE._instance = None

        self.mock_pool = mock.AsyncMock()
        self.mock_conn = mock.AsyncMock()

        # Set up connection context manager
        mock_conn_cm = mock.AsyncMock()
        mock_conn_cm.__aenter__.return_value = self.mock_conn
        mock_conn_cm.__aexit__.return_value = None

        # Make pool.connection() return a context manager
        self.mock_pool.connection = mock.MagicMock(return_value=mock_conn_cm)
        self.mock_pool.close = mock.AsyncMock()
        self.mock_pool.open = mock.AsyncMock()

        # Patch the pool creation
        self.pool_patcher = mock.patch(
            'imbi_common.age.client.AsyncConnectionPool',
            return_value=self.mock_pool,
        )
        self.mock_pool_class = self.pool_patcher.start()
        self.addCleanup(self.pool_patcher.stop)

    async def test_graph_singleton(self) -> None:
        """Test that AGE uses singleton pattern."""
        instance1 = client.AGE.get_instance()
        instance2 = client.AGE.get_instance()
        self.assertIs(instance1, instance2)

    async def test_initialize(self) -> None:
        """Test graph initialization creates indexes."""
        graph = client.AGE.get_instance()
        await graph.initialize()
        self.mock_pool.connection.assert_called()
        self.mock_conn.execute.assert_called()

    async def test_initialize_with_duplicate_index(self) -> None:
        """Test initializing indexes handles duplicate errors gracefully."""
        import psycopg.errors

        graph = client.AGE.get_instance()

        # Mock execute to raise DuplicateTable on index creation
        call_count = 0
        setup_count = len(client.constants.SETUP)
        label_count = len(client.constants.ENSURE_LABELS)
        skip_count = setup_count + 1 + label_count  # SETUP + ENSURE_GRAPH

        async def side_effect(*args: object, **kwargs: object) -> None:
            nonlocal call_count
            call_count += 1
            if call_count > skip_count:
                raise psycopg.errors.DuplicateTable('Already exists')

        self.mock_conn.execute.side_effect = side_effect

        # Should not raise, should handle the error
        await graph.initialize()

        # Verify connection was used
        self.mock_pool.connection.assert_called()

    async def test_aclose(self) -> None:
        """Test graph connection close."""
        graph = client.AGE.get_instance()
        # Force pool creation
        await graph._ensure_pool()
        await graph.aclose()
        self.mock_pool.close.assert_called_once()
