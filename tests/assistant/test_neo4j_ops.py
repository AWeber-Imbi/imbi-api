"""Tests for assistant neo4j_ops module."""

import datetime
import json
import unittest
from unittest import mock

from imbi_api.assistant import models, neo4j_ops


class CreateConversationTestCase(unittest.IsolatedAsyncioTestCase):
    """Test cases for create_conversation."""

    async def test_create_conversation(self) -> None:
        """Test creating a new conversation."""
        with mock.patch('imbi_common.neo4j.run') as mock_run:
            mock_ctx = mock.AsyncMock()
            mock_ctx.__aenter__.return_value = mock_ctx
            mock_ctx.__aexit__.return_value = None
            mock_ctx.consume.return_value = None
            mock_run.return_value = mock_ctx

            conv = await neo4j_ops.create_conversation(
                user_email='test@example.com',
                model='claude-sonnet-4-20250514',
            )
            self.assertIsInstance(conv, models.Conversation)
            self.assertEqual(conv.user_email, 'test@example.com')
            self.assertEqual(conv.model, 'claude-sonnet-4-20250514')
            self.assertIsNotNone(conv.id)
            self.assertFalse(conv.is_archived)
            mock_run.assert_called_once()


class GetConversationTestCase(unittest.IsolatedAsyncioTestCase):
    """Test cases for get_conversation."""

    async def test_conversation_found(self) -> None:
        """Test getting an existing conversation."""
        now = datetime.datetime.now(datetime.UTC)
        with (
            mock.patch('imbi_common.neo4j.run') as mock_run,
            mock.patch('imbi_common.neo4j.convert_neo4j_types') as mc,
        ):
            mock_ctx = mock.AsyncMock()
            mock_ctx.__aenter__.return_value = mock_ctx
            mock_ctx.__aexit__.return_value = None
            mock_ctx.data.return_value = [{'c': {'raw': 'data'}}]
            mock_run.return_value = mock_ctx
            mc.return_value = {
                'id': 'conv-123',
                'user_email': 'test@example.com',
                'title': 'Test',
                'created_at': now,
                'updated_at': now,
                'model': 'claude-sonnet-4-20250514',
                'is_archived': False,
            }

            conv = await neo4j_ops.get_conversation(
                'conv-123', 'test@example.com'
            )
            self.assertIsNotNone(conv)
            self.assertEqual(conv.id, 'conv-123')

    async def test_conversation_not_found(self) -> None:
        """Test getting a nonexistent conversation."""
        with mock.patch('imbi_common.neo4j.run') as mock_run:
            mock_ctx = mock.AsyncMock()
            mock_ctx.__aenter__.return_value = mock_ctx
            mock_ctx.__aexit__.return_value = None
            mock_ctx.data.return_value = []
            mock_run.return_value = mock_ctx

            conv = await neo4j_ops.get_conversation(
                'missing', 'test@example.com'
            )
            self.assertIsNone(conv)


