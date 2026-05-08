"""Project deployment plugin endpoints.

Pass-through endpoints that resolve the project's ``tab='deployment'``
plugin and call its handler methods.  Phase 1 implements ref / commit
discovery, comparison, and ``deploy`` / ``redeploy`` workflow dispatch.
``promote`` (Phase 2) and the persistence of ``DeploymentEvent`` nodes
on the ``Release -[:DEPLOYED_TO]-> Environment`` edge are forthcoming.

See ``docs/deployments-plan.md`` for the full design.
"""

import logging
import typing

import fastapi
import pydantic
from imbi_common import graph
from imbi_common.plugins.base import (
    Commit,
    CompareResult,
    DeploymentPlugin,
    DeploymentRun,
    PluginContext,
    Ref,
)
from imbi_common.plugins.errors import PluginCredentialsMissing

from imbi_api.auth import permissions
from imbi_api.endpoints._helpers import lookup_project_slugs
from imbi_api.identity.host_integration import attach_identity
from imbi_api.plugins import call_with_timeout
from imbi_api.plugins.credentials import get_plugin_credentials
from imbi_api.plugins.resolution import ResolvedPlugin, resolve_plugin

LOGGER = logging.getLogger(__name__)

project_deployments_router = fastapi.APIRouter(tags=['Project: Deployments'])


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class DeployActionRequest(pydantic.BaseModel):
    """Body for ``POST /deployments`` with ``action='deploy'|'redeploy'``."""

    action: typing.Literal['deploy', 'redeploy']
    environment: str
    committish: str
    ref_label: str | None = None
    inputs: dict[str, str] | None = None


class DeploymentTriggerResponse(pydantic.BaseModel):
    """Response shape for a successful deploy/redeploy/promote action."""

    run: DeploymentRun
    plugin_id: str
    plugin_slug: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_and_context(
    db: graph.Graph,
    org_slug: str,
    project_id: str,
    auth: permissions.AuthContext,
    *,
    source: str | None = None,
    environment: str | None = None,
) -> tuple[ResolvedPlugin, PluginContext, dict[str, str]]:
    """Common boilerplate: resolve plugin, attach identity, build creds."""
    resolved = await resolve_plugin(db, project_id, 'deployment', source)
    project_slug, team_slug = await lookup_project_slugs(db, project_id)
    ctx = PluginContext(
        project_id=project_id,
        project_slug=project_slug,
        org_slug=org_slug,
        team_slug=team_slug,
        environment=environment,
        assignment_options=resolved.options,
    )
    ctx = await attach_identity(db, ctx, resolved, auth)

    if ctx.identity and ctx.identity.access_token:
        credentials: dict[str, str] = {
            'access_token': ctx.identity.access_token,
        }
    else:
        try:
            credentials = await get_plugin_credentials(
                db, resolved.plugin_id, resolved.entry
            )
        except PluginCredentialsMissing as exc:
            raise fastapi.HTTPException(
                status_code=503,
                detail=str(exc),
            ) from exc
        if not credentials.get('access_token') and not credentials.get(
            'token'
        ):
            raise fastapi.HTTPException(
                status_code=503,
                detail=(
                    'No deployment credentials available: bind an '
                    'identity or configure a service-account token.'
                ),
            )
    return resolved, ctx, credentials


def _handler(resolved: ResolvedPlugin) -> DeploymentPlugin:
    """Instantiate and type-narrow the plugin handler."""
    return typing.cast(DeploymentPlugin, resolved.entry.handler_cls())


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@project_deployments_router.get('/refs')
async def list_refs(
    org_slug: str,
    project_id: str,
    db: graph.Pool,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('project:deployment:read'),
        ),
    ],
    kind: typing.Literal['default', 'branch', 'tag', 'all'] = 'all',
    q: str | None = None,
    source: str | None = None,
) -> list[Ref]:
    """List branches, tags, or the default ref for the project's repo."""
    resolved, ctx, credentials = await _resolve_and_context(
        db, org_slug, project_id, auth, source=source
    )
    handler = _handler(resolved)
    return await call_with_timeout(
        handler.list_refs(ctx, credentials, kind=kind, query=q)
    )


@project_deployments_router.get('/refs/{ref:path}/commits')
async def list_commits(
    org_slug: str,
    project_id: str,
    ref: str,
    db: graph.Pool,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('project:deployment:read'),
        ),
    ],
    limit: int = 25,
    source: str | None = None,
) -> list[Commit]:
    """List recent commits on a branch / tag / SHA."""
    resolved, ctx, credentials = await _resolve_and_context(
        db, org_slug, project_id, auth, source=source
    )
    handler = _handler(resolved)
    return await call_with_timeout(
        handler.list_commits(ctx, credentials, ref=ref, limit=limit)
    )


@project_deployments_router.get('/commits/{committish}')
async def resolve_commit(
    org_slug: str,
    project_id: str,
    committish: str,
    db: graph.Pool,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('project:deployment:read'),
        ),
    ],
    source: str | None = None,
) -> Commit:
    """Resolve a SHA / branch / tag / ``refs/pull/N/head``."""
    resolved, ctx, credentials = await _resolve_and_context(
        db, org_slug, project_id, auth, source=source
    )
    handler = _handler(resolved)
    return await call_with_timeout(
        handler.resolve_committish(ctx, credentials, committish)
    )


@project_deployments_router.get('/compare')
async def compare_refs(
    org_slug: str,
    project_id: str,
    db: graph.Pool,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('project:deployment:read'),
        ),
    ],
    base: str = fastapi.Query(...),
    head: str = fastapi.Query(...),
    source: str | None = None,
) -> CompareResult:
    """Compare two refs (``base..head``)."""
    resolved, ctx, credentials = await _resolve_and_context(
        db, org_slug, project_id, auth, source=source
    )
    handler = _handler(resolved)
    return await call_with_timeout(
        handler.compare(ctx, credentials, base=base, head=head)
    )


@project_deployments_router.post('', status_code=202)
async def trigger_deployment(
    org_slug: str,
    project_id: str,
    body: DeployActionRequest,
    db: graph.Pool,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('project:deployment:write'),
        ),
    ],
    source: str | None = None,
) -> DeploymentTriggerResponse:
    """Trigger a deploy or redeploy.

    Persistence of ``DeploymentEvent`` on the ``DEPLOYED_TO`` edge is a
    follow-on; this first cut returns the run reference straight from
    the plugin so the UI can surface a "View workflow run" link.
    """
    resolved, ctx, credentials = await _resolve_and_context(
        db,
        org_slug,
        project_id,
        auth,
        source=source,
        environment=body.environment,
    )
    handler = _handler(resolved)
    run = await call_with_timeout(
        handler.trigger_deployment(
            ctx,
            credentials,
            ref_or_sha=body.committish,
            inputs=body.inputs,
        )
    )
    LOGGER.info(
        'Deployment triggered: project=%s env=%s ref=%s plugin=%s '
        'action=%s actor=%s run_id=%s',
        project_id,
        body.environment,
        body.committish,
        resolved.plugin_slug,
        body.action,
        ctx.actor_user_id,
        run.run_id,
    )
    return DeploymentTriggerResponse(
        run=run,
        plugin_id=resolved.plugin_id,
        plugin_slug=resolved.plugin_slug,
    )
