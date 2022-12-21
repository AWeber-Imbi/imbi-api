from __future__ import annotations

import dataclasses
import logging
import re

import jsonpath_ng
import sprockets.mixins.http
import yarl

from imbi import errors, models, version


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
    for expr in re.finditer(r'{([^{}]+)}', source):
        first, last = expr.span(0)
        if last_pos != first:
            result.append(source[last_pos:first])
        last_pos = last
        matches = jsonpath_ng.parse(expr.group(1)).find(context)
        if len(matches) == 1:
            result.append(str(matches[0].value))
        elif matches:
            result.append(expr.group(0))
    rest = source[last_pos:]
    if rest:
        result.append(rest)
    return ''.join(result)


class ConsulClient(sprockets.mixins.http.HTTPClientMixin):
    """Consul API client

    :param env_config: dictionary of configuration details for
        the specific datacenter.  This should be a value from
        application settings['automations']['consul']['environments']

    API client that is connected to a specific datacenter.

    """
    def __init__(self, env_config: dict[str, str]) -> None:
        super().__init__()
        self.logger = logging.getLogger(__package__).getChild('ConsulClient')
        try:
            self.datacenter = env_config['datacenter']
            token = env_config['token']
            root_url = env_config['url']
        except KeyError as error:
            raise RuntimeError(
                f'{error.args[0]} missing from consul configuration')
        else:
            self.api_url = yarl.URL(root_url) / 'v1'
            self.headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
            }

    async def create_token(self, project: models.Project,
                           templates: dict[str, str]) -> tuple[str, str]:
        """Create a new KV access token for `project`

        This method creates a new policy that provides access to the
        KV path and then generates an access token with the policy assigned.
        The response is the accessor and secret IDs for the token.

        """
        self.logger.info('creating tokens for project %s in %s', project.id,
                         self.datacenter)
        project_dict = dataclasses.asdict(project)
        context = {
            'environment': {
                'datacenter': self.datacenter,
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

    async def delete_token(self, accessor_id: str) -> None:
        """Delete a consul token and related policies."""
        url = self.api_url / 'acl'
        response = await self.api(url / 'token' / accessor_id,
                                  method='GET',
                                  raise_error=False)
        if response.ok:
            for policy in response.body.get('Policies') or []:
                await self.api(url / 'policy' / policy['ID'],
                               method='DELETE',
                               raise_error=False)
            await self.api(url / 'token' / accessor_id,
                           method='DELETE',
                           raise_error=False)

    async def api(self,
                  path: str | yarl.URL,
                  *,
                  method: str,
                  raise_error=True,
                  **kwargs) -> sprockets.mixins.http.HTTPResponse:
        """Send an authenticated Consul API request"""
        if not isinstance(path, yarl.URL):
            url = self.api_url / path.lstrip('/')
        else:
            url = path

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