class ListConversationsTestCase(unittest.IsolatedAsyncioTestCase):
    """Test cases for list_conversations."""

    async def test_list_empty(self) -> None:
        """Test listing with no conversations."""
        with mock.patch('imbi_common.neo4j.run') as mock_run:
            mock_ctx = mock.AsyncMock()
            mock_ctx.__aenter__.return_value = mock_ctx
            mock_ctx.__aexit__.return_value = None
            mock_ctx.data.return_value = []
            mock_run.return_value = mock_ctx

            convs = await neo4j_ops.list_conversations('test@example.com')
            self.assertEqual(convs, [])

    async def test_list_with_conversations(self) -> None:
        """Test listing conversations."""
        now = datetime.datetime.now(datetime.UTC)
        with (
            mock.patch('imbi_common.neo4j.run') as mock_run,
            mock.patch('imbi_common.neo4j.convert_neo4j_types') as mc,
        ):
            mock_ctx = mock.AsyncMock()
            mock_ctx.__aenter__.return_value = mock_ctx
            mock_ctx.__aexit__.return_value = None
            mock_ctx.data.return_value = [
                {'c': {'raw': 'data1'}},
                {'c': {'raw': 'data2'}},
            ]
            mock_run.return_value = mock_ctx
            mc.side_effect = [
                {
                    'id': 'conv-1',
                    'user_email': 'test@example.com',
                    'title': 'First',
                    'created_at': now,
                    'updated_at': now,
                    'model': 'claude-sonnet-4-20250514',
                    'is_archived': False,
                },
                {
                    'id': 'conv-2',
                    'user_email': 'test@example.com',
                    'title': 'Second',
                    'created_at': now,
                    'updated_at': now,
                    'model': 'claude-sonnet-4-20250514',
                    'is_archived': False,
                },
            ]

            convs = await neo4j_ops.list_conversations('test@example.com')
            self.assertEqual(len(convs), 2)

    async def test_include_archived(self) -> None:
        """Test listing with archived conversations."""
        with mock.patch('imbi_common.neo4j.run') as mock_run:
            mock_ctx = mock.AsyncMock()
            mock_ctx.__aenter__.return_value = mock_ctx
            mock_ctx.__aexit__.return_value = None
            mock_ctx.data.return_value = []
            mock_run.return_value = mock_ctx

            await neo4j_ops.list_conversations(
                'test@example.com', include_archived=True
            )
            # Check the query does not filter archived
            call_args = mock_run.call_args
            query = call_args[0][0]
            self.assertNotIn('is_archived = false', query)


class AddMessageTestCase(unittest.IsolatedAsyncioTestCase):
    """Test cases for add_message."""

    async def test_add_user_message(self) -> None:
        """Test adding a user message."""
        with mock.patch('imbi_common.neo4j.run') as mock_run:
            mock_ctx = mock.AsyncMock()
            mock_ctx.__aenter__.return_value = mock_ctx
            mock_ctx.__aexit__.return_value = None
            mock_ctx.data.return_value = [{'sequence': 0}]
            mock_run.return_value = mock_ctx

            msg = await neo4j_ops.add_message(
                conversation_id='conv-123',
                role='user',
                content='Hello',
            )
            self.assertIsInstance(msg, models.Message)
            self.assertEqual(msg.role, 'user')
            self.assertEqual(msg.content, 'Hello')
            self.assertEqual(msg.sequence, 0)

    async def test_add_assistant_message_with_tools(self) -> None:
        """Test adding an assistant message with tool data."""
        tool_use = [{'id': 't1', 'name': 'list_projects'}]
        tool_results = [{'tool_use_id': 't1', 'content': 'ok'}]
        token_usage = {'input_tokens': 100, 'output_tokens': 50}

        with mock.patch('imbi_common.neo4j.run') as mock_run:
            mock_ctx = mock.AsyncMock()
            mock_ctx.__aenter__.return_value = mock_ctx
            mock_ctx.__aexit__.return_value = None
            mock_ctx.data.return_value = [{'sequence': 1}]
            mock_run.return_value = mock_ctx

            msg = await neo4j_ops.add_message(
                conversation_id='conv-123',
                role='assistant',
                content='Here are the projects.',
                tool_use=tool_use,
                tool_results=tool_results,
                token_usage=token_usage,
            )
            self.assertEqual(msg.role, 'assistant')
            self.assertEqual(msg.tool_use, tool_use)
            self.assertEqual(msg.token_usage, token_usage)

            # Verify JSON serialization was used for neo4j params
            call_kwargs = mock_run.call_args[1]
            self.assertEqual(call_kwargs['tool_use'], json.dumps(tool_use))

    async def test_add_message_missing_conversation(self) -> None:
        """Test add_message raises ValueError for missing conversation."""
        with mock.patch('imbi_common.neo4j.run') as mock_run:
            mock_ctx = mock.AsyncMock()
            mock_ctx.__aenter__.return_value = mock_ctx
            mock_ctx.__aexit__.return_value = None
            mock_ctx.data.return_value = []
            mock_run.return_value = mock_ctx

            with self.assertRaises(ValueError) as ctx:
                await neo4j_ops.add_message(
                    conversation_id='conv-123',
                    role='user',
                    content='Hello',
                )
            self.assertIn('not found', str(ctx.exception))


