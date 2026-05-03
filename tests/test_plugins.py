"""Tests for the plugin infrastructure modules."""

import asyncio
import unittest
from unittest import mock


class CatalogTestCase(unittest.TestCase):
    def test_catalog_loads(self) -> None:
        from imbi_api.plugins.catalog import list_catalog_entries

        with mock.patch(
            'imbi_api.plugins.catalog.list_plugins',
            return_value=[],
        ):
            entries = list_catalog_entries()
        self.assertIsInstance(entries, list)
        self.assertTrue(len(entries) > 0)
        self.assertEqual(entries[0]['package'], 'imbi-plugin-ssm')
        self.assertEqual(entries[0]['status'], 'not_installed')

    def test_catalog_installed_status(self) -> None:
        from imbi_common.plugins.base import (
            ConfigurationPlugin,
            PluginManifest,
        )
        from imbi_common.plugins.registry import RegistryEntry

        from imbi_api.plugins.catalog import list_catalog_entries

        class _FakePlugin(ConfigurationPlugin):
            manifest = PluginManifest(
                slug='ssm',
                name='SSM',
                plugin_type='configuration',
            )

            async def list_keys(self, ctx, credentials):  # type: ignore[override]
                return []

            async def get_values(self, ctx, credentials, keys=None):  # type: ignore[override]
                return []

            async def set_value(self, ctx, credentials, key, value):  # type: ignore[override]
                raise NotImplementedError

            async def delete_key(self, ctx, credentials, key):  # type: ignore[override]
                pass

        entry = RegistryEntry(
            handler_cls=_FakePlugin,
            manifest=_FakePlugin.manifest,
            package_name='imbi-plugin-ssm',
            package_version='1.0.0',
        )
        with mock.patch(
            'imbi_api.plugins.catalog.list_plugins',
            return_value=[entry],
        ):
            entries = list_catalog_entries()
        ssm = next(e for e in entries if e['package'] == 'imbi-plugin-ssm')
        self.assertEqual(ssm['status'], 'installed')


class AssignmentsTestCase(unittest.TestCase):
    def test_validate_one_default_per_tab_ok(self) -> None:
        from imbi_api.plugins.assignments import (
            PluginAssignmentRow,
            validate_one_default_per_tab,
        )

        rows: list[PluginAssignmentRow] = [
            PluginAssignmentRow(
                plugin_id='p1',
                tab='configuration',
                default=True,
                options={},
            ),
            PluginAssignmentRow(
                plugin_id='p2',
                tab='logs',
                default=True,
                options={},
            ),
        ]
        validate_one_default_per_tab(rows)

    def test_validate_two_defaults_same_tab_raises(self) -> None:
        from imbi_api.plugins.assignments import (
            PluginAssignmentRow,
            validate_one_default_per_tab,
        )

        rows: list[PluginAssignmentRow] = [
            PluginAssignmentRow(
                plugin_id='p1',
                tab='configuration',
                default=True,
                options={},
            ),
            PluginAssignmentRow(
                plugin_id='p2',
                tab='configuration',
                default=True,
                options={},
            ),
        ]
        with self.assertRaises(ValueError):
            validate_one_default_per_tab(rows)

    def test_validate_no_default_raises(self) -> None:
        from imbi_api.plugins.assignments import (
            PluginAssignmentRow,
            validate_one_default_per_tab,
        )

        rows: list[PluginAssignmentRow] = [
            PluginAssignmentRow(
                plugin_id='p1',
                tab='configuration',
                default=False,
                options={},
            ),
        ]
        with self.assertRaises(ValueError):
            validate_one_default_per_tab(rows)


class LifecycleTestCase(unittest.TestCase):
    def test_get_unavailable_slugs_empty(self) -> None:
        from imbi_api.plugins.lifecycle import get_unavailable_slugs

        result = get_unavailable_slugs()
        self.assertIsInstance(result, list)

    def test_startup_load_plugins_logs(self) -> None:
        from imbi_common.plugins.registry import LoadResult

        from imbi_api.plugins import lifecycle

        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = []

        with (
            mock.patch(
                'imbi_api.plugins.lifecycle.load_plugins',
                return_value=LoadResult(
                    loaded=['ssm'],
                    errors={},
                    skipped=[],
                ),
            ),
            mock.patch(
                'imbi_api.plugins.lifecycle.list_plugins',
                return_value=[],
            ),
        ):
            asyncio.run(lifecycle.startup_load_plugins(mock_db))

    def test_audit_unavailable_handles_error(self) -> None:
        from imbi_api.plugins import lifecycle

        mock_db = mock.AsyncMock()
        mock_db.execute.side_effect = RuntimeError('db error')

        with mock.patch(
            'imbi_api.plugins.lifecycle.list_plugins',
            return_value=[],
        ):
            asyncio.run(lifecycle._audit_unavailable(mock_db))


class ReloadHookTestCase(unittest.TestCase):
    def test_plugin_reload_hook_no_valkey(self) -> None:
        from imbi_api.plugins.reload import plugin_reload_hook

        async def _run() -> None:
            with mock.patch(
                'imbi_api.plugins.reload.valkey.get_client',
                side_effect=RuntimeError('no valkey'),
            ):
                async with plugin_reload_hook(db=None):
                    pass

        asyncio.run(_run())

    def test_plugin_reload_hook_no_db(self) -> None:
        from imbi_api.plugins.reload import plugin_reload_hook

        mock_client = mock.MagicMock()

        async def _run() -> None:
            with mock.patch(
                'imbi_api.plugins.reload.valkey.get_client',
                return_value=mock_client,
            ):
                async with plugin_reload_hook(db=None):
                    pass

        asyncio.run(_run())

    def test_publish_reload(self) -> None:
        from imbi_api.plugins.reload import publish_reload

        mock_client = mock.AsyncMock()

        asyncio.run(publish_reload(mock_client))
        mock_client.publish.assert_called_once_with(
            'imbi:plugins:reload', 'reload'
        )


class InstallerTestCase(unittest.TestCase):
    def test_install_disabled_raises(self) -> None:
        from imbi_api.plugins import installer
        from imbi_api.plugins.installer import InstallError

        with mock.patch.object(installer, '_INSTALL_ENABLED', False):
            with self.assertRaises(InstallError) as ctx:
                asyncio.run(installer.install_package('imbi-plugin-ssm'))
        self.assertIn('disabled', str(ctx.exception))

    def test_install_not_in_catalog_raises(self) -> None:
        from imbi_api.plugins import installer
        from imbi_api.plugins.installer import InstallError

        with self.assertRaises(InstallError) as ctx:
            asyncio.run(installer.install_package('not-in-catalog-pkg'))
        self.assertIn('not in the plugin catalog', str(ctx.exception))
