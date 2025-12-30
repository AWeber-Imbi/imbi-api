import fastapi

from .blueprints import blueprint_router
from .status import status_router

routers: list[fastapi.APIRouter] = [blueprint_router, status_router]

__all__ = ['routers']
