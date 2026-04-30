"""Tests for the OAuth provider repository."""

from __future__ import annotations

import typing
import unittest
from unittest import mock

from imbi_api.auth import providers as oauth_providers
from imbi_api.domain import models
from imbi_api.endpoints import oauth_providers as endpoint


class _FakeEncryptor:
    def encrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        return f'enc:{value}'

    def decrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        return value.removeprefix('enc:')


def _patch_encryptor() -> typing.Any:
    return mock.patch(
        'imbi_common.auth.encryption.TokenEncryption.get_instance',
        return_value=_FakeEncryptor(),
    )


class _FakeDB:
    """Minimal in-memory stand-in for ``graph.Graph``."""

    def __init__(self) -> None:
        self.rows: dict[str, models.OAuthProvider] = {}

    async def match(
        self,
        model: typing.Any,
        criteria: dict[str, typing.Any] | None = None,
        order_by: str | None = None,
    ) -> list[models.OAuthProvider]:
        rows = list(self.rows.values())
        if criteria:
            for key, value in criteria.items():
                rows = [r for r in rows if getattr(r, key) == value]
        if order_by:
            rows.sort(key=lambda r: getattr(r, order_by))
        return rows

    async def merge(
        self,
        node: models.OAuthProvider,
        match_on: list[str] | None = None,
    ) -> None:
        self.rows[node.slug] = node

    async def execute(
        self,
        query: typing.Any,
        params: dict[str, typing.Any],
        columns: list[str] | None = None,
    ) -> list[dict[str, typing.Any]]:
        slug = params.get('slug')
        existed = slug in self.rows
        if existed:
            del self.rows[slug]
        return [{'deleted': 1 if existed else 0}]


class ProviderRepoTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        oauth_providers._provider_cache.clear()
        oauth_providers._list_cache.clear()
        self.db = _FakeDB()

    async def test_upsert_encrypts_secret(self) -> None:
        provider = models.OAuthProvider(
            slug='google',
            type='google',
            name='Google',
            enabled=True,
            client_id='cid',
        )
        with _patch_encryptor():
            saved = await oauth_providers.upsert_provider(
                self.db,  # type: ignore[arg-type]
                provider,
                secret_plaintext='shh',
            )
        self.assertEqual(saved.client_secret_encrypted, 'enc:shh')
        # Ensure persisted row carries the encrypted form
        self.assertEqual(
            self.db.rows['google'].client_secret_encrypted, 'enc:shh'
        )

    async def test_upsert_preserves_secret_when_none(self) -> None:
        existing = models.OAuthProvider(
            slug='google',
            type='google',
            name='Google',
            enabled=True,
            client_secret_encrypted='enc:keep',
        )
        with _patch_encryptor():
            saved = await oauth_providers.upsert_provider(
                self.db,  # type: ignore[arg-type]
                existing,
                secret_plaintext=None,
            )
        self.assertEqual(saved.client_secret_encrypted, 'enc:keep')

    async def test_get_provider_uses_cache(self) -> None:
        self.db.rows['google'] = models.OAuthProvider(
            slug='google', type='google', name='Google', enabled=True
        )
        first = await oauth_providers.get_provider(self.db, 'google')  # type: ignore[arg-type]
        # Mutate underlying store; cache should still return original
        self.db.rows['google'] = models.OAuthProvider(
            slug='google',
            type='google',
            name='Renamed',
            enabled=True,
        )
        second = await oauth_providers.get_provider(self.db, 'google')  # type: ignore[arg-type]
        self.assertEqual(first.name, 'Google')
        self.assertEqual(second.name, 'Google')

    async def test_upsert_invalidates_cache(self) -> None:
        self.db.rows['google'] = models.OAuthProvider(
            slug='google', type='google', name='Google', enabled=True
        )
        await oauth_providers.get_provider(self.db, 'google')  # type: ignore[arg-type]
        # Upsert with a different name should clear the cached entry.
        with _patch_encryptor():
            await oauth_providers.upsert_provider(
                self.db,  # type: ignore[arg-type]
                models.OAuthProvider(
                    slug='google',
                    type='google',
                    name='Renamed',
                    enabled=True,
                ),
                secret_plaintext=None,
            )
        fresh = await oauth_providers.get_provider(self.db, 'google')  # type: ignore[arg-type]
        self.assertEqual(fresh.name, 'Renamed')

    async def test_list_providers_filters_enabled(self) -> None:
        self.db.rows['google'] = models.OAuthProvider(
            slug='google', type='google', name='Google', enabled=True
        )
        self.db.rows['github'] = models.OAuthProvider(
            slug='github', type='github', name='GitHub', enabled=False
        )
        all_rows = await oauth_providers.list_providers(
            self.db  # pyright: ignore[reportArgumentType]
        )
        enabled_only = await oauth_providers.list_providers(
            self.db,  # pyright: ignore[reportArgumentType]
            enabled_only=True,
        )
        self.assertEqual(len(all_rows), 2)
        self.assertEqual(len(enabled_only), 1)
        self.assertEqual(enabled_only[0].slug, 'google')

    async def test_delete_provider(self) -> None:
        self.db.rows['google'] = models.OAuthProvider(
            slug='google', type='google', name='Google', enabled=True
        )
        deleted = await oauth_providers.delete_provider(
            self.db,  # pyright: ignore[reportArgumentType]
            'google',
        )
        self.assertTrue(deleted)
        not_deleted = await oauth_providers.delete_provider(
            self.db,  # pyright: ignore[reportArgumentType]
            'google',
        )
        self.assertFalse(not_deleted)


class OAuthProviderReadModelTestCase(unittest.TestCase):
    def test_response_model_omits_secret(self) -> None:
        provider = models.OAuthProvider(
            slug='google',
            type='google',
            name='Google',
            enabled=True,
            client_id='cid',
            client_secret_encrypted='enc:shh',
        )
        read = endpoint.OAuthProviderRead.from_model(provider)
        dumped = read.model_dump()
        self.assertNotIn('client_secret', dumped)
        self.assertNotIn('client_secret_encrypted', dumped)
        self.assertTrue(dumped['has_secret'])

    def test_response_model_has_secret_false_when_empty(self) -> None:
        provider = models.OAuthProvider(
            slug='github',
            type='github',
            name='GitHub',
            enabled=False,
        )
        read = endpoint.OAuthProviderRead.from_model(provider)
        self.assertFalse(read.has_secret)
