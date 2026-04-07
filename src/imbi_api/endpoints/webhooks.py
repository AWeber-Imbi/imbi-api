"""Webhook management endpoints."""

import json
import logging
import typing

import fastapi
from imbi_common import age
from imbi_common.age import exceptions
from imbi_common.auth import encryption

from imbi_api.auth import permissions
from imbi_api.domain import models

LOGGER = logging.getLogger(__name__)

webhooks_router = fastapi.APIRouter(tags=['Webhooks'])


async def _fetch_webhook(
    slug: str,
    org_slug: str,
) -> dict[str, typing.Any] | None:
    """Fetch a webhook with its TPS and rules."""
    q: typing.LiteralString = """
    MATCH (w:Webhook {slug: $slug})
          -[:BELONGS_TO]->(o:Organization {slug: $org_slug})
    OPTIONAL MATCH (w)-[impl:IMPLEMENTED_BY]->(tps:ThirdPartyService)
    RETURN properties(w) AS webhook,
           properties(tps) AS tps,
           impl.identifier_selector AS identifier_selector
    """
    records = await age.query(q, slug=slug, org_slug=org_slug)
    if not records:
        return None
    record = records[0]

    # Fetch rules separately
    rq: typing.LiteralString = """
    MATCH (r:WebhookRule)-[:ACTIONS]->(w:Webhook {slug: $slug})
    RETURN r.filter_expression AS filter_expression,
           r.handler AS handler,
           r.handler_config AS handler_config,
           r.ordinal AS ordinal
    ORDER BY r.ordinal
    """
    rule_records = await age.query(rq, slug=slug)
    record['rules'] = [
        {
            'filter_expression': r['filter_expression'],
            'handler': r['handler'],
            'handler_config': r.get('handler_config'),
        }
        for r in rule_records
    ]
    return record


def _build_rule_params(
    rules: list[models.WebhookRuleCreate],
) -> list[dict[str, object]]:
    """Convert WebhookRuleCreate list to parameter dicts."""
    return [
        {
            'filter_expression': rule.filter_expression,
            'handler': rule.handler,
            'handler_config': json.dumps(rule.handler_config),
            'ordinal': idx,
        }
        for idx, rule in enumerate(rules)
    ]


async def _create_rules(
    webhook_slug: str,
    rules: list[dict[str, object]],
) -> None:
    """Create WebhookRule nodes linked to a webhook."""
    for rule in rules:
        q: typing.LiteralString = """
        MATCH (w:Webhook {slug: $slug})
        CREATE (r:WebhookRule {
            filter_expression: $filter_expression,
            handler: $handler,
            handler_config: $handler_config,
            ordinal: $ordinal
        })
        CREATE (r)-[:ACTIONS]->(w)
        RETURN r.ordinal AS ordinal
        """
        await age.query(
            q,
            slug=webhook_slug,
            filter_expression=rule['filter_expression'],
            handler=rule['handler'],
            handler_config=rule['handler_config'],
            ordinal=rule['ordinal'],
        )


