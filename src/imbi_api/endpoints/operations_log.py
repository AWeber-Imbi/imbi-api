"""Operations log CRUD endpoints.

Writes and the non-project-scoped reads are on the top-level
``operations_log_router``. The project-scoped collection GET lives on
``operations_log_project_router``, which is mounted under
``/organizations/{org_slug}/projects/{project_id}``.

All reads use ``FINAL`` plus ``WHERE is_deleted = 0`` so that the
``ReplacingMergeTree`` dedupes to the latest version and tombstones
are hidden.
"""

from __future__ import annotations

import base64
import datetime

import fastapi

operations_log_router = fastapi.APIRouter(
    prefix='/operations-log', tags=['Operations Log']
)
operations_log_project_router = fastapi.APIRouter(tags=['Operations Log'])

READONLY_PATHS: frozenset[str] = frozenset(
    [
        '/id',
        '/project_id',
        '/occurred_at',
        '/recorded_at',
        '/recorded_by',
        '/_row_version',
        '/row_version',
        '/is_deleted',
    ]
)

DEFAULT_LIMIT: int = 50
MAX_LIMIT: int = 500


def _encode_cursor(occurred_at: datetime.datetime, entry_id: str) -> str:
    """Encode a (timestamp, id) cursor as urlsafe base64."""
    payload = f'{occurred_at.isoformat()}|{entry_id}'.encode()
    return base64.urlsafe_b64encode(payload).rstrip(b'=').decode('ascii')


def _decode_cursor(
    cursor: str,
) -> tuple[datetime.datetime, str] | None:
    """Decode a cursor string. Return None for any malformed input."""
    if not cursor:
        return None
    padding = '=' * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding).decode('utf-8')
    except (ValueError, UnicodeDecodeError):
        return None
    if '|' not in raw:
        return None
    ts_str, _, entry_id = raw.partition('|')
    if not entry_id:
        return None
    try:
        ts = datetime.datetime.fromisoformat(ts_str)
    except ValueError:
        return None
    return ts, entry_id
