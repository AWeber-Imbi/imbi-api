import fastapi

from .blueprints import blueprint_router
from .status import status_router

routers: list[fastapi.APIRouter] = [status_router, blueprint_router]

__all__ = ['routers']