class GetMessagesTestCase(unittest.IsolatedAsyncioTestCase):
    """Test cases for get_messages."""

    async def test_get_empty_messages(self) -> None:
        """Test getting messages from empty conversation."""
        with mock.patch('imbi_common.neo4j.run') as mock_run:
            mock_ctx = mock.AsyncMock()
            mock_ctx.__aenter__.return_value = mock_ctx
            mock_ctx.__aexit__.return_value = None
            mock_ctx.data.return_value = []
            mock_run.return_value = mock_ctx

            msgs = await neo4j_ops.get_messages('conv-123')
            self.assertEqual(msgs, [])

    async def test_get_messages_with_json_fields(self) -> None:
        """Test getting messages that have JSON string fields."""
        now = datetime.datetime.now(datetime.UTC)
        with (
            mock.patch('imbi_common.neo4j.run') as mock_run,
            mock.patch('imbi_common.neo4j.convert_neo4j_types') as mc,
        ):
            mock_ctx = mock.AsyncMock()
            mock_ctx.__aenter__.return_value = mock_ctx
            mock_ctx.__aexit__.return_value = None
            mock_ctx.data.return_value = [{'m': {'raw': 'data'}}]
            mock_run.return_value = mock_ctx
            mc.return_value = {
                'id': 'msg-1',
                'conversation_id': 'conv-123',
                'role': 'assistant',
                'content': 'Result',
                'tool_use': json.dumps([{'id': 't1'}]),
                'tool_results': json.dumps([{'content': 'ok'}]),
                'token_usage': json.dumps(
                    {
                        'input_tokens': 10,
                        'output_tokens': 20,
                    }
                ),
                'created_at': now,
                'sequence': 0,
            }

            msgs = await neo4j_ops.get_messages('conv-123')
            self.assertEqual(len(msgs), 1)
            msg = msgs[0]
            # JSON strings should be deserialized
            self.assertIsInstance(msg.tool_use, list)
            self.assertIsInstance(msg.token_usage, dict)


class CountMessagesTestCase(unittest.IsolatedAsyncioTestCase):
    """Test cases for count_messages."""

    async def test_count_messages(self) -> None:
        """Test counting messages."""
        with mock.patch('imbi_common.neo4j.run') as mock_run:
            mock_ctx = mock.AsyncMock()
            mock_ctx.__aenter__.return_value = mock_ctx
            mock_ctx.__aexit__.return_value = None
            mock_ctx.data.return_value = [{'count': 5}]
            mock_run.return_value = mock_ctx

            count = await neo4j_ops.count_messages('conv-123')
            self.assertEqual(count, 5)

    async def test_count_messages_empty(self) -> None:
        """Test counting messages when no records."""
        with mock.patch('imbi_common.neo4j.run') as mock_run:
            mock_ctx = mock.AsyncMock()
            mock_ctx.__aenter__.return_value = mock_ctx
            mock_ctx.__aexit__.return_value = None
            mock_ctx.data.return_value = []
            mock_run.return_value = mock_ctx

            count = await neo4j_ops.count_messages('conv-123')
            self.assertEqual(count, 0)