@webhooks_router.post('/', status_code=201)
async def create_webhook(
    org_slug: str,
    data: models.WebhookCreate,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('webhook:create'),
        ),
    ],
) -> models.WebhookResponse:
    """Create a new webhook linked to an organization."""
    encryptor = encryption.TokenEncryption.get_instance()

    encrypted_secret = (
        encryptor.encrypt(data.secret) if data.secret is not None else None
    )

    # Step 1: Create webhook node
    create_q: typing.LiteralString = """
    MATCH (o:Organization {slug: $org_slug})
    CREATE (w:Webhook {
        name: $name,
        slug: $slug,
        description: $description,
        icon: $icon,
        notification_path: $notification_path,
        secret: $secret
    })
    CREATE (w)-[:BELONGS_TO]->(o)
    RETURN w.slug AS slug
    """
    try:
        records = await age.query(
            create_q,
            org_slug=org_slug,
            name=data.name,
            slug=data.slug,
            description=data.description,
            icon=data.icon,
            notification_path=data.notification_path,
            secret=encrypted_secret,
        )
    except exceptions.ConstraintError as e:
        raise fastapi.HTTPException(
            status_code=409,
            detail=(
                f'Webhook with slug {data.slug!r} '
                f'or notification_path '
                f'{data.notification_path!r} already exists'
            ),
        ) from e

    if not records:
        raise fastapi.HTTPException(
            status_code=404,
            detail=f'Organization {org_slug!r} not found',
        )

    # Step 2: Link to TPS if provided
    if data.third_party_service_slug:
        tps_q: typing.LiteralString = """
        MATCH (w:Webhook {slug: $slug})
        MATCH (tps:ThirdPartyService {slug: $tps_slug})
              -[:BELONGS_TO]->(:Organization {slug: $org_slug})
        CREATE (w)-[impl:IMPLEMENTED_BY]->(tps)
        SET impl.identifier_selector = $identifier_selector
        RETURN tps.slug AS tps_slug
        """
        await age.query(
            tps_q,
            slug=data.slug,
            tps_slug=data.third_party_service_slug,
            org_slug=org_slug,
            identifier_selector=data.identifier_selector,
        )

    # Step 3: Create rules
    await _create_rules(data.slug, _build_rule_params(data.rules))

    # Step 4: Fetch and return
    result = await _fetch_webhook(data.slug, org_slug)
    if not result:
        raise fastapi.HTTPException(
            status_code=500,
            detail='Webhook created but could not be retrieved',
        )
    return models.WebhookResponse.from_neo4j_record(result)


@webhooks_router.get('/')
async def list_webhooks(
    org_slug: str,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('webhook:read'),
        ),
    ],
) -> list[models.WebhookResponse]:
    """List webhooks for an organization."""
    # Batch fetch all webhooks with their TPS in one query
    wh_q: typing.LiteralString = """
    MATCH (w:Webhook)-[:BELONGS_TO]->
          (o:Organization {slug: $org_slug})
    OPTIONAL MATCH (w)-[impl:IMPLEMENTED_BY]->(tps:ThirdPartyService)
    RETURN properties(w) AS webhook,
           properties(tps) AS tps,
           impl.identifier_selector AS identifier_selector
    ORDER BY w.name
    """
    wh_records = await age.query(wh_q, org_slug=org_slug)

    # Batch fetch all rules for this org's webhooks
    rules_q: typing.LiteralString = """
    MATCH (r:WebhookRule)-[:ACTIONS]->(w:Webhook)
          -[:BELONGS_TO]->(:Organization {slug: $org_slug})
    RETURN w.slug AS slug,
           r.filter_expression AS filter_expression,
           r.handler AS handler,
           r.handler_config AS handler_config,
           r.ordinal AS ordinal
    ORDER BY w.slug, r.ordinal
    """
    rule_records = await age.query(rules_q, org_slug=org_slug)

    # Group rules by webhook slug
    rules_by_slug: dict[str, list[dict[str, typing.Any]]] = {}
    for r in rule_records:
        rules_by_slug.setdefault(r['slug'], []).append(
            {
                'filter_expression': r['filter_expression'],
                'handler': r['handler'],
                'handler_config': r.get('handler_config'),
            }
        )

    results: list[models.WebhookResponse] = []
    for record in wh_records:
        webhook = record['webhook']
        slug = webhook.get('slug', '')
        record['rules'] = rules_by_slug.get(slug, [])
        results.append(models.WebhookResponse.from_neo4j_record(record))
    return results


@webhooks_router.get('/{slug}')
async def get_webhook(
    org_slug: str,
    slug: str,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('webhook:read'),
        ),
    ],
) -> models.WebhookResponse:
    """Get a webhook by slug."""
    record = await _fetch_webhook(slug, org_slug)
    if not record:
        raise fastapi.HTTPException(
            status_code=404,
            detail=f'Webhook with slug {slug!r} not found',
        )
    return models.WebhookResponse.from_neo4j_record(record)


