"""OAuth provider repository.

Reads/writes ``OAuthProvider`` rows in the graph and encrypts
``client_secret`` via the shared ``TokenEncryption`` singleton.

A small in-memory TTL cache keeps the OAuth hot path off the graph
on every callback. Writes invalidate the cache.
"""

from __future__ import annotations

import logging
import time
import typing

from imbi_common import graph
from imbi_common.auth import encryption

from imbi_api.domain import models

LOGGER = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 30.0


_provider_cache: dict[str, tuple[models.OAuthProvider, float]] = {}
_list_cache: dict[bool, tuple[list[models.OAuthProvider], float]] = {}


def _invalidate_cache(slug: str | None = None) -> None:
    """Drop cached entries.

    ``slug=None`` clears everything (used by tests / on full
    rewrites). Otherwise only the affected slug + list views are
    cleared.
    """
    if slug is None:
        _provider_cache.clear()
    else:
        _provider_cache.pop(slug, None)
    _list_cache.clear()


async def list_providers(
    db: graph.Graph,
    *,
    enabled_only: bool = False,
) -> list[models.OAuthProvider]:
    """Return every configured ``OAuthProvider`` row.

    Results are cached for ``_CACHE_TTL_SECONDS`` per ``enabled_only``
    flag.
    """
    cached = _list_cache.get(enabled_only)
    now = time.time()
    if cached is not None and (now - cached[1]) < _CACHE_TTL_SECONDS:
        return list(cached[0])

    if enabled_only:
        providers = await db.match(
            models.OAuthProvider,
            {'enabled': True},
            order_by='slug',
        )
    else:
        providers = await db.match(
            models.OAuthProvider,
            order_by='slug',
        )
    _list_cache[enabled_only] = (list(providers), now)
    return providers


async def get_provider(
    db: graph.Graph, slug: str
) -> models.OAuthProvider | None:
    """Look up a single provider by ``slug``.

    Cached for ``_CACHE_TTL_SECONDS``.
    """
    cached = _provider_cache.get(slug)
    now = time.time()
    if cached is not None and (now - cached[1]) < _CACHE_TTL_SECONDS:
        return cached[0]

    results = await db.match(models.OAuthProvider, {'slug': slug})
    provider = results[0] if results else None
    if provider is not None:
        _provider_cache[slug] = (provider, now)
    return provider


async def upsert_provider(
    db: graph.Graph,
    provider: models.OAuthProvider,
    *,
    secret_plaintext: str | None,
) -> models.OAuthProvider:
    """Create or update a provider row.

    When ``secret_plaintext`` is ``None`` the existing
    ``client_secret_encrypted`` on ``provider`` is preserved (so
    callers can leave the form's secret field blank to keep the
    current secret).  When non-empty the value is encrypted with
    ``TokenEncryption.get_instance()`` before being stored.
    """
    if secret_plaintext is not None:
        encryptor = encryption.TokenEncryption.get_instance()
        encrypted = encryptor.encrypt(secret_plaintext)
        if encrypted is None:
            raise ValueError('Failed to encrypt OAuth client secret')
        provider.client_secret_encrypted = encrypted

    await db.merge(provider, ['slug'])
    _invalidate_cache(provider.slug)
    return provider


async def delete_provider(db: graph.Graph, slug: str) -> bool:
    """Delete a provider row by slug.

    Returns ``True`` if a row was deleted, ``False`` if no row
    matched.
    """
    query: typing.LiteralString = (
        'MATCH (p:OAuthProvider {{slug: {slug}}}) '
        'DETACH DELETE p '
        'RETURN count(p) AS deleted'
    )
    records = await db.execute(query, {'slug': slug}, ['deleted'])
    deleted = 0
    if records:
        raw = graph.parse_agtype(records[0].get('deleted'))
        deleted = int(raw or 0)
    _invalidate_cache(slug)
    return deleted > 0
