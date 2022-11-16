import json

import jsonpatch

from imbi.endpoints import project_types
from tests import base


class AsyncHTTPTestCase(base.TestCaseWithReset):

    ADMIN_ACCESS = True
    TRUNCATE_TABLES = [
        'v1.project_types',
        'v1.project_fact_types',
        'v1.project_fact_type_ranges'
    ]

    def setUp(self) -> None:
        super().setUp()
        self.project_fact_type = self.create_project_fact_type()

    def test_project_fact_type_option_lifecycle(self):
        record = {
            'fact_type_id': self.project_fact_type['id'],
            'min_value': 75,
            'max_value': 100,
            'score': 50
        }

        # Create
        result = self.fetch(
            '/project-fact-type-ranges',
            method='POST', body=json.dumps(record).encode('utf-8'))
        self.assertEqual(result.code, 200)
        response = json.loads(result.body.decode('utf-8'))
        url = self.get_url(
            '/project-fact-type-ranges/{}'.format(response['id']))
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
        self.assertDictEqual(record, response)

        # PATCH
        updated = dict(record)
        updated['score'] = 25
        patch = jsonpatch.make_patch(record, updated)
        patch_value = patch.to_string().encode('utf-8')

        result = self.fetch(
            url, method='PATCH', body=patch_value)
        self.assertEqual(result.code, 200)
        self.assert_link_header_equals(result, url)
        record.update({
            'score': updated['score'],
            'last_modified_by': self.USERNAME[self.ADMIN_ACCESS]
        })
        new_value = json.loads(result.body.decode('utf-8'))
        self.assertDictEqual(new_value, record)

        # Patch no change
        result = self.fetch(url, method='PATCH', body=patch_value)
        self.assertEqual(result.code, 304)

        # GET
        result = self.fetch(url)
        self.assertEqual(result.code, 200)
        self.assert_link_header_equals(result, url)
        self.assertIsNotNone(result.headers['Date'])
        self.assertIsNotNone(result.headers['Last-Modified'])
        self.assertEqual(
            result.headers['Cache-Control'], 'public, max-age={}'.format(
                project_types.RecordRequestHandler.TTL))
        new_value = json.loads(result.body.decode('utf-8'))
        self.assertDictEqual(new_value, record)

        # Collection
        result = self.fetch('/project-fact-type-ranges')
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
