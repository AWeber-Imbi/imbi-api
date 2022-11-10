from __future__ import annotations

import dataclasses
import logging
import re

import jsonpath_ng
import sprockets.mixins.http
import yarl

from imbi import errors, version
from imbi.automations import base


def expand_template(source: str, context: dict) -> str:
    """Expands JSON path expressions within a string template

    >>> context = {'project': {'slug': 'foo'}, 'environment': 'staging'}
    >>> expand_template(
    ...     'https://{project.slug}.services.{environment}.consul', context)
    >>> 'https://foo.services.staging.consul'

    The template is scanned for expressions bracketed by ``{`` and ``}``.
    Each expression is matched as a JSON path expression against the
    supplied context and the result is inserted into the output.

    """
    result = []
    last_pos = 0
    for expr in re.finditer(r'{([^}]+)}', source):
        first, last = expr.span(0)
        if last_pos < first:
            result.append(source[last_pos:first])
            last_pos = last
        matches = jsonpath_ng.parse(expr.group(1)).find(context)
        if len(matches) == 1:
            result.append(matches[0].value)
    rest = source[last_pos:]
    if rest:
        result.append(rest)
    return ''.join(result)


class ConsulClient(sprockets.mixins.http.HTTPClientMixin):
    # TODO: move this into imbi.clients
    def __init__(self, env_config: dict) -> None:
        super().__init__()
        self.logger = logging.getLogger(__package__).getChild('ConsulClient')

        self.api_url = yarl.URL(env_config['url']) / 'v1'
        self.datacenter = env_config['datacenter']
        self.headers = {
            'Authorization': f'Bearer {env_config["token"]}',
            'Content-Type': 'application/json',
        }

    async def create_token(self, project, templates) -> tuple[str, str]:
        self.logger.info('creating tokens for project %s in %s', project.id,
                         self.datacenter)
        project_dict = dataclasses.asdict(project)
        context = {
            'environment': {
                'datacenter': self.datacenter
            },
            'namespace': project_dict['namespace'],
            'project': project_dict,
            'project_type': project_dict['project_type'],
        }

        kv_path = expand_template(templates['key_value_path'], context)
        kv_path = kv_path.strip('/') + '/'
        rule = f'key_prefix "{kv_path}" {{ policy = "read" }}'
        response = await self.api(
            '/acl/policy',
            method='PUT',
            body={
                'Name': '-'.join([
                    'imbi', 'generated', project.namespace.slug, project.slug
                ]),
                'Description': f'Access policy for {project.slug}',
                'Rules': rule,
                'Datacenters': [self.datacenter],
            })
        policy_id = response.body['ID']

        try:
            response = await self.api(
                '/acl/token',
                method='PUT',
                body={
                    'Description': f'Token for {project.slug}',
                    'Policies': [{
                        'ID': policy_id
                    }],
                    'Local': True,
                })
            return response.body['AccessorID'], response.body['SecretID']
        except Exception as error:
            self.logger.error('failed to create token: %s', error)
            await self.api(self.api_url / 'acl' / 'policy' / policy_id,
                           method='DELETE',
                           raise_error=False)
            raise error

    async def delete_token(self, accessor_id) -> None:
        response = await self.api(self.api_url / 'acl' / 'token' / accessor_id,
                                  method='GET',
                                  raise_error=False)
        if response.ok:
            for policy in response.body.get('Policies', []):
                self.logger.info('removing policy %r during rollback',
                                 policy['ID'])
                await self.api(self.api_url / 'acl' / 'policy' / policy['ID'],
                               method='DELETE',
                               raise_error=False)
            self.logger.info('removing token %r during rollback', accessor_id)
            await self.api(self.api_url / 'acl' / 'token' / accessor_id,
                           method='DELETE',
                           raise_error=False)

    async def api(self,
                  path: str | yarl.URL,
                  *,
                  method: str,
                  raise_error=True,
                  **kwargs):
        if isinstance(path, yarl.URL):
            url = path
        else:
            url = self.api_url / path.lstrip('/')

        request_headers = self.headers.copy()
        request_headers.update(kwargs.pop('request_headers', {}))
        response = await super().http_fetch(
            str(url),
            method=method,
            request_headers=request_headers,
            user_agent=f'imbi/{version} (ConsulClient)',
            dont_retry={500},
            **kwargs)
        if raise_error and not response.ok:
            raise errors.InternalServerError('%s %s failed: %s',
                                             method,
                                             url,
                                             response.code,
                                             title='Consul API Failure')
        return response


