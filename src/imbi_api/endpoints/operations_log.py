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
import logging
import typing

import fastapi
import nanoid
import pydantic
from imbi_common import clickhouse, models

from imbi_api.auth import permissions

LOGGER = logging.getLogger(__name__)

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


def _row_to_response(row: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """Strip internal bookkeeping columns before returning to the client."""
    return {
        k: v
        for k, v in row.items()
        if k not in {'_row_version', 'row_version', 'is_deleted'}
    }


def _model_to_row(entry: models.OperationLog) -> dict[str, typing.Any]:
    """Build the ClickHouse row dict from a validated model.

    Uses by_alias=True so the ``_row_version`` column name is emitted
    instead of the Python field name ``row_version``. ``is_deleted`` is
    coerced to UInt8 (0/1) for the ClickHouse UInt8 column.
    """
    dumped = entry.model_dump(by_alias=True, mode='python')
    dumped['is_deleted'] = 1 if entry.is_deleted else 0
    return dumped


async def _insert_row(row: dict[str, typing.Any]) -> None:
    """Insert a single row into operations_log.

    Calls the class method directly with explicit column names/values
    because the module-level ``clickhouse.insert`` wrapper only accepts
    pydantic models and loses the alias during serialization.
    """
    await clickhouse.client.Clickhouse.get_instance().insert(
        'operations_log',
        [list(row.values())],
        list(row.keys()),
    )


@operations_log_router.post('/', status_code=201)
async def create_operation_log(
    data: dict[str, typing.Any],
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('operations_log:create'),
        ),
    ],
) -> dict[str, typing.Any]:
    """Create a new operations log entry."""
    payload = dict(data)
    for server_field in (
        'id',
        'recorded_at',
        'recorded_by',
        '_row_version',
        'row_version',
        'is_deleted',
    ):
        payload.pop(server_field, None)

    try:
        entry = models.OperationLog(
            id=nanoid.generate(),
            recorded_at=datetime.datetime.now(datetime.UTC),
            recorded_by=auth.principal_name,
            **payload,
        )
    except pydantic.ValidationError as e:
        LOGGER.warning('Validation error creating opslog entry: %s', e)
        raise fastapi.HTTPException(
            status_code=400,
            detail=f'Validation error: {e.errors()}',
        ) from e

    if entry.performed_by is None:
        entry.performed_by = entry.recorded_by

    await _insert_row(_model_to_row(entry))
    return _row_to_response(entry.model_dump(mode='json'))
