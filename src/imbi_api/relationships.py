"""Utilities for building hypermedia-style relationship links."""

import typing


def relationship_link(href: str, count: int) -> dict[str, typing.Any]:
    """Build a hypermedia-style relationship link with count."""
    return {'href': href, 'meta': {'count': count}}
