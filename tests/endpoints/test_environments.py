import json
import uuid

import jsonpatch

from imbi.endpoints import environments
from tests import base


class AsyncHTTPTestCase(base.TestCaseWithReset):

    ADMIN_ACCESS = True
    TRUNCATE_TABLES = [
        'v1.environments'
    ]

    def test_environment_lifecycle(self):
        record = {
            'name': str(uuid.uuid4()),
            'description': str(uuid.uuid4()),
            'icon_class': 'fas fa-blind'
        }

        # Create
        result = self.fetch(
            '/environments', method='POST',
            body=json.dumps(record).encode('utf-8'))
        self.assertEqual(result.code, 200)
        url = self.get_url('/environments/{}'.format(record['name']))
        self.assert_link_header_equals(result, url)
        self.assertIsNotNone(result.headers['Date'])
        self.assertIsNone(result.headers.get('Last-Modified', None))
        self.assertEqual(
            result.headers['Cache-Control'], 'public, max-age={}'.format(
                environments.RecordRequestHandler.TTL))
        record.update({
            'created_by': self.USERNAME[self.ADMIN_ACCESS],
            'last_modified_by': None
        })
        new_value = json.loads(result.body.decode('utf-8'))
        self.assertDictEqual(new_value, record)

        # PATCH
        updated = dict(record)
        updated['icon_class'] = str(uuid.uuid4())
        patch = jsonpatch.make_patch(record, updated)
        patch_value = patch.to_string().encode('utf-8')
        record.update({
            'description': updated['description'],
            'icon_class': updated['icon_class'],
            'last_modified_by': self.USERNAME[self.ADMIN_ACCESS]
        })
        result = self.fetch(url, method='PATCH', body=patch_value)
        self.assertEqual(result.code, 200)
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
                environments.RecordRequestHandler.TTL))
        new_value = json.loads(result.body.decode('utf-8'))
        self.assertDictEqual(new_value, record)

        # Collection
        result = self.fetch('/environments')
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
        result = self.fetch(url, method='DELETE')
        self.assertEqual(result.code, 404)

    def test_create_with_missing_fields(self):
        record = {
            'name': str(uuid.uuid4()),
            'icon_class': 'fas fa-blind'
        }
        result = self.fetch('/environments', method='POST',
                            body=json.dumps(record).encode('utf-8'))
        self.assertEqual(result.code, 200)
        new_value = json.loads(result.body.decode('utf-8'))
        self.assertEqual(new_value['name'], record['name'])
        self.assertIsNone(new_value['description'])
        self.assertIsNotNone(new_value['icon_class'])

    def test_method_not_implemented(self):
        for method in {'DELETE', 'PATCH'}:
            result = self.fetch(
                '/environments', method=method,
                allow_nonstandard_methods=True)
            self.assertEqual(result.code, 405)
        url = '/environments/' + str(uuid.uuid4())
        result = self.fetch(
            url, method='POST', allow_nonstandard_methods=True)
        self.assertEqual(result.code, 405)
