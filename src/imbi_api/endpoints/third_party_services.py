"""Third-party service management endpoints."""

import json
import logging
import typing

import fastapi
from imbi_common import neo4j
from neo4j import exceptions

from imbi_api.auth import permissions

LOGGER = logging.getLogger(__name__)


def _serialize_props(props: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """Serialize dict fields to JSON strings for Neo4j storage."""
    result = dict(props)
    for key in ('links', 'identifiers'):
        if key in result and isinstance(result[key], dict):
            result[key] = json.dumps(result[key])
    return result


def _deserialize_service(
    record: dict[str, typing.Any],
) -> dict[str, typing.Any]:
    """Deserialize JSON string fields back to dicts."""
    svc = dict(record)
    for key in ('links', 'identifiers'):
        val = svc.get(key)
        if isinstance(val, str):
            try:
                svc[key] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                svc[key] = {}
        elif val is None:
            svc[key] = {}
    return svc


third_party_services_router = fastapi.APIRouter(
    prefix='/third-party-services',
    tags=['Third-Party Services'],
)


@third_party_services_router.post('/', status_code=201)
async def create_third_party_service(
    data: dict[str, typing.Any],
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission(
                'third_party_service:create',
            ),
        ),
    ],
) -> dict[str, typing.Any]:
    """Create a new third-party service linked to an organization.

    Parameters:
        data: Service data including ``organization_slug`` and
            optionally ``team_slug``.

    Returns:
        The created third-party service.

    Raises:
        400: Invalid data or missing organization_slug
        404: Organization or team not found
        409: Service with slug already exists

    """
    payload = dict(data)
    org_slug = payload.pop('organization_slug', None)
    if not org_slug:
        raise fastapi.HTTPException(
            status_code=400,
            detail='organization_slug is required',
        )
    payload.pop('organization', None)

    team_slug = payload.pop('team_slug', None)
    payload.pop('team', None)

    vendor = payload.get('vendor')
    if not vendor:
        raise fastapi.HTTPException(
            status_code=400,
            detail='vendor is required',
        )

    if not payload.get('name'):
        raise fastapi.HTTPException(
            status_code=400,
            detail='name is required',
        )
    if not payload.get('slug'):
        raise fastapi.HTTPException(
            status_code=400,
            detail='slug is required',
        )

    # Validate status if provided
    valid_statuses = {'active', 'deprecated', 'evaluating', 'inactive'}
    status = payload.get('status', 'active')
    if status not in valid_statuses:
        raise fastapi.HTTPException(
            status_code=400,
            detail=(
                f'Invalid status {status!r}. '
                f'Must be one of: {", ".join(sorted(valid_statuses))}'
            ),
        )

    props = {
        'name': payload['name'],
        'slug': payload['slug'],
        'description': payload.get('description'),
        'icon': payload.get('icon'),
        'vendor': vendor,
        'service_url': (
            str(payload['service_url']) if payload.get('service_url') else None
        ),
        'category': payload.get('category'),
        'status': status,
        'links': payload.get('links', {}),
        'identifiers': payload.get('identifiers', {}),
    }

    neo4j_props = _serialize_props(props)

    if team_slug:
        query: typing.LiteralString = """
        MATCH (o:Organization {slug: $org_slug})
        MATCH (t:Team {slug: $team_slug})-[:BELONGS_TO]->(o)
        CREATE (s:ThirdPartyService $props)
        CREATE (s)-[:BELONGS_TO]->(o)
        CREATE (s)-[:MANAGED_BY]->(t)
        RETURN s{.*, organization: o{.*}, team: t{.*}}
            AS service
        """
        params: dict[str, typing.Any] = {
            'org_slug': org_slug,
            'team_slug': team_slug,
            'props': neo4j_props,
        }
    else:
        query = """
        MATCH (o:Organization {slug: $org_slug})
        CREATE (s:ThirdPartyService $props)
        CREATE (s)-[:BELONGS_TO]->(o)
        RETURN s{.*, organization: o{.*}, team: null}
            AS service
        """
        params = {
            'org_slug': org_slug,
            'props': neo4j_props,
        }

    try:
        async with neo4j.run(query, **params) as result:
            records = await result.data()
    except exceptions.ConstraintError as e:
        raise fastapi.HTTPException(
            status_code=409,
            detail=(
                f'Third-party service with slug '
                f'{props["slug"]!r} already exists'
            ),
        ) from e

    if not records:
        if team_slug:
            raise fastapi.HTTPException(
                status_code=404,
                detail=(
                    f'Organization {org_slug!r} or team '
                    f'{team_slug!r} not found'
                ),
            )
        raise fastapi.HTTPException(
            status_code=404,
            detail=(f'Organization with slug {org_slug!r} not found'),
        )

    return _deserialize_service(records[0]['service'])


@third_party_services_router.get('/')
async def list_third_party_services(
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission(
                'third_party_service:read',
            ),
        ),
    ],
) -> list[dict[str, typing.Any]]:
    """List all third-party services.

    Returns:
        Services ordered by name, each including their
        organization and optional team.

    """
    query: typing.LiteralString = """
    MATCH (s:ThirdPartyService)-[:BELONGS_TO]->(o:Organization)
    OPTIONAL MATCH (s)-[:MANAGED_BY]->(t:Team)
    RETURN s{.*, organization: o{.*}, team: t{.*}}
        AS service
    ORDER BY s.name
    """
    services: list[dict[str, typing.Any]] = []
    async with neo4j.run(query) as result:
        records = await result.data()
        for record in records:
            services.append(_deserialize_service(record['service']))
    return services