@webhooks_router.put('/{slug}')
async def update_webhook(
    org_slug: str,
    slug: str,
    data: models.WebhookUpdate,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('webhook:update'),
        ),
    ],
) -> models.WebhookResponse:
    """Update a webhook (full replacement including rules)."""
    # Verify exists
    check_q: typing.LiteralString = """
    MATCH (w:Webhook {slug: $slug})
          -[:BELONGS_TO]->(o:Organization {slug: $org_slug})
    RETURN properties(w) AS webhook
    """
    existing = await age.query(
        check_q,
        slug=slug,
        org_slug=org_slug,
    )
    if not existing:
        raise fastapi.HTTPException(
            status_code=404,
            detail=f'Webhook with slug {slug!r} not found',
        )

    encryptor = encryption.TokenEncryption.get_instance()

    existing_webhook = existing[0]['webhook']
    if 'secret' not in data.model_fields_set:
        encrypted_secret = existing_webhook.get('secret')
    elif data.secret is None:
        encrypted_secret = None
    else:
        encrypted_secret = encryptor.encrypt(data.secret)

    # Step 1: Delete old rules
    del_rules_q: typing.LiteralString = """
    MATCH (r:WebhookRule)-[:ACTIONS]->(w:Webhook {slug: $slug})
    DETACH DELETE r
    RETURN count(r) AS deleted
    """
    await age.query(del_rules_q, slug=slug)

    # Step 2: Remove old IMPLEMENTED_BY
    del_impl_q: typing.LiteralString = """
    MATCH (w:Webhook {slug: $slug})-[impl:IMPLEMENTED_BY]->()
    DELETE impl
    RETURN count(impl) AS deleted
    """
    await age.query(del_impl_q, slug=slug)

    # Step 3: Update webhook properties
    update_q: typing.LiteralString = """
    MATCH (w:Webhook {slug: $old_slug})
    SET w.name = $name,
        w.slug = $new_slug,
        w.description = $description,
        w.icon = $icon,
        w.notification_path = $notification_path,
        w.secret = $secret
    RETURN w.slug AS slug
    """
    try:
        updated = await age.query(
            update_q,
            old_slug=slug,
            name=data.name,
            new_slug=data.slug,
            description=data.description,
            icon=data.icon,
            notification_path=data.notification_path,
            secret=encrypted_secret,
        )
    except exceptions.ConstraintError as e:
        raise fastapi.HTTPException(
            status_code=409,
            detail=(
                f'Webhook with slug {data.slug!r} '
                f'or notification_path '
                f'{data.notification_path!r} already exists'
            ),
        ) from e

    if not updated:
        raise fastapi.HTTPException(
            status_code=404,
            detail=f'Webhook with slug {slug!r} not found',
        )

    new_slug = data.slug

    # Step 4: Link to TPS if provided
    if data.third_party_service_slug:
        tps_q: typing.LiteralString = """
        MATCH (w:Webhook {slug: $slug})
        MATCH (tps:ThirdPartyService {slug: $tps_slug})
              -[:BELONGS_TO]->(:Organization {slug: $org_slug})
        CREATE (w)-[impl:IMPLEMENTED_BY]->(tps)
        SET impl.identifier_selector = $identifier_selector
        RETURN tps.slug AS tps_slug
        """
        await age.query(
            tps_q,
            slug=new_slug,
            tps_slug=data.third_party_service_slug,
            org_slug=org_slug,
            identifier_selector=data.identifier_selector,
        )

    # Step 5: Create new rules
    await _create_rules(new_slug, _build_rule_params(data.rules))

    # Step 6: Fetch and return
    result = await _fetch_webhook(new_slug, org_slug)
    if not result:
        raise fastapi.HTTPException(
            status_code=404,
            detail=f'Webhook with slug {slug!r} not found',
        )
    return models.WebhookResponse.from_neo4j_record(result)


@webhooks_router.delete('/{slug}', status_code=204)
async def delete_webhook(
    org_slug: str,
    slug: str,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('webhook:delete'),
        ),
    ],
) -> None:
    """Delete a webhook and its rules."""
    query: typing.LiteralString = """
    MATCH (w:Webhook {slug: $slug})
          -[:BELONGS_TO]->(o:Organization {slug: $org_slug})
    OPTIONAL MATCH (r:WebhookRule)-[:ACTIONS]->(w)
    DETACH DELETE r, w
    RETURN count(w) AS deleted
    """
    records = await age.query(
        query,
        slug=slug,
        org_slug=org_slug,
    )

    if not records or records[0].get('deleted', 0) == 0:
        raise fastapi.HTTPException(
            status_code=404,
            detail=f'Webhook with slug {slug!r} not found',
        )


