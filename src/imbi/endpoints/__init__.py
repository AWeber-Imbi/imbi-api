import fastapi

from .auth import auth_router
from .blueprints import blueprint_router
from .groups import groups_router
from .roles import roles_router
from .status import status_router
from .users import users_router

routers: list[fastapi.APIRouter] = [
    auth_router,
    blueprint_router,
    groups_router,
    roles_router,
    status_router,
    users_router,
]

__all__ = ['routers']
