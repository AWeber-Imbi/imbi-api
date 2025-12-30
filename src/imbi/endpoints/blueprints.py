"""Blueprint CRUD endpoints"""

import logging
import typing

import fastapi
import neo4j

from imbi import models
from imbi import neo4j as imbi_neo4j

LOGGER = logging.getLogger(__name__)

blueprint_router = fastapi.APIRouter(prefix='/blueprints', tags=['blueprints'])


@blueprint_router.post('/', response_model=models.Blueprint, status_code=201)
async def create_blueprint(blueprint: models.Blueprint) -> models.Blueprint:
    """Create a new blueprint.

    Creates a blueprint node in the graph database. The slug will be
    auto-generated from the name if not provided.

    Returns:
        The created blueprint with round-trip values from the database

    Raises:
        409: Blueprint with same name and type already exists
    """
    try:
        node = await imbi_neo4j.create_node(blueprint)
        return models.Blueprint.model_validate(dict(node))
    except neo4j.exceptions.ConstraintError as e:
        raise fastapi.HTTPException(
            status_code=409,
            detail=f'Blueprint with name {blueprint.name!r} and type '
            f'{blueprint.type!r} already exists',
        ) from e


@blueprint_router.get('/', response_model=list[models.Blueprint])
async def list_blueprints(
    enabled: bool | None = None,
) -> list[models.Blueprint]:
    """List all blueprints.

    Args:
        enabled: Optional filter by enabled status

    Returns:
        List of all blueprints (optionally filtered)
    """
    parameters = {}
    if enabled is not None:
        parameters['enabled'] = enabled

    blueprints = []
    async for blueprint in imbi_neo4j.fetch_nodes(
        models.Blueprint,
        parameters if parameters else None,
        order_by='name',
    ):
        blueprints.append(blueprint)
    return blueprints


@blueprint_router.get('/{type}', response_model=list[models.Blueprint])
async def list_blueprints_by_type(
    blueprint_type: typing.Annotated[
        typing.Literal[
            'Organization', 'Team', 'Environment', 'ProjectType', 'Project'
        ],
        fastapi.Path(alias='type'),
    ],
    enabled: bool | None = None,
) -> list[models.Blueprint]:
    """List all blueprints of a specific type.

    Args:
        blueprint_type: The blueprint type to filter by
        enabled: Optional filter by enabled status

    Returns:
        List of blueprints matching the type (optionally filtered)
    """
    parameters: dict[str, typing.Any] = {'type': blueprint_type}
    if enabled is not None:
        parameters['enabled'] = enabled

    blueprints = []
    async for blueprint in imbi_neo4j.fetch_nodes(
        models.Blueprint, parameters, order_by='name'
    ):
        blueprints.append(blueprint)
    return blueprints


@blueprint_router.get('/{type}/{slug}', response_model=models.Blueprint)
async def get_blueprint(
    blueprint_type: typing.Annotated[
        typing.Literal[
            'Organization', 'Team', 'Environment', 'ProjectType', 'Project'
        ],
        fastapi.Path(alias='type'),
    ],
    slug: str,
) -> models.Blueprint:
    """Get a specific blueprint by type and slug.

    Args:
        blueprint_type: The blueprint type
        slug: The blueprint slug (URL-safe identifier)

    Returns:
        The requested blueprint

    Raises:
        404: Blueprint not found
    """
    blueprint = await imbi_neo4j.fetch_node(
        models.Blueprint, {'slug': slug, 'type': blueprint_type}
    )
    if blueprint is None:
        raise fastapi.HTTPException(
            status_code=404,
            detail=f'Blueprint with slug {slug!r} and type '
            f'{blueprint_type!r} not found',
        )
    return blueprint


@blueprint_router.put('/{type}/{slug}', response_model=models.Blueprint)
async def update_blueprint(
    blueprint_type: typing.Annotated[
        typing.Literal[
            'Organization', 'Team', 'Environment', 'ProjectType', 'Project'
        ],
        fastapi.Path(alias='type'),
    ],
    slug: str,
    blueprint: models.Blueprint,
) -> models.Blueprint:
    """Update or create a blueprint (upsert).

    If the blueprint exists, it will be updated. If it doesn't exist,
    it will be created.

    Args:
        blueprint_type: The blueprint type
        slug: The blueprint slug (URL-safe identifier)
        blueprint: The blueprint data

    Returns:
        The updated/created blueprint

    Raises:
        400: Slug in URL doesn't match slug in blueprint data
    """
    # Validate that URL slug matches blueprint slug
    if blueprint.slug != slug:
        raise fastapi.HTTPException(
            status_code=400,
            detail=f'Slug in URL ({slug!r}) must match slug in '
            f'blueprint data ({blueprint.slug!r})',
        )

    # Validate that URL type matches blueprint type
    if blueprint.type != blueprint_type:
        raise fastapi.HTTPException(
            status_code=400,
            detail=f'Type in URL ({blueprint_type!r}) must match type in '
            f'blueprint data ({blueprint.type!r})',
        )

    await imbi_neo4j.upsert(blueprint, {'slug': slug, 'type': blueprint_type})
    return blueprint


@blueprint_router.delete('/{type}/{slug}', status_code=204)
async def delete_blueprint(
    blueprint_type: typing.Annotated[
        typing.Literal[
            'Organization', 'Team', 'Environment', 'ProjectType', 'Project'
        ],
        fastapi.Path(alias='type'),
    ],
    slug: str,
) -> None:
    """Delete a blueprint by type and slug.

    Args:
        blueprint_type: The blueprint type
        slug: The blueprint slug (URL-safe identifier)

    Raises:
        404: Blueprint not found
    """
    deleted = await imbi_neo4j.delete_node(
        models.Blueprint, {'slug': slug, 'type': blueprint_type}
    )
    if not deleted:
        raise fastapi.HTTPException(
            status_code=404,
            detail=f'Blueprint with slug {slug!r} and type '
            f'{blueprint_type!r} not found',
        )
