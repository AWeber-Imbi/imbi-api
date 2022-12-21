from __future__ import annotations

import datetime
import http.client
import json
import typing
import unittest.mock
import uuid

import yarl

from imbi import errors, models
from imbi.clients import consul
from tests import base


class TemplateExpansionTests(unittest.TestCase):
    def test_string_without_template(self):
        candidates = [
            'string without a template', 'this {is not a template',
            'neither is this}'
        ]
        for candidate in candidates:
            self.assertEqual(consul.expand_template(candidate, {}), candidate,
                             f'{candidate!r} was changed')

    def test_simple_expansions(self):
        context = {
            'string': 'value',
            'number': 1,
            'bool': True,
        }
        candidates = [
            ('{string}', 'value'),
            ('{number}', '1'),
            ('{bool}', 'True'),
            ('"{string}"', '"value"'),
            ('{string}{number}{bool}', 'value1True'),
            ('{{string}{{number}}{bool}}', '{value{1}True}'),
        ]
        consul.expand_template('"{string}"', context)
        for template, expectation in candidates:
            self.assertEqual(consul.expand_template(template, context),
                             expectation)

    def test_dotted_expansion(self):
        context = {'one': {'two': 3.0}}
        self.assertEqual(consul.expand_template('{one.two}', context), '3.0')

    def test_subscript_expansion(self):
        context = {'one': [0, 1, 'three']}
        self.assertEqual(consul.expand_template('{one[2]}', context), 'three')

    def test_missing_keys(self):
        context = {'a': 1}
        self.assertEqual(consul.expand_template('{b}', context), '')

    def test_ambiguous_expansion(self):
        context = {'a': [1, 2, 3]}
        self.assertEqual(consul.expand_template('{a[*]}', context), '{a[*]}')


class ConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.env_config = {
            'datacenter': 'development',
            'token': '123456',
            'url': 'https://example.com',
        }

    def test_creating_consul_with_missing_configuration(self):
        for key in self.env_config:
            cloned = self.env_config.copy()
            cloned.pop(key)
            with self.assertRaises(RuntimeError):
                consul.ConsulClient(cloned)

    def test_that_headers_are_configured(self):
        client = consul.ConsulClient(self.env_config)
        self.assertEqual(client.headers['Authorization'],
                         f'Bearer {self.env_config["token"]}')

    def test_that_v1_is_added_to_path(self):
        client = consul.ConsulClient(self.env_config)
        self.assertEqual(client.api_url.path, '/v1')


class ConsulConnection:
    def __init__(self, api_url: yarl.URL, token: str) -> None:
        self.headers = {'Authorization': f'Bearer {token}'}
        if api_url.scheme == 'http':
            self.conn = http.client.HTTPConnection(api_url.host, api_url.port)
        elif api_url.scheme == 'https':
            self.conn = http.client.HTTPSConnection(api_url.host, api_url.port)
        else:
            raise RuntimeError('Unsupported consul connection')

    def close(self) -> None:
        self.conn.close()

    @staticmethod
    def _build_url(path: tuple[str, ...]) -> str:
        if len(path) == 1:
            url = yarl.URL(f'/v1/{path[0].lstrip("/")}', encoded=True)
        else:
            url = yarl.URL('/v1')
            for c in path:
                url /= c
        return str(url)

    def get(
        self,
        *path: str,
        secret=None,
        return_response=False
    ) -> dict[str, typing.Any] | http.client.HTTPResponse:
        if secret:
            headers = {'X-Consul-Token': secret}
        else:
            headers = self.headers
        resource = self._build_url(path)
        self.conn.request('GET', resource, headers=headers)
        rsp = self.conn.getresponse()
        if return_response:
            rsp.read()  # required by connection protocol
            return rsp
        if rsp.getcode() != 200:
            raise AssertionError(f'Consul GET {resource} failed: {rsp.read()}')
        return json.load(rsp)

    def put(self, path: str, body: dict[str,
                                        typing.Any]) -> dict[str, typing.Any]:
        headers = self.headers.copy()
        headers['Content-Type'] = 'application/json'
        resource = f'/v1/{path.lstrip("/")}'
        self.conn.request('PUT',
                          resource,
                          body=json.dumps(body).encode('utf-8'),
                          headers=headers)
        rsp = self.conn.getresponse()
        if rsp.getcode() != 200:
            raise AssertionError(f'Consul PUT {resource} failed: {rsp.read()}')
        return json.load(rsp)

    def delete(self, *path: str) -> None:
        self.conn.request('DELETE',
                          self._build_url(path),
                          headers=self.headers)
        self.conn.getresponse().read()  # reqd by the connection protocol


class TokenCreationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.config = base.read_config()['automations']['consul']
        self.env_config = self.config['environments']['Development']
        self.client = consul.ConsulClient(self.env_config)

        self.created_secrets: set[str] = set()
        self.consul_conn = ConsulConnection(self.client.api_url,
                                            self.env_config['token'])
        self.addCleanup(self.cleanup)

        now = datetime.datetime.now(datetime.timezone.utc)
        namespace = models.Namespace(
            id=1,
            created_at=now,
            created_by='me',
            last_modified_at='',
            last_modified_by=None,
            name='Dev tools',
            slug='dev',
            icon_class='icon',
            maintained_by=[],
            gitlab_group_name='',
            sentry_team_slug=None,
        )
        project_type = models.ProjectType(
            id=1,
            created_at=now,
            created_by='me',
            last_modified_at=None,
            last_modified_by=None,
            name='Stuff',
            slug='stuff',
            plural_name='Stuff',
            description=None,
            icon_class=None,
            environment_urls=False,
            gitlab_project_prefix=None,
        )
        self.project_name = str(uuid.uuid4())
        self.project = models.Project(
            id=1,
            created_at=now,
            created_by='me',
            last_modified_at=None,
            last_modified_by=None,
            namespace=namespace,
            project_type=project_type,
            name=self.project_name,
            slug=self.project_name.replace('-', ''),
            description=None,
            environments=[],
            archived=False,
            gitlab_project_id=None,
            sentry_project_slug=None,
            sonarqube_project_key=None,
            pagerduty_service_id=None,
            facts={},
            links={},
            urls={},
            project_score=0,
        )

    def cleanup(self) -> None:
        for secret in self.created_secrets:
            token_info = self.consul_conn.get('/acl/token/self', secret=secret)
            for policy in token_info['Policies'] or []:  # could be null
                self.consul_conn.delete('acl', 'policy', policy['ID'])
            self.consul_conn.delete('acl', 'token', token_info['AccessorID'])
        self.consul_conn.close()

    async def test_token_creation(self):
        accessor, secret = await self.client.create_token(
            self.project, self.config['templates'])
        self.created_secrets.add(secret)

        token_info = self.consul_conn.get('/acl/token/self', secret=secret)
        self.assertTrue(token_info['Local'])
        self.assertEqual(len(token_info['Policies']), 1)

        policy_info = self.consul_conn.get('acl', 'policy',
                                           token_info['Policies'][0]['ID'])
        self.assertEqual(policy_info['Datacenters'],
                         [self.env_config['datacenter']])
        self.assertEqual(
            policy_info['Rules'],
            'key_prefix "%s/" { policy = "read" }' % '/'.join([
                self.project.namespace.slug, self.project.project_type.slug,
                self.project.slug
            ]))

    async def test_policy_creation_failure(self):
        self.client.api_url = self.client.api_url.with_path('/' +
                                                            str(uuid.uuid4()))
        with self.assertRaises(errors.InternalServerError):
            await self.client.create_token(self.project,
                                           self.config['templates'])

    async def test_token_creation_failure(self):
        state = {}
        real_api_method = self.client.api

        async def wrapped(path, *, method, raise_error=True, **kwargs):
            if path == '/acl/token':
                raise errors.InternalServerError('injected failure')
            else:
                response = await real_api_method(path,
                                                 method=method,
                                                 raise_error=raise_error,
                                                 **kwargs)
                if path == '/acl/policy':
                    state['policy_id'] = response.body['ID']
            return response

        mocked = unittest.mock.AsyncMock(wraps=wrapped)
        with unittest.mock.patch.object(self.client, 'api', new=mocked):
            with self.assertRaises(errors.InternalServerError):
                await self.client.create_token(self.project,
                                               self.config['templates'])

        mocked.assert_has_awaits([
            # creates the policy
            unittest.mock.call('/acl/policy',
                               method='PUT',
                               body=unittest.mock.ANY),
            # tries to create the token
            unittest.mock.call('/acl/token',
                               method='PUT',
                               body=unittest.mock.ANY),
            # removes the created policy
            unittest.mock.call(self.client.api_url / 'acl' / 'policy' /
                               state['policy_id'],
                               method='DELETE',
                               raise_error=False)
        ])


class TokenDeletionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.config = base.read_config()['automations']['consul']
        self.env_config = self.config['environments']['Development']
        self.client = consul.ConsulClient(self.env_config)
        self.consul_conn = ConsulConnection(self.client.api_url,
                                            self.env_config['token'])
        self.policy, self.token = None, None
        self.addCleanup(self.cleanup)

        self.policy = self.consul_conn.put(
            '/acl/policy', {
                'Name': str(uuid.uuid4()),
                'Rules': 'key_prefix "/" { policy = "read" }',
                'Datacenters': [self.env_config['datacenter']],
            })

        self.token = self.consul_conn.put('/acl/token', {
            'Policies': [{
                'ID': self.policy['ID']
            }],
            'Local': True,
        })

    def cleanup(self) -> None:
        if self.token:
            self.consul_conn.delete('acl', 'token', self.token['AccessorID'])
        if self.policy:
            self.consul_conn.delete('acl', 'policy', self.policy['ID'])
        self.consul_conn.close()

    async def test_policy_is_deleted(self):
        await self.client.delete_token(self.token['AccessorID'])
        rsp = self.consul_conn.get('acl',
                                   'policy',
                                   self.policy['ID'],
                                   return_response=True)
        self.assertNotEqual(rsp.getcode(), 200)  # NB - consul returns a 403

    async def test_that_all_policies_are_deleted(self):
        token_info = self.consul_conn.get('acl', 'token',
                                          self.token['AccessorID'])
        another_policy = self.consul_conn.put(
            '/acl/policy', {
                'Name': str(uuid.uuid4()),
                'Rules': 'key_prefix "/foo" { policy = "write" }',
                'Datacenters': [self.env_config['datacenter']]
            })
        token_info['Policies'].append({
            'ID': another_policy['ID'],
            'Name': another_policy['Name'],
        })
        self.consul_conn.put(f'/acl/token/{self.token["AccessorID"]}',
                             token_info)

        await self.client.delete_token(self.token['AccessorID'])
        rsp = self.consul_conn.get('acl',
                                   'policy',
                                   another_policy['ID'],
                                   return_response=True)
        self.assertNotEqual(rsp.getcode(), 200)  # NB - consul returns a 403

    async def test_token_is_deleted(self):
        await self.client.delete_token(self.token['AccessorID'])
        rsp = self.consul_conn.get('acl',
                                   'token',
                                   self.token['AccessorID'],
                                   return_response=True)
        self.assertNotEqual(rsp.getcode(), 200)  # NB - consul returns a 403

    async def test_that_deleting_nonexistent_token_succeeds(self):
        await self.client.delete_token(self.token['AccessorID'])
        await self.client.delete_token(self.token['AccessorID'])
