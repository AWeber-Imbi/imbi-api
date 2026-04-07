"""Project management endpoints.

Projects are identified by a Nano-ID (``id`` field) and may
belong to multiple project types.  See ADR-0006 for rationale.
"""

import datetime
import json
import logging
import typing

import fastapi
import nanoid
import pydantic
from imbi_common import age, blueprints, models
from imbi_common.age import exceptions

from imbi_api.auth import permissions
from imbi_api.relationships import relationship_link

LOGGER = logging.getLogger(__name__)

projects_router = fastapi.APIRouter(tags=['Projects'])


# -- Request / Response models ------------------------------------------


class EnvironmentRef(models.Environment):
    """Environment with deployment URL from the DEPLOYED_IN edge."""

    url: pydantic.AnyUrl | str | None = None


class ProjectCreate(pydantic.BaseModel):
    """Request body for creating a project.

    Blueprint-defined fields are accepted as extra properties.
    """

    model_config = pydantic.ConfigDict(extra='allow')

    name: str
    slug: str
    description: str | None = None
    icon: pydantic.HttpUrl | str | None = None
    team_slug: str
    project_type_slugs: list[str] = pydantic.Field(min_length=1)
    environments: dict[str, str | None] = pydantic.Field(
        default_factory=dict,
        description=(
            'Map of environment slug to URL (or null for no URL). '
            'Example: {"production": "https://...", "staging": null}'
        ),
    )
    links: dict[str, pydantic.AnyUrl] = {}
    identifiers: dict[str, int | str] = {}

    @pydantic.field_validator('project_type_slugs')
    @classmethod
    def _deduplicate_type_slugs(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))


class ProjectUpdate(pydantic.BaseModel):
    """Request body for updating a project.

    Blueprint-defined fields are accepted as extra properties.
    """

    model_config = pydantic.ConfigDict(extra='allow')

    name: str | None = None
    slug: str | None = None
    description: str | None = None
    icon: pydantic.HttpUrl | str | None = None
    team_slug: str | None = None
    project_type_slugs: list[str] | None = pydantic.Field(
        default=None, min_length=1
    )

    @pydantic.field_validator('project_type_slugs')
    @classmethod
    def _deduplicate_type_slugs(
        cls,
        v: list[str] | None,
    ) -> list[str] | None:
        if v is not None:
            return list(dict.fromkeys(v))
        return v

    environments: dict[str, str | None] | None = pydantic.Field(
        default=None,
        description=(
            'Map of environment slug to URL (or null). '
            'Replaces all environment assignments when provided.'
        ),
    )
    links: dict[str, pydantic.AnyUrl] | None = None
    identifiers: dict[str, int | str] | None = None


class ProjectResponse(pydantic.BaseModel):
    """Response body for a project."""

    model_config = pydantic.ConfigDict(extra='allow')

    id: str | None = None
    name: str
    slug: str
    description: str | None = None
    icon: pydantic.HttpUrl | str | None = None
    created_at: datetime.datetime | None = None
    updated_at: datetime.datetime | None = None
    team: models.Team
    project_types: list[models.ProjectType] = []
    environments: list[EnvironmentRef] = []
    links: dict[str, pydantic.AnyUrl] = {}
    identifiers: dict[str, int | str] = {}
    relationships: dict[str, models.RelationshipLink] | None = None
    dependency_uris: list[str] = []

    @pydantic.field_validator(
        'links',
        'identifiers',
        mode='before',
    )
    @classmethod
    def _parse_json_strings(
        cls,
        value: typing.Any,
    ) -> typing.Any:
        """Neo4j stores dicts as JSON strings."""
        if isinstance(value, str):
            return json.loads(value)
        return value


# -- Helpers ------------------------------------------------------------

_RESERVED_FIELDS = frozenset(
    {
        'id',
        'team',
        'project_types',
        'environments',
        'created_at',
        'updated_at',
    }
)