class UpdateConversationTitleTestCase(unittest.IsolatedAsyncioTestCase):
    """Test cases for update_conversation_title."""

    async def test_update_title_success(self) -> None:
        """Test updating a conversation title."""
        with mock.patch('imbi_common.neo4j.run') as mock_run:
            mock_ctx = mock.AsyncMock()
            mock_ctx.__aenter__.return_value = mock_ctx
            mock_ctx.__aexit__.return_value = None
            mock_ctx.data.return_value = [{'id': 'conv-123'}]
            mock_run.return_value = mock_ctx

            result = await neo4j_ops.update_conversation_title(
                'conv-123', 'test@example.com', 'New Title'
            )
            self.assertTrue(result)

    async def test_update_title_not_found(self) -> None:
        """Test updating title for nonexistent conversation."""
        with mock.patch('imbi_common.neo4j.run') as mock_run:
            mock_ctx = mock.AsyncMock()
            mock_ctx.__aenter__.return_value = mock_ctx
            mock_ctx.__aexit__.return_value = None
            mock_ctx.data.return_value = []
            mock_run.return_value = mock_ctx

            result = await neo4j_ops.update_conversation_title(
                'missing', 'test@example.com', 'New Title'
            )
            self.assertFalse(result)


class ArchiveConversationTestCase(unittest.IsolatedAsyncioTestCase):
    """Test cases for archive_conversation."""

    async def test_archive_success(self) -> None:
        """Test archiving a conversation."""
        with mock.patch('imbi_common.neo4j.run') as mock_run:
            mock_ctx = mock.AsyncMock()
            mock_ctx.__aenter__.return_value = mock_ctx
            mock_ctx.__aexit__.return_value = None
            mock_ctx.data.return_value = [{'id': 'conv-123'}]
            mock_run.return_value = mock_ctx

            result = await neo4j_ops.archive_conversation(
                'conv-123', 'test@example.com'
            )
            self.assertTrue(result)

    async def test_archive_not_found(self) -> None:
        """Test archiving nonexistent conversation."""
        with mock.patch('imbi_common.neo4j.run') as mock_run:
            mock_ctx = mock.AsyncMock()
            mock_ctx.__aenter__.return_value = mock_ctx
            mock_ctx.__aexit__.return_value = None
            mock_ctx.data.return_value = []
            mock_run.return_value = mock_ctx

            result = await neo4j_ops.archive_conversation(
                'missing', 'test@example.com'
            )
            self.assertFalse(result)


class DeleteConversationTestCase(unittest.IsolatedAsyncioTestCase):
    """Test cases for delete_conversation."""

    async def test_delete_success(self) -> None:
        """Test deleting a conversation."""
        with mock.patch('imbi_common.neo4j.run') as mock_run:
            mock_ctx = mock.AsyncMock()
            mock_ctx.__aenter__.return_value = mock_ctx
            mock_ctx.__aexit__.return_value = None
            mock_ctx.data.return_value = [{'deleted': 1}]
            mock_run.return_value = mock_ctx

            result = await neo4j_ops.delete_conversation(
                'conv-123', 'test@example.com'
            )
            self.assertTrue(result)

    async def test_delete_not_found(self) -> None:
        """Test deleting nonexistent conversation."""
        with mock.patch('imbi_common.neo4j.run') as mock_run:
            mock_ctx = mock.AsyncMock()
            mock_ctx.__aenter__.return_value = mock_ctx
            mock_ctx.__aexit__.return_value = None
            mock_ctx.data.return_value = [{'deleted': 0}]
            mock_run.return_value = mock_ctx

            result = await neo4j_ops.delete_conversation(
                'missing', 'test@example.com'
            )
            self.assertFalse(result)

    async def test_delete_empty_records(self) -> None:
        """Test delete with no records returned."""
        with mock.patch('imbi_common.neo4j.run') as mock_run:
            mock_ctx = mock.AsyncMock()
            mock_ctx.__aenter__.return_value = mock_ctx
            mock_ctx.__aexit__.return_value = None
            mock_ctx.data.return_value = []
            mock_run.return_value = mock_ctx

            result = await neo4j_ops.delete_conversation(
                'missing', 'test@example.com'
            )
            self.assertFalse(result)
