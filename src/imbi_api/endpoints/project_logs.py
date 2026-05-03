"""Project logs plugin endpoints."""

import datetime
import logging
import typing

import fastapi
from imbi_common import graph
from imbi_common.plugins.base import (  # type: ignore[import-not-found]
    LogFilter,
    LogQuery,
    PluginContext,
)
from imbi_common.plugins.errors import (  # type: ignore[import-not-found]
    CursorExpiredError,
    PluginCredentialsMissing,
)

from imbi_api.auth import permissions
from imbi_api.domain import models
from imbi_api.plugins import call_with_timeout
from imbi_api.plugins.credentials import get_plugin_credentials
from imbi_api.plugins.resolution import resolve_plugin

LOGGER = logging.getLogger(__name__)

project_logs_router = fastapi.APIRouter(tags=['Project: Logs'])

_VALID_FILTER_OPS = frozenset({'eq', 'ne', 'contains', 'starts_with', 'regex'})


def _parse_filters(raw: list[str]) -> list[LogFilter]:
    """Parse ``?filter=field:op:value`` query strings."""
    filters: list[LogFilter] = []
    for item in raw:
        parts = item.split(':', 2)
        if len(parts) != 3:
            raise fastapi.HTTPException(
                status_code=400,
                detail=(
                    f'Invalid filter format {item!r}; expected field:op:value'
                ),
            )
        field, op, value = parts
        if op not in _VALID_FILTER_OPS:
            raise fastapi.HTTPException(
                status_code=400,
                detail=(
                    f'Unknown filter op {op!r};'
                    f' allowed: {sorted(_VALID_FILTER_OPS)}'
                ),
            )
        filters.append(LogFilter(field=field, op=op, value=value))  # type: ignore[arg-type]
    return filters


@project_logs_router.get('/')
async def search_logs(
    org_slug: str,
    project_id: str,
    db: graph.Pool,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('project:logs:read'),
        ),
    ],
    source: str | None = fastapi.Query(default=None),
    environment: str | None = fastapi.Query(default=None),
    start_time: str | None = fastapi.Query(default=None),
    end_time: str | None = fastapi.Query(default=None),
    cursor: str | None = fastapi.Query(default=None),
    limit: int = fastapi.Query(default=100, ge=1, le=1000),
    filter: list[str] = fastapi.Query(default_factory=list),  # noqa: B008
) -> models.LogResultResponse:
    """Search project logs via the assigned logs plugin."""
    resolved = await resolve_plugin(db, project_id, 'logs', source)

    now = datetime.datetime.now(datetime.UTC)
    try:
        start_dt = (
            datetime.datetime.fromisoformat(start_time)
            if start_time
            else now - datetime.timedelta(minutes=30)
        )
        end_dt = datetime.datetime.fromisoformat(end_time) if end_time else now
    except ValueError as exc:
        raise fastapi.HTTPException(
            status_code=400,
            detail=f'Invalid datetime format: {exc}',
        ) from exc

    filters = _parse_filters(filter)
    query = LogQuery(
        start_time=start_dt,
        end_time=end_dt,
        filters=filters,
        limit=limit,
        cursor=cursor,
    )

    ctx = PluginContext(
        project_id=project_id,
        project_slug='',
        org_slug=org_slug,
        environment=environment,
        assignment_options=resolved.options,
    )
    try:
        credentials = await get_plugin_credentials(
            db, resolved.plugin_id, resolved.entry
        )
    except PluginCredentialsMissing as exc:
        raise fastapi.HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    handler = resolved.entry.handler_cls()
    try:
        result = await call_with_timeout(
            handler.search(ctx, credentials, query)
        )
    except CursorExpiredError as exc:
        raise fastapi.HTTPException(
            status_code=409,
            detail={
                'error': 'cursor_expired',
                'message': str(exc),
            },
        ) from exc

    return models.LogResultResponse(
        entries=[
            models.LogEntryResponse(
                timestamp=e.timestamp,
                message=e.message,
                level=e.level,
                raw=e.raw,
            )
            for e in result.entries
        ],
        next_cursor=result.next_cursor,
        total=result.total,
    )


@project_logs_router.get('/schema')
async def get_log_schema(
    org_slug: str,
    project_id: str,
    db: graph.Pool,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('project:logs:read'),
        ),
    ],
    source: str | None = fastapi.Query(default=None),
    environment: str | None = fastapi.Query(default=None),
) -> list[dict[str, typing.Any]]:
    """Get the log schema (available fields) for the assigned logs plugin."""
    resolved = await resolve_plugin(db, project_id, 'logs', source)
    ctx = PluginContext(
        project_id=project_id,
        project_slug='',
        org_slug=org_slug,
        environment=environment,
        assignment_options=resolved.options,
    )
    try:
        credentials = await get_plugin_credentials(
            db, resolved.plugin_id, resolved.entry
        )
    except PluginCredentialsMissing as exc:
        raise fastapi.HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    handler = resolved.entry.handler_cls()
    return await call_with_timeout(handler.schema(ctx, credentials))
