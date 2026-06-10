"""Tests for identity attribution: subject -> Imbi user resolution."""

from __future__ import annotations

import unittest
from unittest import mock

from imbi_common.plugins.base import ServicePlugin

from imbi_api.identity import attribution


def _entry(plugin_type: str) -> mock.Mock:
    entry = mock.Mock()
    entry.manifest.plugin_type = plugin_type
    return entry


class IdentityPluginSlugsTests(unittest.TestCase):
    def test_filters_to_identity_plugins(self) -> None:
        plugins = [
            ServicePlugin(slug='github'),
            ServicePlugin(slug='github-commit-sync'),
            ServicePlugin(slug='unknown'),
        ]

        def fake_get(slug: str) -> mock.Mock:
            if slug == 'github':
                return _entry('identity')
            if slug == 'github-commit-sync':
                return _entry('webhook')
            raise attribution.PluginNotFoundError(slug)

        with mock.patch.object(
            attribution, 'get_plugin', side_effect=fake_get
        ):
            self.assertEqual(
                ['github'], attribution.identity_plugin_slugs(plugins)
            )


class MakeUserResolverTests(unittest.IsolatedAsyncioTestCase):
    def test_none_when_no_identity_plugins(self) -> None:
        db = mock.AsyncMock()
        with mock.patch.object(
            attribution, 'identity_plugin_slugs', return_value=[]
        ):
            self.assertIsNone(
                attribution.make_user_resolver(
                    db, [ServicePlugin(slug='github-commit-sync')]
                )
            )

    async def test_resolves_subject_to_email(self) -> None:
        db = mock.AsyncMock()
        db.match.return_value = [mock.Mock(email='alice@example.com')]
        with (
            mock.patch.object(
                attribution,
                'identity_plugin_slugs',
                return_value=['github'],
            ),
            mock.patch.object(
                attribution.identity_repository,
                'find_user_by_subject',
                new=mock.AsyncMock(return_value='user-1'),
            ) as find,
        ):
            resolver = attribution.make_user_resolver(
                db, [ServicePlugin(slug='github')]
            )
            assert resolver is not None
            self.assertEqual('alice@example.com', await resolver('42'))
        find.assert_awaited_once_with(db, 'github', '42')

    async def test_unmatched_subject_returns_none(self) -> None:
        db = mock.AsyncMock()
        with (
            mock.patch.object(
                attribution,
                'identity_plugin_slugs',
                return_value=['github'],
            ),
            mock.patch.object(
                attribution.identity_repository,
                'find_user_by_subject',
                new=mock.AsyncMock(return_value=None),
            ),
        ):
            resolver = attribution.make_user_resolver(
                db, [ServicePlugin(slug='github')]
            )
            assert resolver is not None
            self.assertIsNone(await resolver('99'))
        db.match.assert_not_called()

    async def test_multiple_distinct_users_logs_and_returns_none(
        self,
    ) -> None:
        db = mock.AsyncMock()
        db.match.side_effect = [
            [mock.Mock(email='a@example.com')],
            [mock.Mock(email='b@example.com')],
        ]
        with (
            mock.patch.object(
                attribution,
                'identity_plugin_slugs',
                return_value=['github', 'gitlab'],
            ),
            mock.patch.object(
                attribution.identity_repository,
                'find_user_by_subject',
                new=mock.AsyncMock(side_effect=['user-1', 'user-2']),
            ),
        ):
            resolver = attribution.make_user_resolver(
                db,
                [ServicePlugin(slug='github'), ServicePlugin(slug='gitlab')],
            )
            assert resolver is not None
            with self.assertLogs(attribution.LOGGER, level='ERROR'):
                self.assertIsNone(await resolver('1'))