def _add_relationships(
    project: dict[str, typing.Any],
    org_slug: str,
    dependency_count: int = 0,
) -> dict[str, typing.Any]:
    """Attach relationships sub-object to a project dict."""
    project_id = project.get('id') or ''
    team = project.get('team', {})
    team_slug = team.get('slug', '') if team else ''
    base = f'/api/organizations/{org_slug}/projects/{project_id}'
    project['relationships'] = {
        'team': relationship_link(
            f'/api/organizations/{org_slug}/teams/{team_slug}',
            1 if team_slug else 0,
        ),
        'environments': relationship_link(
            f'{base}/environments',
            len(project.get('environments') or []),
        ),
        'dependencies': relationship_link(
            f'{base}/dependencies',
            dependency_count,
        ),
    }
    return project


# -- Helpers for fetching project details (AGE-compatible) -------------

_RETURN_FRAGMENT: typing.LiteralString = """
    RETURN properties(p) AS project
"""


async def _fetch_project_details(
    project_id: str,
    org_slug: str,
) -> dict[str, typing.Any] | None:
    """Fetch a project with all its relationships via separate queries.

    Apache AGE does not support ``CALL {}`` subqueries or map
    projections (``n{.*}``), so we run several small queries and
    assemble the result in Python.
    """
    # 1. Get the project node
    q1: typing.LiteralString = """
    MATCH (p:Project {id: $project_id})
          -[:OWNED_BY]->(:Team)
          -[:BELONGS_TO]->(o:Organization {slug: $org_slug})
    RETURN properties(p) AS project
    """
    records = await age.query(q1, project_id=project_id, org_slug=org_slug)
    if not records:
        return None
    project: dict[str, typing.Any] = records[0]['project']

    # 2. Get team (with its organization)
    q2: typing.LiteralString = """
    MATCH (p:Project {id: $project_id})-[:OWNED_BY]->(t:Team)
          -[:BELONGS_TO]->(o:Organization)
    RETURN properties(t) AS team, properties(o) AS org
    """
    team_records = await age.query(q2, project_id=project_id)
    if team_records:
        team = team_records[0]['team']
        team['organization'] = team_records[0].get('org')
        project['team'] = team

    # 3. Get project types
    q3: typing.LiteralString = """
    MATCH (p:Project {id: $project_id})-[:TYPE]->(pt:ProjectType)
          -[:BELONGS_TO]->(o:Organization)
    RETURN properties(pt) AS pt, properties(o) AS org
    """
    pt_records = await age.query(q3, project_id=project_id)
    pts: list[dict[str, typing.Any]] = []
    for r in pt_records:
        pt = r['pt']
        pt['organization'] = r.get('org')
        pts.append(pt)
    project['project_types'] = pts

    # 4. Get environments with deployment URLs
    q4: typing.LiteralString = """
    MATCH (p:Project {id: $project_id})-[d:DEPLOYED_IN]->(env:Environment)
          -[:BELONGS_TO]->(o:Organization)
    RETURN properties(env) AS env, properties(o) AS org,
           d.url AS url
    """
    env_records = await age.query(q4, project_id=project_id)
    envs: list[dict[str, typing.Any]] = []
    for r in env_records:
        env = r['env']
        env['sort_order'] = env.get('sort_order') or 0
        env['url'] = r.get('url')
        env['organization'] = r.get('org')
        envs.append(env)
    project['environments'] = envs

    # 5. Get dependency URIs
    q5: typing.LiteralString = """
    MATCH (p:Project {id: $project_id})-[:DEPENDS_ON]->(dep:Project)
          -[:OWNED_BY]->(:Team)
          -[:BELONGS_TO]->(depOrg:Organization)
    WHERE dep.id IS NOT NULL
    RETURN dep.id AS dep_id, depOrg.slug AS dep_org_slug
    """
    dep_records = await age.query(q5, project_id=project_id)
    project['dependency_uris'] = [
        f'/organizations/{r["dep_org_slug"]}/projects/{r["dep_id"]}'
        for r in dep_records
    ]

    return project


