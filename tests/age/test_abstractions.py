import unittest
from unittest import mock

from imbi_common import age
from imbi_common.age import client


class AGEAbstractionsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()

        # Clear the singleton instance
        client.AGE._instance = None

        self.mock_pool = mock.AsyncMock()
        self.mock_conn = mock.AsyncMock()
        self.mock_cursor = mock.AsyncMock()

        # Set up connection context manager
        mock_conn_cm = mock.AsyncMock()
        mock_conn_cm.__aenter__.return_value = self.mock_conn
        mock_conn_cm.__aexit__.return_value = None

        # Make pool.connection() return a context manager
        self.mock_pool.connection = mock.MagicMock(return_value=mock_conn_cm)
        self.mock_pool.close = mock.AsyncMock()
        self.mock_pool.open = mock.AsyncMock()

        # Mock cursor returned by execute
        self.mock_conn.execute.return_value = self.mock_cursor
        self.mock_cursor.fetchall.return_value = []

        # Patch the pool creation
        self.pool_patcher = mock.patch(
            'imbi_common.age.client.AsyncConnectionPool',
            return_value=self.mock_pool,
        )
        self.mock_pool_class = self.pool_patcher.start()
        self.addCleanup(self.pool_patcher.stop)

    async def test_initialize_function(self) -> None:
        """Test module-level initialize function."""
        await age.initialize()
        self.mock_pool.connection.assert_called()

    async def test_aclose_function(self) -> None:
        """Test module-level aclose function."""
        # Force pool creation first
        instance = client.AGE.get_instance()
        await instance._ensure_pool()
        await age.aclose()
        self.mock_pool.close.assert_called()

    async def test_session_context_manager(self) -> None:
        """Test session context manager."""
        async with age.session() as conn:
            self.assertEqual(conn, self.mock_conn)

    async def test_run_context_manager(self) -> None:
        """Test run context manager with query."""
        self.mock_cursor.fetchall.return_value = [
            ('test_value',),
        ]

        async with age.run('MATCH (n) RETURN n') as result:
            data = await result.data()
            self.assertEqual(len(data), 1)

        self.mock_conn.execute.assert_called()

    def test_cypher_property_params(self) -> None:
        """Test cypher property parameter generation."""
        params = {'id': '123', 'name': 'test'}
        result = age.cypher_property_params(params)
        self.assertEqual(result, 'id: $id, name: $name')

        # Test empty params
        self.assertEqual(age.cypher_property_params({}), '')
        self.assertEqual(
            age.cypher_property_params(
                None  # type: ignore[arg-type]
            ),
            '',
        )

    async def test_upsert_node(self) -> None:
        """Test upserting a node."""
        import pydantic

        class TestNode(pydantic.BaseModel):
            id: str
            name: str

        test_node = TestNode(id='123', name='Test Node')

        with mock.patch('imbi_common.age.query') as mock_query:
            mock_query.return_value = [{'nodeId': 'element123'}]
            result = await age.upsert(test_node, {'id': '123'})

            # Verify the result
            self.assertEqual(result, 'element123')

            # Verify query was called
            mock_query.assert_called_once()
            query_text = mock_query.call_args[0][0]

            # Verify query contains MERGE, ON CREATE SET, ON MATCH SET
            self.assertIn('MERGE', query_text)
            self.assertIn('ON CREATE SET', query_text)
            self.assertIn('ON MATCH SET', query_text)

    async def test_delete_node_found(self) -> None:
        """Test deleting a node that exists."""
        import pydantic

        class TestNode(pydantic.BaseModel):
            id: str
            name: str

        with mock.patch('imbi_common.age.query') as mock_query:
            mock_query.return_value = [{'deleted': 1}]
            result = await age.delete_node(TestNode, {'id': '123'})

            # Verify the result is True (node was deleted)
            self.assertTrue(result)

            # Verify query was called
            mock_query.assert_called_once()
            query_text = mock_query.call_args[0][0]

            # Verify query contains DELETE and WHERE
            self.assertIn('DELETE', query_text)
            self.assertIn('WHERE', query_text)
            self.assertIn('testnode', query_text.lower())
            self.assertIn('node.id = $id', query_text)

    async def test_delete_node_not_found(self) -> None:
        """Test deleting a node that doesn't exist."""
        import pydantic

        class TestNode(pydantic.BaseModel):
            id: str
            name: str

        with mock.patch('imbi_common.age.query') as mock_query:
            mock_query.return_value = [{'deleted': 0}]
            result = await age.delete_node(TestNode, {'id': '999'})

            # Verify the result is False (node was not found)
            self.assertFalse(result)

    async def test_delete_node_multiple_parameters(self) -> None:
        """Test deleting a node with multiple match parameters."""
        import pydantic

        class TestNode(pydantic.BaseModel):
            slug: str
            type: str

        with mock.patch('imbi_common.age.query') as mock_query:
            mock_query.return_value = [{'deleted': 1}]
            result = await age.delete_node(
                TestNode, {'slug': 'test-node', 'type': 'Project'}
            )

            # Verify the result is True
            self.assertTrue(result)

            # Verify query was called with correct parameters
            mock_query.assert_called_once()
            query_text = mock_query.call_args[0][0]
            kwargs = mock_query.call_args[1]

            # Verify both parameters are in the WHERE clause
            self.assertIn('node.slug = $slug', query_text)
            self.assertIn('node.type = $type', query_text)
            self.assertIn('AND', query_text)

            # Verify parameters were passed
            self.assertEqual(kwargs['slug'], 'test-node')
            self.assertEqual(kwargs['type'], 'Project')


