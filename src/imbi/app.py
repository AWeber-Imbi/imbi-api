import asyncio
import contextlib
import logging
import os
import typing

import fastapi

from imbi import clickhouse, endpoints, neo4j, version
from imbi.auth import seed

LOGGER = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def fastapi_lifespan(
    *_args: typing.Any, **_kwargs: typing.Any
) -> typing.AsyncIterator[None]:  # pragma: nocover
    """This is invoked by FastAPI for us to control startup and shutdown."""
    await asyncio.gather(
        clickhouse.initialize(),
        neo4j.initialize(),
    )

    # Auto-seed authentication system if not already seeded
    auto_seed = os.getenv('IMBI_AUTO_SEED_AUTH', 'true').lower() == 'true'
    if auto_seed:
        is_seeded = await seed.check_if_seeded()
        if not is_seeded:
            LOGGER.info('Auto-seeding authentication system...')
            result = await seed.bootstrap_auth_system()
            LOGGER.info(
                'Auto-seed complete: %d permissions, %d roles created',
                result['permissions'],
                result['roles'],
            )
        else:
            LOGGER.debug('Authentication system already seeded')

    LOGGER.debug('Startup complete')
    yield
    await asyncio.gather(
        neo4j.aclose(),
        clickhouse.aclose(),
    )
    LOGGER.debug('Clean shutdown complete')


def create_app() -> fastapi.FastAPI:
    app = fastapi.FastAPI(
        title='Imbi',
        lifespan=fastapi_lifespan,
        version=version,
        redoc_url='/docs',
        docs_url=None,
        license_info={
            'name': 'BSD 3-Clause',
            'url': 'https://github.com/AWeber-Imbi/imbi-api/blob/main/LICENSE',
        },
    )
    for router in endpoints.routers:
        app.include_router(router)
    return app