# -- Endpoints ----------------------------------------------------------


@projects_router.post('/', status_code=201)
async def create_project(
    org_slug: str,
    data: ProjectCreate,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('project:create'),
        ),
    ],
) -> ProjectResponse:
    """Create a new project in an organization."""
    dynamic_model = await blueprints.get_model(
        models.Project,
        context={'project_type': data.project_type_slugs},
    )

    project_id = nanoid.generate()

    try:
        project = dynamic_model(
            id=project_id,
            team=models.Team(
                name='',
                slug=data.team_slug,
                organization=models.Organization(
                    name='',
                    slug=org_slug,
                ),
            ),
            project_types=[],
            environments=[],
            name=data.name,
            slug=data.slug,
            description=data.description,
            icon=data.icon,
            links=data.links,
            identifiers=data.identifiers,
            **{
                k: v
                for k, v in (data.model_extra or {}).items()
                if k not in _RESERVED_FIELDS
            },
        )
    except pydantic.ValidationError as e:
        LOGGER.warning(
            'Validation error creating project: %s',
            e,
        )
        raise fastapi.HTTPException(
            status_code=400,
            detail=f'Validation error: {e.errors()}',
        ) from e

    now = datetime.datetime.now(datetime.UTC)
    project.created_at = now
    project.updated_at = now
    props = project.model_dump(
        exclude={
            'team',
            'project_types',
            'environments',
        },
    )

    # Pre-validate that all project type slugs exist before creating
    # anything, to avoid orphaned Project nodes when slugs are invalid.
    validate_query: typing.LiteralString = """
    MATCH (o:Organization {slug: $org_slug})
    UNWIND $pt_slugs AS pt_slug
    OPTIONAL MATCH (pt:ProjectType {slug: pt_slug})
             -[:BELONGS_TO]->(o)
    RETURN pt_slug, pt IS NOT NULL AS found
    """
    validation = await age.query(
        validate_query,
        org_slug=org_slug,
        pt_slugs=data.project_type_slugs,
    )
    missing = [r['pt_slug'] for r in validation if not r['found']]
    if missing:
        raise fastapi.HTTPException(
            status_code=422,
            detail=(f'Project type slug(s) not found: {sorted(missing)!r}'),
        )

    # Step 1: Create the project node with explicit properties
    prop_keys = sorted(props.keys())
    prop_str = ', '.join(f'{k}: ${k}' for k in prop_keys)
    create_query: str = f"""
    MATCH (o:Organization {{slug: $org_slug}})
    MATCH (t:Team {{slug: $team_slug}})
          -[:BELONGS_TO]->(o)
    CREATE (p:Project {{{prop_str}}})
    CREATE (p)-[:OWNED_BY]->(t)
    RETURN p.id AS project_id
    """
    try:
        records = await age.query(
            create_query,
            org_slug=org_slug,
            team_slug=data.team_slug,
            **props,
        )
    except exceptions.ConstraintError as e:
        raise fastapi.HTTPException(
            status_code=409,
            detail=(f'Project with id {project_id!r} already exists'),
        ) from e

    if not records:
        raise fastapi.HTTPException(
            status_code=404,
            detail=(
                f'Organization {org_slug!r} or team'
                f' {data.team_slug!r} not found'
            ),
        )

    # Step 2: Create TYPE relationships (one at a time)
    for pt_slug in data.project_type_slugs:
        type_q: typing.LiteralString = """
        MATCH (p:Project {id: $project_id})
        MATCH (pt:ProjectType {slug: $pt_slug})
              -[:BELONGS_TO]->(:Organization {slug: $org_slug})
        CREATE (p)-[:TYPE]->(pt)
        RETURN pt.slug AS slug
        """
        await age.query(
            type_q,
            project_id=project_id,
            pt_slug=pt_slug,
            org_slug=org_slug,
        )

    # Step 3: Create DEPLOYED_IN relationships (one at a time)
    for env_slug, env_url in data.environments.items():
        env_q: typing.LiteralString = """
        MATCH (p:Project {id: $project_id})
        MATCH (e:Environment {slug: $env_slug})
              -[:BELONGS_TO]->(:Organization {slug: $org_slug})
        CREATE (p)-[:DEPLOYED_IN {url: $env_url}]->(e)
        RETURN e.slug AS slug
        """
        await age.query(
            env_q,
            project_id=project_id,
            env_slug=env_slug,
            env_url=env_url or '',
            org_slug=org_slug,
        )

    # Step 4: Fetch full response
    result = await _fetch_project_details(project_id, org_slug)
    if not result:
        raise fastapi.HTTPException(
            status_code=500,
            detail='Project created but could not be retrieved',
        )
    result = _add_relationships(result, org_slug)
    return ProjectResponse.model_validate(result)