# -- Project EXISTS_IN endpoints -------------------------------------------


project_services_router = fastapi.APIRouter(
    tags=['Project Services'],
)


@project_services_router.get('/')
async def list_project_services(
    org_slug: str,
    project_id: str,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('project:read'),
        ),
    ],
) -> list[models.ExistsInResponse]:
    """List third-party services this project exists in."""
    query: typing.LiteralString = """
    MATCH (p:Project {id: $project_id})
          -[:OWNED_BY]->(:Team)
          -[:BELONGS_TO]->(o:Organization {slug: $org_slug})
    MATCH (p)-[ei:EXISTS_IN]->(tps:ThirdPartyService)
    RETURN tps.slug AS service_slug,
           tps.name AS service_name,
           ei.identifier AS identifier,
           ei.canonical_link AS canonical_link
    ORDER BY tps.name
    """
    records = await age.query(
        query,
        org_slug=org_slug,
        project_id=project_id,
    )

    return [
        models.ExistsInResponse(
            third_party_service_slug=r['service_slug'],
            third_party_service_name=r['service_name'],
            identifier=r['identifier'],
            canonical_link=r.get('canonical_link'),
        )
        for r in records
    ]


@project_services_router.post('/', status_code=201)
async def create_project_service(
    org_slug: str,
    project_id: str,
    data: models.ExistsInCreate,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('project:write'),
        ),
    ],
) -> models.ExistsInResponse:
    """Add an EXISTS_IN link between a project and a service."""
    query: typing.LiteralString = """
    MATCH (p:Project {id: $project_id})
          -[:OWNED_BY]->(:Team)
          -[:BELONGS_TO]->(o:Organization {slug: $org_slug})
    MATCH (tps:ThirdPartyService {slug: $tps_slug})
          -[:BELONGS_TO]->(o)
    MERGE (p)-[ei:EXISTS_IN]->(tps)
    SET ei.identifier = $identifier,
        ei.canonical_link = $canonical_link
    RETURN tps.slug AS service_slug,
           tps.name AS service_name,
           ei.identifier AS identifier,
           ei.canonical_link AS canonical_link
    """
    records = await age.query(
        query,
        org_slug=org_slug,
        project_id=project_id,
        tps_slug=data.third_party_service_slug,
        identifier=data.identifier,
        canonical_link=data.canonical_link,
    )

    if not records:
        raise fastapi.HTTPException(
            status_code=404,
            detail=(
                f'Project {project_id!r} or '
                f'service {data.third_party_service_slug!r} '
                f'not found'
            ),
        )

    r = records[0]
    return models.ExistsInResponse(
        third_party_service_slug=r['service_slug'],
        third_party_service_name=r['service_name'],
        identifier=r['identifier'],
        canonical_link=r.get('canonical_link'),
    )


@project_services_router.delete(
    '/{service_slug}',
    status_code=204,
)
async def delete_project_service(
    org_slug: str,
    project_id: str,
    service_slug: str,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('project:write'),
        ),
    ],
) -> None:
    """Remove an EXISTS_IN link."""
    query: typing.LiteralString = """
    MATCH (p:Project {id: $project_id})
          -[:OWNED_BY]->(:Team)
          -[:BELONGS_TO]->(o:Organization {slug: $org_slug})
    MATCH (p)-[ei:EXISTS_IN]->
          (tps:ThirdPartyService {slug: $tps_slug})
    DELETE ei
    RETURN count(ei) AS deleted
    """
    records = await age.query(
        query,
        org_slug=org_slug,
        project_id=project_id,
        tps_slug=service_slug,
    )

    if not records or records[0].get('deleted', 0) == 0:
        raise fastapi.HTTPException(
            status_code=404,
            detail=(
                f'EXISTS_IN link between project '
                f'{project_id!r} and service '
                f'{service_slug!r} not found'
            ),
        )
