import os
import re
import urllib.parse

import aioboto3
import aioredis
import botocore.exceptions
import sprockets.mixins.http
from tornado import web

from imbi import errors, oauth2, user
from imbi.clients import aws
from imbi.endpoints import base


class CollectionRequestHandler(sprockets.mixins.http.HTTPClientMixin,
                               base.ValidatingRequestHandler):
    GET_PROJECT_INFO_SQL = re.sub(
        r'\s+', ' ', """\
        SELECT n.id AS namespace_id,
               n.aws_ssm_slug AS namespace_slug,
               p.slug AS project_slug,
               t.slug AS project_type_slug,
               p.configuration_type,
               p.environments
          FROM v1.namespaces AS n
          JOIN v1.projects AS p
            ON p.namespace_id = n.id
          JOIN v1.project_types AS t
            ON p.project_type_id = t.id
         WHERE p.id = %(project_id)s""")

    GET_ROLE_ARN_SQL = re.sub(
        r'\s+', ' ', """\
        SELECT role_arn
          FROM v1.aws_roles
         WHERE environment = %(environment)s
           AND namespace_id = %(namespace_id)s""")

    @staticmethod
    def path_prefix(template: str, project_slug: str, project_type_slug: str,
                    namespace_slug: str) -> str:
        return template.replace('{project_slug}', project_slug).replace(
            '{project_type_slug}',
            project_type_slug).replace('{namespace_slug}', namespace_slug)

    @property
    def _redis(self) -> aioredis.Redis:
        return self.application.session_redis

    async def get(self, *args, **kwargs) -> None:
        result = await self.postgres_execute(
            self.GET_PROJECT_INFO_SQL, {'project_id': kwargs['project_id']},
            'get-project-namespace-id')
        project_info = result.row

        if project_info['configuration_type'] != 'ssm':
            raise errors.ItemNotFound(
                'Project %s does not use SSM configuration',
                project_info['project_slug'],
                title='Wrong configuration type for project')

        imbi_user = await self.get_current_user()
        env = os.environ.get('ENVIRONMENT', 'production').lower()
        google_integration = await oauth2.OAuth2Integration.by_name(
            self.application, f'google-{env}')
        tokens = await google_integration.get_user_tokens(imbi_user)
        if len(tokens) == 0:
            raise errors.Forbidden(
                'No OAuth 2.0 tokens for %s',
                imbi_user.username,
                title=('Not authorized to access SSM in this namespace &'
                       ' environment'))
        google_tokens = tokens[0]

        ssm_path_prefix = self.path_prefix(
            self.application.settings['project_configuration']
            ['ssm_prefix_template'], project_info['project_slug'],
            project_info['project_type_slug'], project_info['namespace_slug'])

        role_arn_exists = False
        params_by_path = {}
        aws_session = aioboto3.Session()
        for environment in project_info['environments']:
            result = await self.postgres_execute(
                self.GET_ROLE_ARN_SQL, {
                    'environment': environment,
                    'namespace_id': project_info['namespace_id']
                }, 'get-role-arn')
            if not result.row:
                continue
            else:
                role_arn_exists = True
            role_arn = result.row['role_arn']

            creds = await self.get_aws_credentials(aws_session, role_arn,
                                                   imbi_user,
                                                   google_tokens.id_token,
                                                   google_tokens.refresh_token)
            params = await aws.get_parameters_by_path(
                aws_session, ssm_path_prefix, creds['access_key_id'],
                creds['secret_access_key'], creds['session_token'])
            for param in params:
                if param['Name'] in params_by_path:
                    params_by_path[
                        param['Name']]['values'][environment] = param['Value']
                else:
                    params_by_path[param['Name']] = {
                        'type': param['Type'],
                        'values': {
                            environment: param['Value']
                        }
                    }
        if not role_arn_exists:
            raise web.HTTPError(
                401,
                reason='No role ARNs exist for this namespace & environment')

        output = [{
            'name': name,
            'type': data['type'],
            'values': [{
                'environment': environment,
                'value': value
            } for environment, value in data['values'].items()]
        } for name, data in params_by_path.items()]
        self.send_response(output)

    async def get_aws_credentials(self,
                                  aws_session: aioboto3.Session,
                                  role_arn,
                                  imbi_user: user.User,
                                  id_token,
                                  refresh_token,
                                  is_retry=False) -> dict:
        """Return (potentially cached or refreshed) AWS credentials."""
        key = f'{imbi_user.username}:{role_arn}'
        cached_creds = await self._redis.hgetall(key)
        if cached_creds:
            return {
                key.decode('utf-8'): value.decode('utf-8')
                for key, value in cached_creds.items()
            }
        try:
            response = await aws.get_credentials(aws_session, role_arn,
                                                 imbi_user.username, id_token)
        except botocore.exceptions.ClientError as error:
            if not is_retry and error.response['Error'][
                    'Code'] == 'ExpiredTokenException':
                id_token = await self.refresh_oauth2_tokens(
                    imbi_user.username, imbi_user.external_id, refresh_token)
                return await self.get_aws_credentials(aws_session, role_arn,
                                                      imbi_user, id_token,
                                                      refresh_token, True)
            else:
                error_code = error.response['Error']['Code']
                if error_code == 'AccessDenied':
                    raise web.HTTPError(
                        401,
                        reason=('Not authorized to access SSM in this '
                                'namespace & environment'))
                else:
                    self.logger.error('Unexpected AWS credential failure: %s',
                                      error_code)
                    raise web.HTTPError(
                        500,
                        reason='Unexpected failure fetching AWS credentials')
        creds = {
            'access_key_id': response['Credentials']['AccessKeyId'],
            'secret_access_key': response['Credentials']['SecretAccessKey'],
            'session_token': response['Credentials']['SessionToken']
        }
        await self._redis.hmset_dict(key, creds)
        await self._redis.expireat(
            key, response['Credentials']['Expiration'].timestamp())
        return creds

    async def refresh_oauth2_tokens(self, username, external_id,
                                    refresh_token) -> str:
        env = os.environ.get('ENVIRONMENT', 'production').lower()
        google_integration = await oauth2.OAuth2Integration.by_name(
            self.application, f'google-{env}')
        body = urllib.parse.urlencode({
            'client_id': google_integration.client_id,
            'client_secret': google_integration.client_secret,
            'grant_type': 'refresh_token',
            'redirect_uri': google_integration.callback_url,
            'refresh_token': refresh_token,
        })
        response = await self.http_fetch(
            str(google_integration.token_endpoint),
            method='POST',
            body=body,
            content_type='application/x-www-form-urlencoded')
        if response.code in (401, 403):
            await self.session.clear()
            raise web.Finish()
        elif not response.ok:
            self.logger.error(
                'failed to exchange refresh token for new tokens: %s %s',
                response.body['error'], response.body['error_description'])
            raise errors.InternalServerError(
                'failed exchange refresh token for new token: %s',
                response.code,
                title='Google authorization failure',
                instance={
                    'error': response.body['error'],
                    'error_description': response.body['error_description'],
                })
        await google_integration.upsert_user_tokens(
            username,
            external_id,
            response.body['access_token'],
            refresh_token=response.body.get('refresh_token', None),
            id_token=response.body['id_token'])
        return response.body['id_token']