@projects_router.get('/')
async def list_projects(
    org_slug: str,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('project:read'),
        ),
    ],
    project_type: str | None = None,
) -> list[ProjectResponse]:
    """List all projects, optionally filtered by type."""
    # Fetch all project nodes in one query
    if project_type:
        base_q: typing.LiteralString = """
        MATCH (p:Project)-[:OWNED_BY]->(:Team)
              -[:BELONGS_TO]->(o:Organization {slug: $org_slug})
        MATCH (p)-[:TYPE]->(filter_pt:ProjectType {slug: $project_type})
        RETURN properties(p) AS project
        ORDER BY p.name
        """
    else:
        base_q = """
        MATCH (p:Project)-[:OWNED_BY]->(:Team)
              -[:BELONGS_TO]->(o:Organization {slug: $org_slug})
        RETURN properties(p) AS project
        ORDER BY p.name
        """
    proj_records = await age.query(
        base_q, org_slug=org_slug, project_type=project_type
    )
    if not proj_records:
        return []

    project_ids = [r['project']['id'] for r in proj_records]

    # Batch fetch teams for all projects
    teams_q: typing.LiteralString = """
    MATCH (p:Project)-[:OWNED_BY]->(t:Team)
          -[:BELONGS_TO]->(o:Organization {slug: $org_slug})
    WHERE p.id IN $ids
    RETURN p.id AS pid, properties(t) AS team, properties(o) AS org
    """
    team_records = await age.query(teams_q, org_slug=org_slug, ids=project_ids)
    teams_by_id: dict[str, dict[str, typing.Any]] = {}
    for r in team_records:
        team = r['team']
        team['organization'] = r.get('org')
        teams_by_id[r['pid']] = team

    # Batch fetch project types
    pts_q: typing.LiteralString = """
    MATCH (p:Project)-[:TYPE]->(pt:ProjectType)
          -[:BELONGS_TO]->(o:Organization {slug: $org_slug})
    WHERE p.id IN $ids
    RETURN p.id AS pid, properties(pt) AS pt, properties(o) AS org
    """
    pt_records = await age.query(pts_q, org_slug=org_slug, ids=project_ids)
    pts_by_id: dict[str, list[dict[str, typing.Any]]] = {}
    for r in pt_records:
        pt = r['pt']
        pt['organization'] = r.get('org')
        pts_by_id.setdefault(r['pid'], []).append(pt)

    # Batch fetch environments
    envs_q: typing.LiteralString = """
    MATCH (p:Project)-[d:DEPLOYED_IN]->(env:Environment)
          -[:BELONGS_TO]->(o:Organization {slug: $org_slug})
    WHERE p.id IN $ids
    RETURN p.id AS pid, properties(env) AS env,
           properties(o) AS org, d.url AS url
    """
    env_records = await age.query(envs_q, org_slug=org_slug, ids=project_ids)
    envs_by_id: dict[str, list[dict[str, typing.Any]]] = {}
    for r in env_records:
        env = r['env']
        env['sort_order'] = env.get('sort_order') or 0
        env['url'] = r.get('url')
        env['organization'] = r.get('org')
        envs_by_id.setdefault(r['pid'], []).append(env)

    # Batch fetch dependencies
    deps_q: typing.LiteralString = """
    MATCH (p:Project)-[:DEPENDS_ON]->(dep:Project)
          -[:OWNED_BY]->(:Team)
          -[:BELONGS_TO]->(depOrg:Organization)
    WHERE p.id IN $ids AND dep.id IS NOT NULL
    RETURN p.id AS pid, dep.id AS dep_id,
           depOrg.slug AS dep_org_slug
    """
    dep_records = await age.query(deps_q, ids=project_ids)
    deps_by_id: dict[str, list[str]] = {}
    for r in dep_records:
        uri = f'/organizations/{r["dep_org_slug"]}/projects/{r["dep_id"]}'
        deps_by_id.setdefault(r['pid'], []).append(uri)

    # Assemble results
    results: list[ProjectResponse] = []
    for record in proj_records:
        proj = record['project']
        pid = proj['id']
        proj['team'] = teams_by_id.get(pid)
        proj['project_types'] = pts_by_id.get(pid, [])
        proj['environments'] = envs_by_id.get(pid, [])
        proj['dependency_uris'] = deps_by_id.get(pid, [])
        dep_count = len(proj['dependency_uris'])
        proj = _add_relationships(proj, org_slug, dep_count)
        results.append(ProjectResponse.model_validate(proj))
    return results


