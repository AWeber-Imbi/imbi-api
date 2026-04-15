"""JSON Patch (RFC 6902) utilities."""

import typing

import fastapi
import jsonpatch
import pydantic

LOGGER = __import__('logging').getLogger(__name__)

READONLY_PATHS: frozenset[str] = frozenset(
    [
        '/created_at',
        '/updated_at',
        '/relationships',
        '/id',
    ]
)


class PatchOperation(pydantic.BaseModel):
    """A single JSON Patch operation (RFC 6902).

    Attributes:
        op: The operation type.
        path: JSON Pointer (RFC 6901) target path.
        value: New value for add/replace/test operations.
        from_: Source path for move/copy operations.

    """

    model_config = pydantic.ConfigDict(populate_by_name=True)

    op: typing.Literal['add', 'remove', 'replace', 'move', 'copy', 'test']
    path: str
    value: typing.Any = None
    from_: str | None = pydantic.Field(None, alias='from')


def apply_patch(
    document: dict[str, typing.Any],
    operations: list[PatchOperation],
    readonly_paths: frozenset[str] = READONLY_PATHS,
) -> dict[str, typing.Any]:
    """Apply a JSON Patch document to a dict.

    Parameters:
        document: Current resource state as JSON-serializable dict.
        operations: Validated patch operations.
        readonly_paths: Paths that cannot be modified. Defaults to
            ``READONLY_PATHS`` (created_at, updated_at, relationships, id).

    Returns:
        A new dict with the patch applied.

    Raises:
        HTTPException 400: Path is read-only or operation is invalid.
        HTTPException 422: A ``test`` operation failed.

    """
    for op in operations:
        path = op.path
        if any(
            path == ro or path.startswith(f'{ro}/') for ro in readonly_paths
        ):
            raise fastapi.HTTPException(
                status_code=400,
                detail=f'Path {path!r} is read-only and cannot be patched',
            )

    ops_list = [
        op.model_dump(by_alias=True, exclude_none=True) for op in operations
    ]

    try:
        result: dict[str, typing.Any] = jsonpatch.apply_patch(
            document, ops_list
        )
    except jsonpatch.JsonPatchTestFailed as e:
        raise fastapi.HTTPException(
            status_code=422,
            detail=f'Patch test operation failed: {e}',
        ) from e
    except (jsonpatch.JsonPatchConflict, jsonpatch.InvalidJsonPatch) as e:
        raise fastapi.HTTPException(
            status_code=400,
            detail=f'Invalid patch: {e}',
        ) from e

    return result