@third_party_services_router.get('/{slug}')
async def get_third_party_service(
    slug: str,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission(
                'third_party_service:read',
            ),
        ),
    ],
) -> dict[str, typing.Any]:
    """Get a third-party service by slug.

    Parameters:
        slug: Service slug identifier.

    Returns:
        Service with organization and optional team.

    Raises:
        404: Service not found

    """
    query: typing.LiteralString = """
    MATCH (s:ThirdPartyService {slug: $slug})
          -[:BELONGS_TO]->(o:Organization)
    OPTIONAL MATCH (s)-[:MANAGED_BY]->(t:Team)
    RETURN s{.*, organization: o{.*}, team: t{.*}}
        AS service
    """
    async with neo4j.run(query, slug=slug) as result:
        records = await result.data()

    if not records:
        raise fastapi.HTTPException(
            status_code=404,
            detail=(f'Third-party service with slug {slug!r} not found'),
        )
    return _deserialize_service(records[0]['service'])


@third_party_services_router.put('/{slug}')
async def update_third_party_service(
    slug: str,
    data: dict[str, typing.Any],
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission(
                'third_party_service:update',
            ),
        ),
    ],
) -> dict[str, typing.Any]:
    """Update a third-party service.

    Parameters:
        slug: Service slug from URL.
        data: Updated service data.

    Returns:
        The updated service.

    Raises:
        400: Validation error
        404: Service not found

    """
    payload = dict(data)
    if 'slug' not in payload:
        payload['slug'] = slug

    payload.pop('organization_slug', None)
    payload.pop('organization', None)

    team_slug = payload.pop('team_slug', None)
    payload.pop('team', None)

    # Fetch existing to validate it exists
    fetch_query: typing.LiteralString = """
    MATCH (s:ThirdPartyService {slug: $slug})
          -[:BELONGS_TO]->(o:Organization)
    OPTIONAL MATCH (s)-[:MANAGED_BY]->(t:Team)
    RETURN s{.*, organization: o{.*}, team: t{.*}}
        AS service
    """
    async with neo4j.run(fetch_query, slug=slug) as result:
        records = await result.data()

    if not records:
        raise fastapi.HTTPException(
            status_code=404,
            detail=(f'Third-party service with slug {slug!r} not found'),
        )

    existing = _deserialize_service(records[0]['service'])

    # Validate status if provided
    valid_statuses = {'active', 'deprecated', 'evaluating', 'inactive'}
    status = payload.get('status', 'active')
    if status not in valid_statuses:
        raise fastapi.HTTPException(
            status_code=400,
            detail=(
                f'Invalid status {status!r}. '
                f'Must be one of: {", ".join(sorted(valid_statuses))}'
            ),
        )

    props = {
        'name': payload.get('name', existing['name']),
        'slug': payload['slug'],
        'description': payload.get('description'),
        'icon': payload.get('icon'),
        'vendor': payload.get('vendor', existing['vendor']),
        'service_url': (
            str(payload['service_url']) if payload.get('service_url') else None
        ),
        'category': payload.get('category'),
        'status': status,
        'links': payload.get('links', existing.get('links', {})),
        'identifiers': payload.get(
            'identifiers',
            existing.get('identifiers', {}),
        ),
    }

    neo4j_props = _serialize_props(props)

    if team_slug:
        update_query: typing.LiteralString = """
        MATCH (s:ThirdPartyService {slug: $slug})
              -[:BELONGS_TO]->(o:Organization)
        OPTIONAL MATCH (s)-[old_mgr:MANAGED_BY]->()
        DELETE old_mgr
        WITH s, o
        MATCH (t:Team {slug: $team_slug})-[:BELONGS_TO]->(o)
        SET s = $props
        CREATE (s)-[:MANAGED_BY]->(t)
        RETURN s{.*, organization: o{.*}, team: t{.*}}
            AS service
        """
        update_params: dict[str, typing.Any] = {
            'slug': slug,
            'team_slug': team_slug,
            'props': neo4j_props,
        }
    else:
        update_query = """
        MATCH (s:ThirdPartyService {slug: $slug})
              -[:BELONGS_TO]->(o:Organization)
        OPTIONAL MATCH (s)-[old_mgr:MANAGED_BY]->()
        DELETE old_mgr
        WITH s, o
        SET s = $props
        RETURN s{.*, organization: o{.*}, team: null}
            AS service
        """
        update_params = {
            'slug': slug,
            'props': neo4j_props,
        }

    try:
        async with neo4j.run(
            update_query,
            **update_params,
        ) as result:
            updated = await result.data()
    except exceptions.ConstraintError as e:
        raise fastapi.HTTPException(
            status_code=409,
            detail=(
                f'Third-party service with slug '
                f'{payload["slug"]!r} already exists'
            ),
        ) from e

    if not updated:
        raise fastapi.HTTPException(
            status_code=404,
            detail=(f'Third-party service with slug {slug!r} not found'),
        )

    return _deserialize_service(updated[0]['service'])


@third_party_services_router.delete('/{slug}', status_code=204)
async def delete_third_party_service(
    slug: str,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission(
                'third_party_service:delete',
            ),
        ),
    ],
) -> None:
    """Delete a third-party service.

    Parameters:
        slug: Service slug to delete.

    Raises:
        404: Service not found

    """
    query: typing.LiteralString = """
    MATCH (s:ThirdPartyService {slug: $slug})
    DETACH DELETE s
    RETURN count(s) AS deleted
    """
    async with neo4j.run(query, slug=slug) as result:
        records = await result.data()

    if not records or records[0]['deleted'] == 0:
        raise fastapi.HTTPException(
            status_code=404,
            detail=(f'Third-party service with slug {slug!r} not found'),
        )