class BlueprintSectionProperty(pydantic.BaseModel):
    """A single property from a blueprint's JSON Schema."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True, serialize_by_alias=True
    )

    type: str | None = None
    format: str | None = None
    title: str | None = None
    description: str | None = None
    enum: list[str] | None = None
    default: typing.Any = None
    minimum: float | None = None
    maximum: float | None = None
    x_ui: dict[str, typing.Any] | None = pydantic.Field(
        None, alias='x-ui', serialization_alias='x-ui'
    )


class BlueprintSection(pydantic.BaseModel):
    """One blueprint's contribution to the project schema."""

    name: str
    slug: str
    description: str | None = None
    properties: dict[str, BlueprintSectionProperty]


class ProjectSchemaResponse(pydantic.BaseModel):
    """Fully resolved, blueprint-grouped schema for a project."""

    sections: list[BlueprintSection]


@projects_router.get('/{project_id}/schema')
async def get_project_schema(
    org_slug: str,
    project_id: str,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('project:read'),
        ),
    ],
) -> ProjectSchemaResponse:
    """Return the merged blueprint schema for a specific project.

    Resolves the project's own types and environments, matches every
    applicable blueprint, and returns the properties grouped by
    blueprint so the UI can render labelled sections.
    """
    # Verify the project exists in this organization
    exists_q: typing.LiteralString = """
    MATCH (p:Project {id: $project_id})
          -[:OWNED_BY]->(:Team)
          -[:BELONGS_TO]->(o:Organization {slug: $org_slug})
    RETURN p.id AS id
    """
    exists = await age.query(
        exists_q,
        project_id=project_id,
        org_slug=org_slug,
    )
    if not exists:
        raise fastapi.HTTPException(
            status_code=404,
            detail=f'Project {project_id!r} not found',
        )

    # Fetch the project's type slugs
    type_q: typing.LiteralString = """
    MATCH (p:Project {id: $project_id})-[:TYPE]->(pt:ProjectType)
    RETURN pt.slug AS slug
    """
    type_records = await age.query(type_q, project_id=project_id)
    type_slugs: set[str] = {r['slug'] for r in type_records}

    # Fetch the project's environment slugs
    env_q: typing.LiteralString = """
    MATCH (p:Project {id: $project_id})
          -[:DEPLOYED_IN]->(env:Environment)
    RETURN env.slug AS slug
    """
    env_records = await age.query(env_q, project_id=project_id)
    env_slugs: set[str] = {r['slug'] for r in env_records}

    # Fetch all enabled Project blueprints ordered by priority
    all_blueprints: list[models.Blueprint] = []
    async for bp in age.fetch_nodes(
        models.Blueprint,
        {'type': 'Project', 'enabled': True},
        order_by='priority',
    ):
        all_blueprints.append(bp)

    # Match blueprints whose filters intersect the project's own types/envs.
    # A blueprint with no filter matches everything.
    # A blueprint with a project_type filter matches if any of the project's
    # types appear in that list (same for environment).
    sections: list[BlueprintSection] = []
    for bp in all_blueprints:
        f = bp.filter
        if f is not None:
            if f.project_type and not type_slugs.intersection(f.project_type):
                continue
            if f.environment and not env_slugs.intersection(f.environment):
                continue

        schema = bp.json_schema
        if not schema.properties:
            continue

        props: dict[str, BlueprintSectionProperty] = {}
        for prop_name, prop_schema in schema.properties.items():
            x_ui = (
                prop_schema.model_extra.get('x-ui')
                if prop_schema.model_extra
                else None
            )
            props[prop_name] = BlueprintSectionProperty(
                type=getattr(prop_schema, 'type', None),
                format=getattr(prop_schema, 'format', None),
                title=getattr(prop_schema, 'title', None),
                description=getattr(prop_schema, 'description', None),
                enum=getattr(prop_schema, 'enum', None),
                default=getattr(prop_schema, 'default', None),
                minimum=getattr(prop_schema, 'minimum', None),
                maximum=getattr(prop_schema, 'maximum', None),
                **{'x-ui': x_ui},
            )

        sections.append(
            BlueprintSection(
                name=bp.name,
                slug=bp.slug or '',
                description=bp.description,
                properties=props,
            )
        )

    return ProjectSchemaResponse(sections=sections)


