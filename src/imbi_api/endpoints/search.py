"""Vector similarity search endpoint."""

import typing

import fastapi
import pydantic
from imbi_common import graph

from imbi_api.auth import permissions

search_router = fastapi.APIRouter(tags=['Search'])


class SearchResult(pydantic.BaseModel):
    """A single vector search result."""

    node_label: str
    node_id: str
    attribute: str
    chunk_text: str
    distance: float


@search_router.get('/search')
async def search(
    db: graph.Pool,
    auth: typing.Annotated[
        permissions.AuthContext,
        fastapi.Depends(
            permissions.require_permission('search:read'),
        ),
    ],
    q: typing.Annotated[str, fastapi.Query(min_length=1)],
    node_label: str | None = None,
    attribute: str | None = None,
    model: str = 'text',
    limit: typing.Annotated[int, fastapi.Query(ge=1, le=100)] = 10,
    threshold: float | None = None,
) -> list[SearchResult]:
    """Search nodes by semantic similarity.

    Results are ordered by cosine distance ascending (most similar
    first). ``threshold`` is a distance ceiling: 0.0 = identical,
    2.0 = maximally dissimilar.
    """
    results = await db.search(
        q,
        model_name=model,
        node_label=node_label,
        attribute=attribute,
        limit=limit,
        distance_threshold=threshold,
    )
    return [
        SearchResult(
            node_label=r.node_label,
            node_id=r.node_id,
            attribute=r.attribute,
            chunk_text=r.chunk_text,
            distance=r.distance,
        )
        for r in results
    ]
