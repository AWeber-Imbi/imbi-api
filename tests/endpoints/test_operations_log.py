"""Tests for operations log endpoints."""

import datetime
import unittest

from imbi_api.endpoints import operations_log


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