@projects_router.get('/{project_id}')
async def get_project(
    org_slug: str,
    project_id: str,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('project:read'),
        ),
    ],
) -> ProjectResponse:
    """Get a project by ID."""
    project = await _fetch_project_details(project_id, org_slug)
    if not project:
        raise fastapi.HTTPException(
            status_code=404,
            detail=f'Project {project_id!r} not found',
        )
    dep_count = len(project.get('dependency_uris', []))
    result = _add_relationships(project, org_slug, dep_count)
    return ProjectResponse.model_validate(result)


@projects_router.put('/{project_id}')
async def update_project(
    org_slug: str,
    project_id: str,
    data: ProjectUpdate,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('project:write'),
        ),
    ],
) -> ProjectResponse:
    """Update a project."""
    # Fetch existing project properties
    fetch_q: typing.LiteralString = """
    MATCH (p:Project {id: $project_id})
          -[:OWNED_BY]->(:Team)
          -[:BELONGS_TO]->(o:Organization {slug: $org_slug})
    RETURN properties(p) AS project
    """
    records = await age.query(
        fetch_q,
        project_id=project_id,
        org_slug=org_slug,
    )
    if not records:
        raise fastapi.HTTPException(
            status_code=404,
            detail=f'Project {project_id!r} not found',
        )
    existing = records[0]['project']

    # Fetch current team slug
    team_q: typing.LiteralString = """
    MATCH (p:Project {id: $project_id})-[:OWNED_BY]->(t:Team)
    RETURN t.slug AS team_slug
    """
    team_recs = await age.query(team_q, project_id=project_id)
    current_team = team_recs[0]['team_slug'] if team_recs else ''

    # Fetch current type slugs
    type_q: typing.LiteralString = """
    MATCH (p:Project {id: $project_id})-[:TYPE]->(pt:ProjectType)
    RETURN pt.slug AS slug
    """
    type_recs = await age.query(type_q, project_id=project_id)
    current_types = [r['slug'] for r in type_recs]

    effective_team = data.team_slug or current_team
    effective_types = data.project_type_slugs or current_types

    dynamic_model = await blueprints.get_model(
        models.Project,
        context={'project_type': effective_types},
    )

    # Merge provided fields with existing values
    merged = {
        'name': data.name or existing.get('name', ''),
        'slug': data.slug or existing.get('slug', ''),
        'description': (
            data.description
            if data.description is not None
            else existing.get('description')
        ),
        'icon': (data.icon if data.icon is not None else existing.get('icon')),
        'links': (
            data.links if data.links is not None else existing.get('links', {})
        ),
        'identifiers': (
            data.identifiers
            if data.identifiers is not None
            else existing.get('identifiers', {})
        ),
    }

    # Merge blueprint extra fields
    base_fields = set(ProjectUpdate.model_fields)
    skip = {
        'id',
        'team',
        'project_types',
        'environments',
        'created_at',
        'updated_at',
    }
    extra_fields = {
        k: v
        for k, v in existing.items()
        if k not in base_fields and k not in skip
    }
    extra_fields.update(
        {
            k: v
            for k, v in (data.model_extra or {}).items()
            if k not in _RESERVED_FIELDS
        }
    )

    try:
        project = dynamic_model(
            id=project_id,
            team=models.Team(
                name='',
                slug=effective_team,
                organization=models.Organization(
                    name='',
                    slug=org_slug,
                ),
            ),
            project_types=[],
            environments=[],
            **merged,  # type: ignore[arg-type]
            **extra_fields,
        )
    except pydantic.ValidationError as e:
        LOGGER.warning(
            'Validation error updating project: %s',
            e,
        )
        raise fastapi.HTTPException(
            status_code=400,
            detail=f'Validation error: {e.errors()}',
        ) from e

    project.created_at = existing.get('created_at')
    project.updated_at = datetime.datetime.now(datetime.UTC)
    props = project.model_dump(
        exclude={
            'team',
            'project_types',
            'environments',
        },
    )

    # Pre-validate team slug exists before executing the update to
    # prevent partial writes (SET p = $props commits even when a
    # subsequent strict MATCH on the team returns 0 rows).
    if data.team_slug:
        team_check: typing.LiteralString = """
        MATCH (t:Team {slug: $team_slug})
              -[:BELONGS_TO]->(o:Organization {slug: $org_slug})
        RETURN t.slug AS slug
        """
        team_records = await age.query(
            team_check,
            team_slug=data.team_slug,
            org_slug=org_slug,
        )
        if not team_records:
            raise fastapi.HTTPException(
                status_code=422,
                detail=(
                    f'Team {data.team_slug!r} not found in'
                    f' organization {org_slug!r}'
                ),
            )

    # Pre-validate that all project type slugs exist to avoid
    # silently deleting existing TYPE edges with no replacements.
    if data.project_type_slugs is not None:
        pt_check: typing.LiteralString = """
        MATCH (o:Organization {slug: $org_slug})
        UNWIND $pt_slugs AS pt_slug
        OPTIONAL MATCH (pt:ProjectType {slug: pt_slug})
                 -[:BELONGS_TO]->(o)
        RETURN pt_slug, pt IS NOT NULL AS found
        """
        pt_records = await age.query(
            pt_check,
            org_slug=org_slug,
            pt_slugs=data.project_type_slugs,
        )
        missing = [r['pt_slug'] for r in pt_records if not r['found']]
        if missing:
            raise fastapi.HTTPException(
                status_code=422,
                detail=(
                    f'Project type slug(s) not found: {sorted(missing)!r}'
                ),
            )

    # Step 1: Update project node properties with individual SETs
    prop_keys = sorted(props.keys())
    set_clauses = ', '.join(f'p.{k} = ${k}' for k in prop_keys)
    update_q: str = f"""
    MATCH (p:Project {{id: $project_id}})
    SET {set_clauses}
    RETURN p.id AS id
    """
    try:
        updated = await age.query(
            update_q,
            project_id=project_id,
            **props,
        )
    except exceptions.ConstraintError as e:
        raise fastapi.HTTPException(
            status_code=409,
            detail=str(e),
        ) from e

    if not updated:
        raise fastapi.HTTPException(
            status_code=404,
            detail=f'Project {project_id!r} not found',
        )

    # Step 2: Update team ownership if changed
    if data.team_slug:
        del_own_q: typing.LiteralString = """
        MATCH (p:Project {id: $project_id})
              -[old_own:OWNED_BY]->(:Team)
        DELETE old_own
        RETURN p.id AS id
        """
        await age.query(del_own_q, project_id=project_id)
        new_own_q: typing.LiteralString = """
        MATCH (p:Project {id: $project_id})
        MATCH (new_t:Team {slug: $new_team_slug})
              -[:BELONGS_TO]->(:Organization {slug: $org_slug})
        CREATE (p)-[:OWNED_BY]->(new_t)
        RETURN new_t.slug AS slug
        """
        await age.query(
            new_own_q,
            project_id=project_id,
            new_team_slug=data.team_slug,
            org_slug=org_slug,
        )

    # Step 3: Update TYPE relationships if changed
    if data.project_type_slugs is not None:
        del_type_q: typing.LiteralString = """
        MATCH (p:Project {id: $project_id})
              -[old_type:TYPE]->(:ProjectType)
        DELETE old_type
        RETURN p.id AS id
        """
        await age.query(del_type_q, project_id=project_id)
        for pt_slug in data.project_type_slugs:
            new_type_q: typing.LiteralString = """
            MATCH (p:Project {id: $project_id})
            MATCH (pt:ProjectType {slug: $pt_slug})
                  -[:BELONGS_TO]->(
                      :Organization {slug: $org_slug})
            CREATE (p)-[:TYPE]->(pt)
            RETURN pt.slug AS slug
            """
            await age.query(
                new_type_q,
                project_id=project_id,
                pt_slug=pt_slug,
                org_slug=org_slug,
            )

    # Step 4: Update DEPLOYED_IN relationships if changed
    if data.environments is not None:
        del_env_q: typing.LiteralString = """
        MATCH (p:Project {id: $project_id})
              -[old_env:DEPLOYED_IN]->(:Environment)
        DELETE old_env
        RETURN p.id AS id
        """
        await age.query(del_env_q, project_id=project_id)
        for env_slug, env_url in data.environments.items():
            new_env_q: typing.LiteralString = """
            MATCH (p:Project {id: $project_id})
            MATCH (e:Environment {slug: $env_slug})
                  -[:BELONGS_TO]->(
                      :Organization {slug: $org_slug})
            CREATE (p)-[:DEPLOYED_IN {url: $env_url}]->(e)
            RETURN e.slug AS slug
            """
            await age.query(
                new_env_q,
                project_id=project_id,
                env_slug=env_slug,
                env_url=env_url or '',
                org_slug=org_slug,
            )

    # Step 5: Fetch full response
    result = await _fetch_project_details(project_id, org_slug)
    if not result:
        raise fastapi.HTTPException(
            status_code=404,
            detail=f'Project {project_id!r} not found',
        )
    dep_count = len(result.get('dependency_uris', []))
    result = _add_relationships(result, org_slug, dep_count)
    return ProjectResponse.model_validate(result)


@projects_router.delete('/{project_id}', status_code=204)
async def delete_project(
    org_slug: str,
    project_id: str,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('project:delete'),
        ),
    ],
) -> None:
    """Delete a project."""
    query: typing.LiteralString = """
    MATCH (p:Project {id: $project_id})
          -[:OWNED_BY]->(:Team)
          -[:BELONGS_TO]->(:Organization {slug: $org_slug})
    DETACH DELETE p
    RETURN count(p) AS deleted
    """
    records = await age.query(
        query,
        project_id=project_id,
        org_slug=org_slug,
    )

    if not records or records[0].get('deleted', 0) == 0:
        raise fastapi.HTTPException(
            status_code=404,
            detail=f'Project {project_id!r} not found',
        )
