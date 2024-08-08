from __future__ import annotations

import re

import jsonpatch
import pydantic

from imbi import errors
from imbi.endpoints import base
from imbi.endpoints.components import models


class CollectionRequestHandler(base.PaginatedCollectionHandler):
    NAME = 'components'
    ITEM_NAME = 'component'
    ID_KEY = 'package_url'
    FIELDS = [
        'package_url', 'name', 'status', 'home_page', 'icon_class',
        'active_version'
    ]
    DEFAULTS = {
        'status': 'Active',
        'icon_class': 'fas fa-save',
        'active_version': None
    }

    COLLECTION_SQL = re.sub(
        r'\s+', ' ', """\
        SELECT c.package_url, c."name", c.status, c.home_page, c.icon_class,
               c.active_version, COUNT(v.id) AS version_count,
               COUNT(p.project_id) AS project_count, c.created_at,
               c.created_by, c.last_modified_at, c.last_modified_by
          FROM v1.components AS c
          LEFT JOIN v1.component_versions AS v ON v.package_url = c.package_url
          LEFT JOIN v1.project_components AS p ON p.version_id = v.id
         WHERE c.package_url > %(starting_package)s
         GROUP BY c.package_url, c."name", c.status, c.home_page, c.icon_class,
                  c.active_version, c.created_at, c.created_by,
                  c.last_modified_at, c.last_modified_by
         ORDER BY c.package_url ASC
         LIMIT %(limit)s
        """)
    GET_SQL = re.sub(
        r'\s+', ' ', """\
        SELECT package_url, "name", status, home_page, icon_class,
               active_version, created_at, created_by,
               last_modified_at, last_modified_by
          FROM v1.components
         WHERE package_url = %(package_url)s
        """)
    POST_SQL = re.sub(
        r'\s+', ' ', """\
        INSERT INTO v1.components
                    (package_url, "name", status, home_page,
                     active_version, icon_class, created_by)
             VALUES (%(package_url)s, %(name)s, %(status)s, %(home_page)s,
                     %(active_version)s, %(icon_class)s, %(username)s)
          RETURNING *
        """)

    def get_pagination_token_from_request(self) -> models.ComponentToken:
        return models.ComponentToken.from_request(self.request)

    @base.require_permission('admin')
    async def post(self, *args, **kwargs) -> None:
        await super().post(*args, **kwargs)


class RecordRequestHandler(base.CRUDRequestHandler):
    NAME = 'component'
    ID_KEY = 'package_url'
    GET_SQL = re.sub(
        r'\s+', ' ', """\
        SELECT package_url, "name", status, home_page, icon_class,
               active_version, created_at, created_by,
               last_modified_at, last_modified_by
          FROM v1.components
         WHERE package_url = %(package_url)s
        """)
    DELETE_SQL = re.sub(
        r'\s+', ' ', """\
        DELETE FROM v1.components WHERE package_url = %(package_url)s
        """)
    PATCH_SQL = re.sub(
        r'\s+', ' ', """\
        UPDATE v1.components
           SET package_url = %(package_url)s,
               "name" = %(name)s,
               status = %(status)s,
               home_page = %(home_page)s,
               icon_class = %(icon_class)s,
               active_version = %(active_version)s,
               last_modified_at = CURRENT_TIMESTAMP,
               last_modified_by = %(username)s
         WHERE package_url = %(current_package_url)s
        """)

    @base.require_permission('admin')
    async def delete(self, *args, **kwargs):
        await super().delete(*args, **kwargs)

    @base.require_permission('admin')
    async def patch(self, *args, **kwargs):
        result = await self.postgres_execute(
            self.GET_SQL, {'package_url': kwargs['package_url']},
            f'get-{self.NAME}')
        if not result:
            raise errors.ItemNotFound(instance=self.request.uri)

        original = models.Component.model_validate(result.row)
        original_dict = original.model_dump()
        patch = jsonpatch.JsonPatch(self.get_request_body())
        updated_dict = patch.apply(original_dict)
        if all(original_dict[k] == updated_dict[k] for k in updated_dict):
            self._add_self_link(self.request.path)
            self._add_link_header()
            self.set_status(304)
            return

        try:
            updated = models.Component.model_validate(updated_dict)
        except pydantic.ValidationError as error:
            all_errors = error.errors(include_context=False)
            raise errors.BadRequest(
                'failed to validate patched version: %s',
                str(error).replace('\n', ';'),
                title='Invalid Component generated by update',
                detail=all_errors[0]['msg'],
                validation_errors=all_errors) from None

        updated_dict.update({
            'current_package_url': original.package_url,
            'username': self._current_user.username,
        })
        result = await self.postgres_execute(self.PATCH_SQL, updated_dict,
                                             f'patch-{self.NAME}')
        if not result:
            raise errors.DatabaseError('No rows were returned from PATCH_SQL',
                                       title='failed to update record')

        await self._get({'package_url': updated.package_url})


class ProjectComponentsRequestHandler(base.PaginatedCollectionHandler):
    # the status & score columns will become "real" when we figure
    # out how we want to score these in the future
    COLLECTION_SQL = re.sub(
        r'\s+', ' ', """\
        SELECT c.package_url, c."name", v.version, c.icon_class,
               c.status, NULL AS score
          FROM v1.project_components AS p
          JOIN v1.component_versions AS v ON v.id = p.version_id
          JOIN v1.components AS c ON c.package_url = v.package_url
         WHERE c.package_url > %(starting_package)s
           AND p.project_id = %(project_id)s
         ORDER BY c.package_url ASC
        """)

    def get_pagination_token_from_request(
            self, *, project_id: str) -> models.ProjectComponentsToken:
        return models.ProjectComponentsToken.from_request(
            self.request, project_id=project_id)
