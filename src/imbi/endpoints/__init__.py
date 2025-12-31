import fastapi

from .auth import auth_router
from .blueprints import blueprint_router
from .status import status_router

routers: list[fastapi.APIRouter] = [
    auth_router,
    blueprint_router,
    status_router,
]

__all__ = ['routers']
