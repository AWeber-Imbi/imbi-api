"""Credential retrieval for plugin instances."""

import json
import logging
import typing

from imbi_common import graph
from imbi_common.auth.encryption import TokenEncryption
from imbi_common.plugins.errors import (  # type: ignore[import-not-found]
    PluginCredentialsMissing,
)
from imbi_common.plugins.registry import (  # type: ignore[import-not-found]
    RegistryEntry,
)

LOGGER = logging.getLogger(__name__)


async def get_plugin_credentials(
    db: graph.Graph,
    plugin_id: str,
    entry: RegistryEntry,
) -> dict[str, str]:
    """Fetch and decrypt plugin credentials from the graph.

    Traverses Plugin <- HAS_PLUGIN - ThirdPartyService
    -> HAS_APPLICATION -> ServiceApplication and decrypts
    the plugin_credentials field.

    Raises:
        PluginCredentialsMissing: If required credentials are absent.
    """
    query: typing.LiteralString = """
    MATCH (p:Plugin {id: {plugin_id}})
    <-[:HAS_PLUGIN]-(s:ThirdPartyService)
    -[:HAS_APPLICATION]->(a:ServiceApplication)
    RETURN a.plugin_credentials AS creds
    """
    records = await db.execute(
        query,
        {'plugin_id': plugin_id},
        ['creds'],
    )
    if not records or records[0].get('creds') is None:
        creds_raw: str | None = None
    else:
        creds_raw = graph.parse_agtype(records[0]['creds'])

    if creds_raw:
        encryptor = TokenEncryption.get_instance()
        decrypted_str = encryptor.decrypt(creds_raw)
        if decrypted_str:
            credentials: dict[str, str] = json.loads(decrypted_str)
        else:
            credentials = {}
    else:
        credentials = {}

    required_fields = [f for f in entry.manifest.credentials if f.required]
    missing = [f.name for f in required_fields if f.name not in credentials]
    if missing:
        raise PluginCredentialsMissing(
            f'Missing required credentials for plugin '
            f'{entry.manifest.slug!r}: {missing}'
        )
    return credentials
