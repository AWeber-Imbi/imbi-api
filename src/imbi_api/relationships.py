"""Utilities for building hypermedia-style relationship links."""

import functools

from imbi_common.models import RelationshipLink

from imbi_api import settings


@functools.cache
def api_prefix() -> str:
    """Configured API path prefix (e.g. '/api', or '' when unset)."""
    return settings.ServerConfig().api_prefix


def relationship_link(href: str, count: int) -> RelationshipLink:
    """Build a hypermedia-style relationship link with count."""
    return RelationshipLink(href=href, count=count)


def build_relationships(
    base_url: str,
    links: dict[str, tuple[str, int]],
) -> dict[str, RelationshipLink]:
    """Build a relationships dict from name -> (path_suffix, count)."""
    return {
        name: RelationshipLink(href=f'{base_url}{suffix}', count=count)
        for name, (suffix, count) in links.items()
    }