class AGEHighLevelTestCase(unittest.IsolatedAsyncioTestCase):
    """Test cases for high-level graph functions."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()

        # Clear the singleton instance
        client.AGE._instance = None

        self.mock_pool = mock.AsyncMock()
        self.mock_conn = mock.AsyncMock()
        self.mock_cursor = mock.AsyncMock()

        # Set up connection context manager
        mock_conn_cm = mock.AsyncMock()
        mock_conn_cm.__aenter__.return_value = self.mock_conn
        mock_conn_cm.__aexit__.return_value = None

        self.mock_pool.connection = mock.MagicMock(return_value=mock_conn_cm)
        self.mock_pool.close = mock.AsyncMock()
        self.mock_pool.open = mock.AsyncMock()

        self.mock_conn.execute.return_value = self.mock_cursor
        self.mock_cursor.fetchall.return_value = []

        # Patch the pool creation
        self.pool_patcher = mock.patch(
            'imbi_common.age.client.AsyncConnectionPool',
            return_value=self.mock_pool,
        )
        self.mock_pool_class = self.pool_patcher.start()
        self.addCleanup(self.pool_patcher.stop)

    async def test_create_node(self) -> None:
        """Test create_node function."""
        import pydantic

        class TestNode(pydantic.BaseModel):
            id: str
            name: str

        test_node = TestNode(id='123', name='Test')

        with mock.patch('imbi_common.age.query') as mock_query:
            mock_query.return_value = [{'node': {'id': '123', 'name': 'Test'}}]
            result = await age.create_node(test_node)

            # Verify result is the validated model
            mock_query.assert_called_once()
            self.assertIsInstance(result, TestNode)
            self.assertEqual(result.id, '123')
            self.assertEqual(result.name, 'Test')

    async def test_create_relationship_with_type(self) -> None:
        """Test create_relationship with relationship type string."""
        import pydantic

        class FromNode(pydantic.BaseModel):
            id: str

        class ToNode(pydantic.BaseModel):
            id: str

        from_node = FromNode(id='1')
        to_node = ToNode(id='2')

        with mock.patch('imbi_common.age.query') as mock_query:
            mock_query.return_value = [{'r': {}}]
            result = await age.create_relationship(
                from_node, to_node, rel_type='KNOWS'
            )

            # Verify query was called correctly
            mock_query.assert_called_once()
            query_text = mock_query.call_args[0][0]
            self.assertIn('KNOWS', query_text)
            self.assertIn('CREATE', query_text)
            self.assertEqual(result, {})

    async def test_retrieve_relationship_edges(self) -> None:
        """Test retrieve_relationship_edges function."""
        from typing import NamedTuple

        import pydantic

        class FriendNode(pydantic.BaseModel):
            id: str
            name: str

        class FriendshipProps(pydantic.BaseModel):
            since: str

        class FriendEdge(NamedTuple):
            node: FriendNode
            properties: FriendshipProps

        class TestNode(pydantic.BaseModel):
            id: str

        test_node = TestNode(id='123')

        with mock.patch('imbi_common.age.query') as mock_query:
            mock_query.return_value = [
                {
                    'b': {'id': '456', 'name': 'Alice'},
                    'r': {'since': '2020'},
                }
            ]
            result = await age.retrieve_relationship_edges(
                test_node, 'FRIENDS_WITH', 'OUTGOING', FriendEdge
            )

            # Verify results
            mock_query.assert_called_once()
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].node.name, 'Alice')
            self.assertEqual(result[0].properties.since, '2020')
