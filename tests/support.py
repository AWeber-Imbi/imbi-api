"""Shared test helpers.

Building the FastAPI app via :func:`imbi_api.app.create_app` costs
~130 ms (it registers 255 routes), and the only per-test state on the
app is ``dependency_overrides``. Rebuilding it in every ``setUp`` adds
up to minutes across the suite, so share a single instance and reset
the overrides between tests instead.
"""

import functools
import unittest

import fastapi

from imbi_api import app


@functools.cache
def shared_app() -> fastapi.FastAPI:
    """Return a process-wide :class:`fastapi.FastAPI` instance."""
    return app.create_app()


class SharedAppTestCase(unittest.TestCase):
    """Base case that reuses one app and clears overrides per test."""

    test_app: fastapi.FastAPI

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.test_app = shared_app()

    def tearDown(self) -> None:
        self.test_app.dependency_overrides.clear()
        super().tearDown()


class SharedAppAsyncTestCase(unittest.IsolatedAsyncioTestCase):
    """``IsolatedAsyncioTestCase`` variant of :class:`SharedAppTestCase`."""

    test_app: fastapi.FastAPI

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.test_app = shared_app()

    def tearDown(self) -> None:
        self.test_app.dependency_overrides.clear()
        super().tearDown()
