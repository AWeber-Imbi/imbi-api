"""Tests for the scoring recompute queue."""

from __future__ import annotations

import unittest
from unittest import mock

from imbi_api.scoring import queue as score_queue


class EnqueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_enqueue_xadds_when_debounce_acquired(self) -> None:
        client = mock.AsyncMock()
        client.set = mock.AsyncMock(return_value=True)
        client.xadd = mock.AsyncMock()
        result = await score_queue.enqueue_recompute(
            client, 'p1', 'attribute_change'
        )
        self.assertTrue(result)
        client.set.assert_awaited_once()
        client.xadd.assert_awaited_once()
        args, _kwargs = client.xadd.await_args
        self.assertEqual(args[0], score_queue.STREAM)
        self.assertEqual(args[1]['project_id'], 'p1')
        self.assertEqual(args[1]['reason'], 'attribute_change')

    async def test_enqueue_skips_when_debounced(self) -> None:
        client = mock.AsyncMock()
        client.set = mock.AsyncMock(return_value=None)
        client.xadd = mock.AsyncMock()
        result = await score_queue.enqueue_recompute(
            client, 'p1', 'attribute_change'
        )
        self.assertFalse(result)
        client.xadd.assert_not_called()


class ConsumerTests(unittest.IsolatedAsyncioTestCase):
    async def test_xack_on_success(self) -> None:
        client = mock.AsyncMock()
        client.xack = mock.AsyncMock()
        with mock.patch.object(
            score_queue, '_process_message', mock.AsyncMock()
        ):
            await score_queue._handle_entries(
                client,
                [(b'1-0', {b'project_id': b'p1'})],
                mock.AsyncMock(),
                mock.AsyncMock(),
            )
        client.xack.assert_awaited_once()

    async def test_no_xack_on_exception(self) -> None:
        client = mock.AsyncMock()
        client.xack = mock.AsyncMock()
        with mock.patch.object(
            score_queue,
            '_process_message',
            mock.AsyncMock(side_effect=RuntimeError('boom')),
        ):
            await score_queue._handle_entries(
                client,
                [(b'1-0', {b'project_id': b'p1'})],
                mock.AsyncMock(),
                mock.AsyncMock(),
            )
        client.xack.assert_not_called()

    async def test_dlq_after_max_deliveries(self) -> None:
        client = mock.AsyncMock()
        client.xpending_range = mock.AsyncMock(
            return_value=[{'times_delivered': 6}]
        )
        client.xadd = mock.AsyncMock()
        client.xack = mock.AsyncMock()
        result = await score_queue._maybe_dead_letter(
            client, b'1-0', {'project_id': 'p1'}
        )
        self.assertTrue(result)
        client.xadd.assert_awaited_once()
        client.xack.assert_awaited_once()

    async def test_dlq_skipped_below_threshold(self) -> None:
        client = mock.AsyncMock()
        client.xpending_range = mock.AsyncMock(
            return_value=[{'times_delivered': 1}]
        )
        client.xadd = mock.AsyncMock()
        client.xack = mock.AsyncMock()
        result = await score_queue._maybe_dead_letter(
            client, b'1-0', {'project_id': 'p1'}
        )
        self.assertFalse(result)

    async def test_dlq_skipped_when_empty_pending(self) -> None:
        client = mock.AsyncMock()
        client.xpending_range = mock.AsyncMock(return_value=[])
        result = await score_queue._maybe_dead_letter(
            client, b'1-0', {'project_id': 'p1'}
        )
        self.assertFalse(result)

    async def test_dlq_xpending_exception_returns_false(self) -> None:
        client = mock.AsyncMock()
        client.xpending_range = mock.AsyncMock(
            side_effect=RuntimeError('nope')
        )
        result = await score_queue._maybe_dead_letter(
            client, b'1-0', {'project_id': 'p1'}
        )
        self.assertFalse(result)

    async def test_dlq_tuple_entry_path(self) -> None:
        """Cover the tuple-based entry format from xpending_range."""
        client = mock.AsyncMock()
        # Simulate a tuple-based response: [msg_id, consumer, idle, deliveries]
        client.xpending_range = mock.AsyncMock(
            return_value=[(b'1-0', b'worker-0', 70000, 6)]
        )
        client.xadd = mock.AsyncMock()
        client.xack = mock.AsyncMock()
        result = await score_queue._maybe_dead_letter(
            client, b'1-0', {'project_id': 'p1'}
        )
        self.assertTrue(result)


class EnqueueNoneClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_none_client_returns_false(self) -> None:
        result = await score_queue.enqueue_recompute(
            None, 'p1', 'bulk_rescore'
        )
        self.assertFalse(result)

    async def test_enqueue_exception_returns_false(self) -> None:
        client = mock.AsyncMock()
        client.set = mock.AsyncMock(side_effect=RuntimeError('conn error'))
        result = await score_queue.enqueue_recompute(
            client, 'p1', 'attribute_change'
        )
        self.assertFalse(result)


class EnsureGroupTests(unittest.IsolatedAsyncioTestCase):
    async def test_busygroup_is_ignored(self) -> None:
        client = mock.AsyncMock()
        client.xgroup_create = mock.AsyncMock(
            side_effect=Exception('BUSYGROUP Consumer Group already exists')
        )
        await score_queue.ensure_group(client)  # should not raise

    async def test_other_error_is_logged(self) -> None:
        client = mock.AsyncMock()
        client.xgroup_create = mock.AsyncMock(
            side_effect=Exception('some other error')
        )
        with self.assertLogs('imbi_api.scoring.queue', level='WARNING'):
            await score_queue.ensure_group(client)


class ClaimStaleTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_entries_from_xautoclaim(self) -> None:
        client = mock.AsyncMock()
        entries = [(b'1-0', {b'project_id': b'p1'})]
        client.xautoclaim = mock.AsyncMock(return_value=['0-0', entries, []])
        result = await score_queue._claim_stale(client, 'worker-0')
        self.assertEqual(result, entries)

    async def test_returns_empty_on_exception(self) -> None:
        client = mock.AsyncMock()
        client.xautoclaim = mock.AsyncMock(side_effect=Exception('fail'))
        result = await score_queue._claim_stale(client, 'worker-0')
        self.assertEqual(result, [])

    async def test_returns_empty_when_result_malformed(self) -> None:
        client = mock.AsyncMock()
        client.xautoclaim = mock.AsyncMock(return_value=None)
        result = await score_queue._claim_stale(client, 'worker-0')
        self.assertEqual(result, [])

    async def test_returns_empty_when_msgs_not_list(self) -> None:
        client = mock.AsyncMock()
        # result[1] is not a list
        client.xautoclaim = mock.AsyncMock(return_value=['0-0', None, []])
        result = await score_queue._claim_stale(client, 'worker-0')
        self.assertEqual(result, [])


class HandleEntriesWithDlqTest(unittest.IsolatedAsyncioTestCase):
    async def test_dead_letter_prevents_processing(self) -> None:
        client = mock.AsyncMock()
        process = mock.AsyncMock()
        with mock.patch.object(
            score_queue,
            '_maybe_dead_letter',
            mock.AsyncMock(return_value=True),
        ):
            with mock.patch.object(score_queue, '_process_message', process):
                await score_queue._handle_entries(
                    client,
                    [(b'1-0', {b'project_id': b'p1'})],
                    mock.AsyncMock(),
                    mock.AsyncMock(),
                    check_dlq=True,
                )
        process.assert_not_called()


class AllProjectIdsTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_project_ids_no_filter(self) -> None:
        db = mock.AsyncMock()
        db.execute = mock.AsyncMock(return_value=[{'id': 'a1'}, {'id': 'b2'}])
        result = await score_queue.all_project_ids(db)
        self.assertEqual(result, ['a1', 'b2'])

    async def test_all_project_ids_with_type_filter(self) -> None:
        db = mock.AsyncMock()
        db.execute = mock.AsyncMock(return_value=[{'id': 'c3'}])
        result = await score_queue.all_project_ids(db, 'service')
        self.assertEqual(result, ['c3'])


class ProjectsOfTypeTest(unittest.IsolatedAsyncioTestCase):
    async def test_projects_of_type(self) -> None:
        db = mock.AsyncMock()
        db.execute = mock.AsyncMock(return_value=[{'id': 'p1'}, {'id': 'p2'}])
        result = await score_queue.projects_of_type(db, 'service')
        self.assertEqual(result, ['p1', 'p2'])
