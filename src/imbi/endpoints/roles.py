"""Role and permission management endpoints."""

import logging
import typing

import fastapi
from neo4j import exceptions

from imbi import models, neo4j
from imbi.auth import permissions

LOGGER = logging.getLogger(__name__)

roles_router = fastapi.APIRouter(prefix='/roles', tags=['Roles'])


@roles_router.post('/', response_model=models.Role, status_code=201)
async def create_role(
    role: models.Role,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(permissions.require_permission('role:create')),
    ],
) -> models.Role:
    """Create a new role.

    Args:
        role: The role to create
        auth: Authentication context (injected)

    Returns:
        The created role

    Raises:
        401: Not authenticated
        403: Missing role:create permission
        409: Role with same slug already exists

    """
    try:
        return await neo4j.create_node(role)
    except exceptions.ConstraintError as e:
        raise fastapi.HTTPException(
            status_code=409,
            detail=f'Role with slug {role.slug!r} already exists',
        ) from e


@roles_router.get('/', response_model=list[models.Role])
async def list_roles(
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(permissions.require_permission('role:read')),
    ],
) -> list[models.Role]:
    """List all roles.

    Args:
        auth: Authentication context (injected)

    Returns:
        List of all roles ordered by priority (highest first)

    Raises:
        401: Not authenticated
        403: Missing role:read permission

    """
    roles = []
    async for role in neo4j.fetch_nodes(
        models.Role, order_by='priority DESC, name'
    ):
        roles.append(role)
    return roles


@roles_router.get('/{slug}', response_model=models.Role)
async def get_role(
    slug: str,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(permissions.require_permission('role:read')),
    ],
) -> models.Role:
    """Get a specific role by slug.

    Args:
        slug: The role slug
        auth: Authentication context (injected)

    Returns:
        The requested role with permissions and parent role loaded

    Raises:
        401: Not authenticated
        403: Missing role:read permission
        404: Role not found

    """
    role = await neo4j.fetch_node(models.Role, {'slug': slug})
    if role is None:
        raise fastapi.HTTPException(
            status_code=404, detail=f'Role with slug {slug!r} not found'
        )

    # Load permissions relationship
    await neo4j.refresh_relationship(role, 'permissions')

    # Load parent role relationship
    await neo4j.refresh_relationship(role, 'parent_role')

    return role


@roles_router.put('/{slug}', response_model=models.Role)
async def update_role(
    slug: str,
    role: models.Role,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(permissions.require_permission('role:update')),
    ],
) -> models.Role:
    """Update or create a role (upsert).

    Args:
        slug: The role slug
        role: The role data
        auth: Authentication context (injected)

    Returns:
        The updated/created role

    Raises:
        400: Slug mismatch or attempting to modify system role
        401: Not authenticated
        403: Missing role:update permission

    """
    # Validate that URL slug matches role slug
    if role.slug != slug:
        raise fastapi.HTTPException(
            status_code=400,
            detail=f'Slug in URL ({slug!r}) must match slug in '
            f'role data ({role.slug!r})',
        )

    # Check if role is a system role
    existing_role = await neo4j.fetch_node(models.Role, {'slug': slug})
    if existing_role and existing_role.is_system:
        raise fastapi.HTTPException(
            status_code=400, detail='Cannot modify system role'
        )

    await neo4j.upsert(role, {'slug': slug})
    return role


@roles_router.delete('/{slug}', status_code=204)
async def delete_role(
    slug: str,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(permissions.require_permission('role:delete')),
    ],
) -> None:
    """Delete a role by slug.

    System roles cannot be deleted.

    Args:
        slug: The role slug
        auth: Authentication context (injected)

    Raises:
        400: Trying to delete a system role
        401: Not authenticated
        403: Missing role:delete permission
        404: Role not found

    """
    # Check if role exists and is not a system role
    role = await neo4j.fetch_node(models.Role, {'slug': slug})
    if role is None:
        raise fastapi.HTTPException(
            status_code=404, detail=f'Role with slug {slug!r} not found'
        )

    if role.is_system:
        raise fastapi.HTTPException(
            status_code=400, detail='Cannot delete system role'
        )

    deleted = await neo4j.delete_node(models.Role, {'slug': slug})
    if not deleted:
        raise fastapi.HTTPException(
            status_code=404, detail=f'Role with slug {slug!r} not found'
        )


@roles_router.post('/{slug}/permissions', status_code=204)
async def grant_permission(
    slug: str,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(permissions.require_permission('role:update')),
    ],
    permission_name: str = fastapi.Body(..., embed=True),
) -> None:
    """Grant a permission to a role.

    Args:
        slug: The role slug
        permission_name: The permission name to grant (e.g., 'blueprint:read')
        auth: Authentication context (injected)

    Raises:
        401: Not authenticated
        403: Missing role:update permission
        404: Role or permission not found

    """
    # Check if role exists
    role = await neo4j.fetch_node(models.Role, {'slug': slug})
    if role is None:
        raise fastapi.HTTPException(
            status_code=404, detail=f'Role with slug {slug!r} not found'
        )

    # Check if permission exists
    perm = await neo4j.fetch_node(models.Permission, {'name': permission_name})
    if perm is None:
        raise fastapi.HTTPException(
            status_code=404,
            detail=f'Permission {permission_name!r} not found',
        )

    # Create GRANTS relationship
    query = """
    MATCH (role:Role {slug: $slug})
    MATCH (perm:Permission {name: $permission_name})
    MERGE (role)-[:GRANTS]->(perm)
    """
    async with neo4j.run(
        query, slug=slug, permission_name=permission_name
    ) as result:
        await result.consume()

    LOGGER.info('Granted permission %s to role %s', permission_name, slug)


@roles_router.delete('/{slug}/permissions/{permission_name}', status_code=204)
async def revoke_permission(
    slug: str,
    permission_name: str,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(permissions.require_permission('role:update')),
    ],
) -> None:
    """Revoke a permission from a role.

    Args:
        slug: The role slug
        permission_name: The permission name to revoke
        auth: Authentication context (injected)

    Raises:
        401: Not authenticated
        403: Missing role:update permission
        404: Role or permission not found

    """
    # Check if role exists
    role = await neo4j.fetch_node(models.Role, {'slug': slug})
    if role is None:
        raise fastapi.HTTPException(
            status_code=404, detail=f'Role with slug {slug!r} not found'
        )

    # Delete GRANTS relationship
    query = """
    MATCH (role:Role {slug: $slug})-[r:GRANTS]->
          (perm:Permission {name: $permission_name})
    DELETE r
    RETURN count(r) AS deleted
    """
    async with neo4j.run(
        query, slug=slug, permission_name=permission_name
    ) as result:
        records = await result.data()
        if not records or records[0]['deleted'] == 0:
            raise fastapi.HTTPException(
                status_code=404,
                detail=f'Permission {permission_name!r} not granted to '
                f'role {slug!r}',
            )

    LOGGER.info('Revoked permission %s from role %s', permission_name, slug)
