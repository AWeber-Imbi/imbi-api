"""Organization management endpoints with blueprint support."""

import logging
import typing

import fastapi
import pydantic
from neo4j import exceptions

from imbi import blueprints, models, neo4j
from imbi.auth import permissions

LOGGER = logging.getLogger(__name__)

organizations_router = fastapi.APIRouter(
    prefix='/organizations', tags=['Organizations']
)


@organizations_router.post('/', status_code=201)
async def create_organization(
    data: models.Organization,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(permissions.require_permission('organization:create')),
    ],
) -> models.Organization:
    """
    Create a new organization with blueprint fields applied.

    This endpoint accepts the base Organization fields (name, slug,
    description, icon_url) plus any additional fields defined by enabled
    blueprints.

    To see the current schema with all blueprint fields, use:
    `GET /schema/Organization`

    Parameters:
        data: Organization data including base fields and blueprint fields.
            Base fields:
            - name (str, required): Organization name
            - slug (str, required): URL-safe identifier
            - description (str, optional): Organization description
            - icon_url (str, optional): Icon URL

    Returns:
        dict: The created organization with all fields (base + blueprint)

    Raises:
        400: Invalid data or validation error
        409: Organization with slug already exists
        401: Not authenticated
        403: Missing organization:create permission

    Example:
        ```json
        {
            "name": "Engineering",
            "slug": "engineering",
            "description": "Engineering department",
            "region": "us-west-2",  // Custom blueprint field
            "cost_center": "ENG-001"  // Custom blueprint field
        }
        ```
    """
    dynamic_model = await blueprints.get_model(models.Organization)
    try:
        org = dynamic_model.model_validate(data.model_dump())
    except pydantic.ValidationError as e:
        LOGGER.warning('Validation error creating organization: %s', e)
        raise fastapi.HTTPException(
            status_code=400,
            detail=f'Validation error: {e.errors()}',
        ) from e

    try:
        return await neo4j.create_node(org)  # type: ignore
    except exceptions.ConstraintError as e:
        # Get slug from org (dynamic model)
        slug_value = getattr(org, 'slug', 'unknown')
        raise fastapi.HTTPException(
            status_code=409,
            detail=f'Organization with slug {slug_value!r} already exists',
        ) from e


@organizations_router.get('/')
async def list_organizations(
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(permissions.require_permission('organization:read')),
    ],
) -> list[models.Organization]:
    """
    Retrieve all organizations with blueprint fields applied.

    Returns organizations ordered by name. Each organization includes base
    fields plus any additional fields from enabled blueprints.

    To see the current schema with all blueprint fields, use:
    `GET /schema/Organization`

    Returns:
        list: Organizations with base + blueprint fields, ordered by name

    Raises:
        401: Not authenticated
        403: Missing organization:read permission
    """
    # Apply blueprints to get dynamic model
    dynamic_model = await blueprints.get_model(models.Organization)

    organizations: list[models.Organization] = []
    async for org in neo4j.fetch_nodes(dynamic_model, order_by='name'):
        organizations.append(org)  # type: ignore[arg-type]
    return organizations


@organizations_router.get('/{slug}')
async def get_organization(
    slug: str,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(permissions.require_permission('organization:read')),
    ],
) -> models.Organization:
    """
    Retrieve an organization by slug with blueprint fields applied.

    To see the current schema with all blueprint fields, use:
    `GET /schema/Organization`

    Parameters:
        slug: Organization slug identifier

    Returns:
        dict: Organization with base + blueprint fields

    Raises:
        404: Organization not found
        401: Not authenticated
        403: Missing organization:read permission
    """
    dynamic_model = await blueprints.get_model(models.Organization)
    org = await neo4j.fetch_node(dynamic_model, {'slug': slug})
    if org is None:
        raise fastapi.HTTPException(
            status_code=404,
            detail=f'Organization with slug {slug!r} not found',
        )
    return org  # type: ignore


@organizations_router.put('/{slug}')
async def update_organization(
    slug: str,
    data: models.Organization,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(permissions.require_permission('organization:update')),
    ],
) -> models.Organization:
    """
    Update an existing organization with blueprint fields.

    This endpoint accepts the base Organization fields plus any additional
    fields defined by enabled blueprints.

    To see the current schema with all blueprint fields, use:
    `GET /schema/Organization`

    Parameters:
        slug: Organization slug from URL
        data: Updated organization data (base + blueprint fields)

    Returns:
        dict: The updated organization with all fields

    Raises:
        400: Invalid data, validation error, or slug mismatch
        404: Organization not found
        401: Not authenticated
        403: Missing organization:update permission
    """
    dynamic_model = await blueprints.get_model(models.Organization)
    existing = await neo4j.fetch_node(dynamic_model, {'slug': slug})
    if existing is None:
        raise fastapi.HTTPException(
            status_code=404,
            detail=f'Organization with slug {slug!r} not found',
        )

    if data.slug != slug:
        raise fastapi.HTTPException(
            status_code=400,
            detail=f'Slug in URL ({slug!r}) must match slug in body '
            f'({data.slug!r})',
        )

    try:
        org = dynamic_model.model_validate(data.model_dump())
    except pydantic.ValidationError as e:
        LOGGER.warning('Validation error updating organization: %s', e)
        raise fastapi.HTTPException(
            status_code=400,
            detail=f'Validation error: {e.errors()}',
        ) from e

    await neo4j.upsert(org, {'slug': slug})
    return org  # type: ignore


@organizations_router.delete('/{slug}', status_code=204)
async def delete_organization(
    slug: str,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(permissions.require_permission('organization:delete')),
    ],
) -> None:
    """
    Delete an organization.

    Parameters:
        slug: Organization slug to delete

    Raises:
        404: Organization not found
        401: Not authenticated
        403: Missing organization:delete permission
    """
    deleted = await neo4j.delete_node(models.Organization, {'slug': slug})
    if not deleted:
        raise fastapi.HTTPException(
            status_code=404,
            detail=f'Organization with slug {slug!r} not found',
        )
