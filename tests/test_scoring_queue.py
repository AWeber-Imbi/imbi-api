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