class ConsulCreateTokenAutomation(base.Automation):
    def __init__(self, application, project_id, current_user, db) -> None:
        super().__init__(application, current_user, db)
        self.imbi_project_id = project_id
        self.project = None
        self.settings = self.automation_settings['consul']
        self.enabled = bool(self.settings.get('environments'))

    async def prepare(self) -> list[str]:
        if not self.enabled:
            self.logger.warning(
                'consul token creation disabled: no environment tokens '
                'configured')
            return self.errors

        project = await self._get_project(self.imbi_project_id)
        if project is None:
            self._add_error('Project id {} does not exist',
                            self.imbi_project_id)
        elif project.project_type.name not in self.settings['enabled_for']:
            self.logger.warning(
                'disabling consul automation since project type %r not '
                'found in %r', project.project_type.name,
                self.settings['enabled_for'])
            self.enabled = False
        else:
            self.project = project

        return self.errors

    async def run(self):
        if not self.enabled:
            return None
        if not self.project.environments:
            self.logger.debug('no data centers selected, nothing to do')
            return None

        templates = self.settings.get('templates', {})
        clients: dict[str, ConsulClient] = {}
        tokens: dict[str, tuple[str, str]] = {}

        try:
            for target_env in self.project.environments:
                try:
                    env_config = self.settings['environments'][target_env]
                except KeyError:
                    self.logger.info(
                        'not creating tokens for environment %r since it is '
                        'not configured', target_env)
                else:
                    if env_config['datacenter'] in tokens.keys():
                        continue  # allow environments to share Consul DCs
                    try:
                        client = clients[env_config['datacenter']]
                    except KeyError:
                        client = ConsulClient(env_config)
                        clients[env_config['datacenter']] = client
                    accessor_id, secret_id = await client.create_token(
                        self.project, templates)
                    tokens[env_config['datacenter']] = (accessor_id, secret_id)
                    await self.insert_secrets(env_config['datacenter'],
                                              accessor_id, secret_id)
        except Exception as error:
            self.logger.error(
                'failed to create consul tokens, rollback back: %s', error)
            for datacenter, keys in tokens.items():
                await clients[datacenter].delete_token(keys[0])
            await self.db.execute(
                'DELETE FROM v1.project_secrets'
                ' WHERE project_id = %(imbi_project_id)s'
                "   AND name LIKE 'consul_%%'", {
                    'imbi_project_id': self.project.id,
                })
            raise error

    async def insert_secrets(self, datacenter, accessor_id, secret_id) -> None:
        self.logger.debug('inserting tokens for project=%s datacenter=%s',
                          self.project.id, datacenter)
        await self.db.execute(
            'INSERT INTO v1.project_secrets(project_id, name, value,'
            '                               created_by)'
            ' VALUES (%(imbi_project_id)s, %(key_name)s,'
            '         %(key_value)s, %(username)s)'
            ' ON CONFLICT '
            ' ON CONSTRAINT project_secrets_pkey '
            ' DO UPDATE '
            ' SET value = %(key_value)s,'
            '     last_modified_at = CURRENT_TIMESTAMP,'
            '     last_modified_by = %(username)s', {
                'imbi_project_id': self.project.id,
                'key_name': f'consul_{datacenter}_accessor',
                'key_value': self.application.encrypt_value(accessor_id),
                'username': self.user.username,
            })
        await self.db.execute(
            'INSERT INTO v1.project_secrets(project_id, name, value,'
            '                               created_by)'
            ' VALUES (%(imbi_project_id)s, %(key_name)s,'
            '         %(key_value)s, %(username)s)'
            ' ON CONFLICT '
            ' ON CONSTRAINT project_secrets_pkey '
            ' DO UPDATE '
            ' SET value = %(key_value)s,'
            '     last_modified_at = CURRENT_TIMESTAMP,'
            '     last_modified_by = %(username)s', {
                'imbi_project_id': self.project.id,
                'key_name': f'consul_{datacenter}_secret',
                'key_value': self.application.encrypt_value(secret_id),
                'username': self.user.username,
            })
