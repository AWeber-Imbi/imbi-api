import typing

import cypherantic
import pydantic
import slugify
from jsonschema_models.models import Schema

__all__ = [
    'Blueprint',
    'BlueprintAssignment',
    'BlueprintEdge',
    'Environment',
    'Node',
    'Organization',
    'Project',
    'ProjectType',
    'Schema',
    'Team',
]


class Blueprint(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra='ignore')

    name: str
    slug: str | None = None
    type: typing.Literal[
        'Organization', 'Team', 'Environment', 'ProjectType', 'Project'
    ]
    description: str | None = None
    enabled: bool = True
    priority: int = 0
    filter: dict[str, typing.Any] | None = None
    json_schema: Schema
    version: int = 0

    @pydantic.model_validator(mode='after')
    def generate_and_validate_slug(self) -> typing.Self:
        """Generate slug from name if not provided and validate it."""
        if self.slug is None:
            self.slug = slugify.slugify(self.name)

        # Validate slug format
        if not self.slug:
            raise ValueError('Slug cannot be empty')
        if not all(c.isalnum() or c == '-' for c in self.slug):
            raise ValueError(
                'Slug must contain only lowercase letters, '
                'numbers, and hyphens'
            )
        return self

    @pydantic.field_validator('json_schema', mode='before')
    @classmethod
    def validate_json_schema(cls, value: typing.Any) -> Schema:
        if isinstance(value, str):
            return Schema.model_validate_json(value)
        elif isinstance(value, dict):
            return Schema.model_validate(value)
        elif isinstance(value, Schema):
            return value
        raise ValueError('Invalid JSON Schema value')

    @pydantic.field_serializer('json_schema')
    def serialize_json_schema(self, value: Schema) -> str:
        return value.model_dump_json(indent=0)


class BlueprintAssignment(pydantic.BaseModel):
    cypherantic_config: typing.ClassVar[cypherantic.RelationshipConfig] = (
        cypherantic.RelationshipConfig(rel_type='BLUEPRINT')
    )
    priority: int = 0


class BlueprintEdge(typing.NamedTuple):
    node: Blueprint
    properties: BlueprintAssignment


class Node(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra='ignore')

    name: str
    slug: str
    description: str | None = None
    icon_url: pydantic.HttpUrl | None = None


class Organization(Node): ...


class Team(Node):
    member_of: typing.Annotated[
        Organization,
        cypherantic.Relationship(rel_type='MANAGED_BY', direction='OUTGOING'),
    ]


class Environment(Node): ...


class ProjectType(Node): ...


class Project(Node):
    team: typing.Annotated[
        Team,
        cypherantic.Relationship(rel_type='OWNED_BY', direction='OUTGOING'),
    ]
    project_type: typing.Annotated[
        ProjectType,
        cypherantic.Relationship(rel_type='TYPE', direction='OUTGOING'),
    ]
    environments: typing.Annotated[
        list[Environment],
        cypherantic.Relationship(rel_type='DEPLOYED_IN', direction='OUTGOING'),
    ] = []
    links: dict[str, pydantic.HttpUrl]
    urls: dict[str, pydantic.HttpUrl]
    identifiers: dict[str, int | str]
