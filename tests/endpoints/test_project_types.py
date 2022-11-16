import json
import uuid

import jsonpatch

from imbi.endpoints import project_types
from tests import base


class AsyncHTTPTestCase(base.TestCaseWithReset):

    ADMIN_ACCESS = True
    TRUNCATE_TABLES = ['v1.project_types']

    def test_project_type_lifecycle(self):
        record = {
            field: project_types.CollectionRequestHandler.DEFAULTS.get(
                field, None)
            for field in project_types.CollectionRequestHandler.FIELDS
            if field != project_types.CollectionRequestHandler.ID_KEY
        }
        record.update({
            'name': str(uuid.uuid4()),
            'plural_name': str(uuid.uuid4()),
            'slug': str(uuid.uuid4()),
            'description': str(uuid.uuid4()),
            'icon_class': 'fas fa-blind',
            'environment_urls': False,
            'gitlab_project_prefix': 'foo'
        })

        # Create
        result = self.fetch('/project-types', method='POST', json_body=record)
        self.assertEqual(result.code, 200)
        response = json.loads(result.body.decode('utf-8'))
        url = self.get_url('/project-types/{}'.format(response['id']))
        self.assert_link_header_equals(result, url)
        self.assertIsNotNone(result.headers['Date'])
        self.assertIsNone(result.headers.get('Last-Modified', None))
        self.assertEqual(
            result.headers['Cache-Control'], 'public, max-age={}'.format(
                project_types.RecordRequestHandler.TTL))
        record.update({
            'id': response['id'],
            'created_by': self.USERNAME[self.ADMIN_ACCESS],
            'last_modified_by': None
        })
        self.assertDictEqual(response, record)

        # PATCH
        updated = dict(record)
        updated['icon_class'] = str(uuid.uuid4())
        patch = jsonpatch.make_patch(record, updated)
        patch_value = patch.to_string().encode('utf-8')
        record.update({
            'icon_class': updated['icon_class'],
            'last_modified_by': self.USERNAME[self.ADMIN_ACCESS]
        })

        result = self.fetch(url, method='PATCH', body=patch_value)
        self.assertEqual(result.code, 200)
        self.assert_link_header_equals(result, url)
        new_value = json.loads(result.body.decode('utf-8'))
        self.assertDictEqual(new_value, record)

        # Patch no change
        result = self.fetch(url, method='PATCH', body=patch_value)
        self.assertEqual(result.code, 304)

        # GET
        result = self.fetch(url)
        self.assertEqual(result.code, 200)
        self.assertIsNotNone(result.headers['Date'])
        self.assertIsNotNone(result.headers['Last-Modified'])
        self.assert_link_header_equals(result, url)
        self.assertEqual(
            result.headers['Cache-Control'], 'public, max-age={}'.format(
                project_types.RecordRequestHandler.TTL))
        new_value = json.loads(result.body.decode('utf-8'))
        self.assertDictEqual(new_value, record)

        # Collection
        result = self.fetch('/project-types')
        self.assertEqual(result.code, 200)
        self.assertListEqual(
            json.loads(result.body.decode('utf-8')),
            [{k: v for k, v in record.items()
              if k not in ['created_by', 'last_modified_by']}])

        # DELETE
        result = self.fetch(url, method='DELETE')
        self.assertEqual(result.code, 204)

        # GET record should not exist
        result = self.fetch(url)
        self.assertEqual(result.code, 404)

        # DELETE should fail as record should not exist
        result = self.fetch(url,  method='DELETE')
        self.assertEqual(result.code, 404)

    def test_create_with_missing_fields(self):
        result = self.fetch(
            '/project-types', method='POST',
            json_body={
                'name': str(uuid.uuid4()),
                'plural_name': str(uuid.uuid4()),
                'slug': str(uuid.uuid4())
            })
        self.assertEqual(result.code, 400)

    def test_method_not_implemented(self):
        for method in {'DELETE', 'PATCH'}:
            result = self.fetch('/project-types', method=method)
            self.assertEqual(result.code, 405)

        url = '/project-types/' + str(uuid.uuid4())
        result = self.fetch(url, method='POST')
        self.assertEqual(result.code, 405)
