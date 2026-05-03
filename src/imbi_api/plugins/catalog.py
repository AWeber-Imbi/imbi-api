"""Plugin catalog — known packages and install status."""

import logging
import pathlib
import tomllib
import typing

from imbi_common.plugins.registry import (  # type: ignore[import-not-found]
    list_plugins,
)

LOGGER = logging.getLogger(__name__)


class CatalogEntry(typing.TypedDict):
    package: str
    version: str
    slugs: list[str]
    author: str
    description: str
    docs_url: str
    status: typing.Literal['installed', 'not_installed', 'update_available']


def _load_catalog_toml() -> list[dict[str, typing.Any]]:
    catalog_path = pathlib.Path(__file__).parent / 'catalog.toml'
    with catalog_path.open('rb') as fh:
        data = tomllib.load(fh)
    plugins: list[dict[str, typing.Any]] = data.get('plugins', [])
    return plugins


_CATALOG_RAW: list[dict[str, typing.Any]] = _load_catalog_toml()


def allowed_packages() -> frozenset[str]:
    """Return the set of package names declared in the catalog."""
    return frozenset(e['package'] for e in _CATALOG_RAW)


def list_catalog_entries() -> list[CatalogEntry]:
    """Cross-reference catalog entries against the registry."""
    installed = {e.package_name: e for e in list_plugins()}
    result: list[CatalogEntry] = []
    for entry in _CATALOG_RAW:
        pkg = entry['package']
        if pkg in installed:
            status: typing.Literal[
                'installed', 'not_installed', 'update_available'
            ] = 'installed'
        else:
            status = 'not_installed'
        result.append(
            CatalogEntry(
                package=pkg,
                version=entry.get('version', ''),
                slugs=entry.get('slugs', []),
                author=entry.get('author', ''),
                description=entry.get('description', ''),
                docs_url=entry.get('docs_url', ''),
                status=status,
            )
        )
    return result
