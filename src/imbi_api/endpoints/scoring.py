"""Project score history, rollup, and rescore endpoints."""

from __future__ import annotations

import asyncio
import datetime
import logging
import typing

import fastapi
from imbi_common import clickhouse, graph

from imbi_api.auth import permissions
from imbi_api.domain import scoring as scoring_models
from imbi_api.endpoints.scoring_policies import _load_policy
from imbi_api.scoring import OptionalValkeyClient
from imbi_api.scoring import queue as score_queue

LOGGER = logging.getLogger(__name__)

scoring_router = fastapi.APIRouter(tags=['Scoring'])


_GRANULARITY_EXPR = {
    'raw': 'timestamp',
    'hour': 'toStartOfHour(timestamp)',
    'day': 'toStartOfDay(timestamp)',
}


@scoring_router.get(
    '/organizations/{org_slug}/projects/{project_id}/score/history'
)
async def get_score_history(
    org_slug: str,
    project_id: str,
    db: graph.Pool,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(permissions.require_permission('project:read')),
    ],
    granularity: typing.Literal['raw', 'hour', 'day'] = 'raw',
    from_: typing.Annotated[
        datetime.datetime | None, fastapi.Query(alias='from')
    ] = None,
    to: datetime.datetime | None = None,
) -> scoring_models.ScoreHistoryResponse:
    exists = await db.execute(
        'MATCH (p:Project {{id: {id}}})'
        '-[:OWNED_BY]->(:Team)'
        '-[:BELONGS_TO]->(:Organization {{slug: {org}}})'
        ' RETURN p.id AS id',
        {'id': project_id, 'org': org_slug},
        ['id'],
    )
    if not exists:
        raise fastapi.HTTPException(
            status_code=404,
            detail=f'Project {project_id!r} not found',
        )
    bucket = _GRANULARITY_EXPR[granularity]
    where: list[str] = ['project = {project_id:String}']
    params: dict[str, typing.Any] = {'project_id': project_id}
    if from_ is not None:
        where.append('timestamp >= {from_ts:DateTime64(3)}')
        params['from_ts'] = from_
    if to is not None:
        where.append('timestamp <= {to_ts:DateTime64(3)}')
        params['to_ts'] = to
    where_sql = ' AND '.join(where)
    if granularity == 'raw':
        sql = (
            'SELECT timestamp, score, previous_score, change_reason'  # noqa: S608
            ' FROM score_history WHERE ' + where_sql + ' ORDER BY timestamp'
        )
    else:
        sql = (
            f'SELECT {bucket} AS ts, argMax(score, timestamp) AS score'  # noqa: S608
            ' FROM score_history WHERE '
            + where_sql
            + f' GROUP BY {bucket} ORDER BY ts'
        )
    rows = await clickhouse.query(sql, params)
    points: list[scoring_models.ScoreHistoryPoint] = []
    for row in rows:
        if granularity == 'raw':
            points.append(
                scoring_models.ScoreHistoryPoint(
                    timestamp=str(row['timestamp']),
                    score=float(row['score']),
                    previous_score=float(row['previous_score']),
                    change_reason=str(row.get('change_reason') or ''),
                )
            )
        else:
            points.append(
                scoring_models.ScoreHistoryPoint(
                    timestamp=str(row['ts']),
                    score=float(row['score']),
                )
            )
    return scoring_models.ScoreHistoryResponse(
        project_id=project_id,
        granularity=granularity,
        points=points,
    )


@scoring_router.get('/scores/rollup')
async def score_rollup(
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(permissions.require_permission('project:read')),
    ],
    dimension: typing.Literal['team', 'project_type', 'organization'] = 'team',
) -> list[scoring_models.ScoreRollupRow]:
    column = {
        'team': 'team',
        'project_type': 'project_type',
        'organization': 'organization',
    }[dimension]
    sql = (
        f'SELECT {column} AS key,'  # noqa: S608
        ' argMaxMerge(latest_score) AS latest_score,'
        ' avgMerge(avg_score) AS avg_score,'
        ' maxMerge(last_updated) AS last_updated'
        ' FROM score_latest'
        f' GROUP BY {column}'
        f' ORDER BY {column}'
    )
    rows = await clickhouse.query(sql)
    out: list[scoring_models.ScoreRollupRow] = []
    for row in rows:
        out.append(
            scoring_models.ScoreRollupRow(
                dimension=dimension,
                key=str(row.get('key') or ''),
                latest_score=float(row.get('latest_score') or 0.0),
                avg_score=float(row.get('avg_score') or 0.0),
                last_updated=(
                    str(row['last_updated'])
                    if row.get('last_updated')
                    else None
                ),
            )
        )
    return out


@scoring_router.post('/scoring/rescore')
async def rescore(
    db: graph.Pool,
    valkey_client: OptionalValkeyClient,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('scoring_policy:rescore_all')
        ),
    ],
    body: scoring_models.RescoreRequest | None = None,
) -> scoring_models.RescoreResponse:
    body = body or scoring_models.RescoreRequest()
    project_ids: list[str] = []
    if body.policy_slug:
        policy = await _load_policy(db, body.policy_slug)
        if policy is None:
            raise fastapi.HTTPException(
                status_code=404,
                detail=f'Policy {body.policy_slug!r} not found',
            )
        project_ids = await score_queue.affected_projects(db, policy)
    elif body.blueprint_slug:
        rows = await db.execute(
            'MATCH (b:Blueprint {{slug: {slug}}})'
            ' WITH b.filter AS filt'
            ' MATCH (p:Project)'
            ' RETURN p.id AS id',
            {'slug': body.blueprint_slug},
            ['id'],
        )
        project_ids = [v for r in rows if (v := graph.parse_agtype(r['id']))]
    else:
        project_ids = await score_queue.all_project_ids(
            db, body.project_type_slug
        )
    requested_by = auth.user.email if auth.user else auth.principal_name
    results = await asyncio.gather(
        *[
            score_queue.enqueue_recompute(
                valkey_client, pid, 'bulk_rescore', requested_by=requested_by
            )
            for pid in project_ids
        ]
    )
    return scoring_models.RescoreResponse(enqueued=sum(results))
