import base64
import dataclasses
import http
import typing

import sprockets.mixins.http
import yarl

from imbi import errors, models, oauth2, user
from imbi.clients import github
from imbi.endpoints import base


@dataclasses.dataclass
class GitHubToken:
    access_token: str
    refresh_token: str


class RedirectHandler(sprockets.mixins.http.HTTPClientMixin,
                      base.RequestHandler):
    integration: 'oauth2.OAuth2Integration'

    NAME = 'github-redirect'

    async def prepare(self) -> None:
        await super().prepare()
        if not self._finished:
            self.integration = await oauth2.OAuth2Integration.by_name(
                self.application, 'github')
            if not self.integration:
                raise errors.IntegrationNotFound('github')

    async def get(self):
        auth_code = self.get_query_argument('code')
        state = base64.b64decode(self.get_query_argument('state'),
                                 b'-_').decode('utf-8')
        username, target = state.rstrip('?').split(':')
        token = await self.exchange_code_for_token(auth_code)
        try:
            user_id, email = await self.fetch_github_user(token)
            imbi_user = user.User(self.application, username=username)
            await imbi_user.refresh()
            if imbi_user.email_address != email:
                raise errors.Forbidden(
                    'mismatched user email: expected %r received %r',
                    imbi_user.email_address,
                    email,
                    title='GitHub authorization failure',
                    detail='unexpected email address {} for user {}'.format(
                        email, imbi_user.username))
            await self.integration.upsert_user_tokens(imbi_user.username,
                                                      str(user_id),
                                                      token.access_token,
                                                      token.refresh_token)
            await imbi_user.update_last_seen_at()
        except Exception:
            await self.revoke_github_token(token, username)
            raise

        target = yarl.URL(self.request.full_url()).with_path(target or '/ui/')
        self.redirect(str(target))

    async def exchange_code_for_token(self, code) -> GitHubToken:
        body = {
            'client_id': self.integration.client_id,
            'client_secret': self.integration.client_secret,
            'grant_type': 'authorization_code',
            'redirect_uri': str(self.integration.callback_url),
            'code': code,
        }
        response = await self.http_fetch(
            str(self.integration.token_endpoint),
            method='POST',
            body=body,
            content_type=sprockets.mixins.http.CONTENT_TYPE_JSON,
            request_headers={
                'X-GitHub-Api-Version': '2022-11-28',
                'Accept': str(sprockets.mixins.http.CONTENT_TYPE_JSON)
            })
        if not response.ok:
            self.logger.error(
                'failed to exchange auth code for token: (%s) %s',
                response.code, response.body)
            raise errors.InternalServerError(
                'failed exchange auth code for token: %s',
                response.code,
                title='GitHub authorization failure',
                instance={'error': response.body},
            )
        self.logger.debug('response_body %r', response.body)
        return GitHubToken(access_token=response.body['access_token'],
                           refresh_token=response.body['refresh_token'])

    async def fetch_github_user(self, token: GitHubToken) \
            -> typing.Tuple[int, str]:
        response = await self.http_fetch(
            str(self.integration.api_endpoint / 'user'),
            request_headers={
                'X-GitHub-Api-Version': '2022-11-28',
                'Accept': str(sprockets.mixins.http.CONTENT_TYPE_JSON),
                'Authorization': f'Bearer {token.access_token}'
            })
        if response.ok:
            return (response.body['id'], response.body['email'])
        raise errors.InternalServerError(
            'failed to retrieve GitHub user from access token',
            title='GitHub user lookup failure')

    async def revoke_github_token(self, token: GitHubToken, username: str):
        credentials = []
        token_types = []
        if token.access_token:
            credentials.append(token.access_token)
            token_types.append('access_token')
        if token.refresh_token:
            credentials.append(token.refresh_token)
            token_types.append('refresh_token')

        if not credentials:
            return

        body = {'credentials': credentials}
        response = await self.http_fetch(
            str(self.integration.api_endpoint / 'credentials' / 'revoke'),
            method='POST',
            body=body,
            content_type=sprockets.mixins.http.CONTENT_TYPE_JSON,
            request_headers={
                'X-GitHub-Api-Version': '2022-11-28',
                'Accept': str(sprockets.mixins.http.CONTENT_TYPE_JSON),
            },
        )

        if not response.ok:
            self.logger.warning(
                'failed to revoke GitHub credentials for user %s: %s '
                '(tokens: %s)', username, response.code,
                ', '.join(token_types))


class GitHubIntegratedHandler(base.AuthenticatedRequestHandler):
    """Base handler for GitHub-integrated endpoints"""

    NAME = 'github-integrated'
    INTEGRATION_NAME = 'github'

    async def prepare(self) -> None:
        await super().prepare()
        if not self._finished:
            self.client = await github.create_client(self.application,
                                                     self.INTEGRATION_NAME,
                                                     self.current_user)

    async def get_environment_slug(self, environment_name: str) -> str:
        """Fetch the environment slug from the database.

        Args:
            environment_name: The environment name (e.g., 'Staging',
                'Production')

        Returns:
            The environment slug (e.g., 'staging', 'production')

        Raises:
            ItemNotFound: If the environment does not exist
        """
        result = await self.postgres_execute(
            'SELECT slug FROM v1.environments WHERE name = %(name)s',
            {'name': environment_name},
            metric_name='fetch-environment-slug')
        if not result.row_count:
            raise errors.ItemNotFound(
                f'Environment "{environment_name}" not found',
                instance=self.request.uri)
        return result.row['slug']

    async def resolve_workflow_input(self, input_template: str,
                                     request_body: dict,
                                     project: models.Project) -> str:
        """Resolve a workflow input template to its actual value.

        Args:
            input_template: Template string (e.g., 'request.environment',
                'project.name')

            request_body: The request body containing user-provided values

            project: The project model

        Returns:
            The resolved input value

        Raises:
            BadRequest: If template is invalid or required data is missing
        """
        if not input_template or '.' not in input_template:
            raise errors.BadRequest(
                f'Invalid input template: {input_template}')

        source, field = input_template.split('.', 1)

        if source == 'request':
            value = request_body.get(field)
            if value is None:
                raise errors.BadRequest(
                    f'Field "{field}" is required in request body')

            # Special handling for environment field - convert to slug
            if field == 'environment':
                return await self.get_environment_slug(value)

            return str(value)

        elif source == 'project':
            if not hasattr(project, field):
                raise errors.BadRequest(
                    f'Project does not have field: {field}')
            value = getattr(project, field)
            if value is None:
                raise errors.BadRequest(f'Project field "{field}" is not set')
            return str(value)

        elif source == 'literal':
            return str(field)

        else:
            raise errors.BadRequest(f'Unknown input source: {source}')


class ProjectTagsHandler(GitHubIntegratedHandler):
    """Handler for fetching GitHub tags for a project"""

    NAME = 'github-project-tags'

    async def get(self, project_id: str) -> None:
        """Return list of tags from the project's GitHub repository

        Tags are fetched from GitHub API and 'refs/tags/' prefix removed
        """
        try:
            project_id_int = int(project_id)
        except ValueError:
            raise errors.BadRequest('Invalid project ID')

        project = await models.project(project_id_int, self.application)
        if project is None:
            raise errors.ItemNotFound(instance=self.request.uri)

        if 'github' not in project.identifiers:
            raise errors.ItemNotFound(
                'Project does not have a GitHub repository configured',
                instance=self.request.uri)

        org = project.project_type.github_org or project.project_type.slug
        repo = project.slug

        try:
            refs = await self.client.get_matching_refs(org, repo, 'tags')
        except Exception as e:
            self.logger.error('Failed to fetch tags from GitHub: %s', e)
            raise errors.InternalServerError(
                'Failed to fetch tags from GitHub',
                instance=self.request.uri) from e

        tags = []
        for ref_obj in refs:
            ref = ref_obj.get('ref', '')
            if ref.startswith('refs/tags/'):
                tags.append(ref[len('refs/tags/'):])

        self.send_response(tags)


class ProjectDeploymentsHandler(GitHubIntegratedHandler):
    """Handler for creating GitHub deployments"""

    NAME = 'github-project-deployments'

    async def post(self, project_id: str) -> None:
        """Create a GitHub deployment for the project

        Expects JSON body with:
        - ref: Tag or branch to deploy
        - environment: Target environment name
        """
        try:
            project_id_int = int(project_id)
        except ValueError:
            raise errors.BadRequest('Invalid project ID')

        body = self.get_request_body()
        if not body:
            raise errors.BadRequest('Request body is required')

        ref = body.get('ref')
        environment = body.get('environment')

        if not ref:
            raise errors.BadRequest('Field "ref" is required')
        if not environment:
            raise errors.BadRequest('Field "environment" is required')

        project = await models.project(project_id_int, self.application)
        if project is None:
            raise errors.ItemNotFound(instance=self.request.uri)

        if 'github' not in project.identifiers:
            raise errors.ItemNotFound(
                'Project does not have a GitHub identifier configured',
                instance=self.request.uri)

        if (project.environments is None
                or environment not in project.environments):
            raise errors.BadRequest(
                f'Environment "{environment}" is not configured'
                ' for this project')

        # Fetch the environment slug from the database
        environment_slug = await self.get_environment_slug(environment)

        org = project.project_type.github_org or project.project_type.slug
        repo = project.slug

        try:
            await self.client.create_deployment(org, repo, ref,
                                                environment_slug)
        except Exception as e:
            self.logger.error('Failed to create GitHub deployment: %s', e)
            raise errors.InternalServerError(
                'Failed to create GitHub deployment',
                instance=self.request.uri) from e

        api_endpoint = self.client.token.integration.api_endpoint
        if api_endpoint:
            base_url = str(api_endpoint).replace('api.', '')
            deployments_url = (f'{base_url}/{org}/{repo}/deployments/'
                               f'{environment_slug}')
        else:
            deployments_url = None

        self.set_status(http.HTTPStatus.CREATED)
        self.send_response({'deployments_url': deployments_url})


class ProjectWorkflowDispatchHandler(GitHubIntegratedHandler):
    """Generic handler for triggering GitHub workflow dispatch events"""

    NAME = 'github-project-workflow-dispatch'

    async def post(self, project_id: str, workflow_id: str) -> None:
        """Trigger a workflow dispatch for the project

        Expects JSON body with fields required by the workflow
        (typically 'environment')
        """
        try:
            project_id_int = int(project_id)
        except ValueError:
            raise errors.BadRequest('Invalid project ID')

        # Load workflow configuration
        workflow_config = self._get_workflow_config(workflow_id)
        if not workflow_config:
            raise errors.ItemNotFound(f'Workflow "{workflow_id}" not found',
                                      instance=self.request.uri)

        # Load project
        project = await models.project(project_id_int, self.application)
        if project is None:
            raise errors.ItemNotFound(instance=self.request.uri)

        # Validate project type
        applies_to_types = workflow_config.get('applies_to_project_types', [])
        if (applies_to_types
                and project.project_type.id not in applies_to_types):
            raise errors.BadRequest(
                f'Workflow "{workflow_config.get("name")}" is not available '
                f'for project type "{project.project_type.name}"')

        # Validate project has GitHub identifier
        if self.INTEGRATION_NAME not in project.identifiers:
            raise errors.ItemNotFound(
                f'Project does not have a {self.INTEGRATION_NAME.title()} '
                'identifier configured',
                instance=self.request.uri)

        # Get request body
        body = self.get_request_body()
        if not body:
            raise errors.BadRequest('Request body is required')

        # Resolve workflow inputs
        workflow_inputs = {}
        input_templates = workflow_config.get('inputs', {})
        for input_name, input_template in input_templates.items():
            try:
                workflow_inputs[input_name] = (await
                                               self.resolve_workflow_input(
                                                   input_template, body,
                                                   project))
            except errors.ApplicationError:
                raise
            except Exception as e:
                self.logger.error(
                    'Failed to resolve input "%s" with template "%s": %s',
                    input_name, input_template, e)
                raise errors.BadRequest(f'Failed to resolve workflow input '
                                        f'"{input_name}"') from e

        # Validate environment if specified in request
        if 'environment' in body:
            if (project.environments is None
                    or body['environment'] not in project.environments):
                raise errors.BadRequest(
                    f'Environment "{body["environment"]}" is not configured '
                    'for this project')

        # Get GitHub org and repo
        github_org = (project.project_type.github_org
                      or project.project_type.slug)
        repo = project.slug

        # Validate workflow_id is configured
        workflow_id_value = workflow_config.get('workflow_id')
        if not workflow_id_value:
            raise errors.InternalServerError(
                f'Workflow "{workflow_config.get("name")}" is missing '
                f'required "workflow_id" field in configuration')

        # Get ref (branch/tag) to run workflow on
        ref = body.get('ref', 'main')

        # Dispatch workflow
        try:
            await self.client.dispatch_workflow(
                org=github_org,
                repo=repo,
                workflow_filename=workflow_id_value,
                inputs=workflow_inputs,
                ref=ref)
        except Exception as e:
            self.logger.error('Failed to trigger workflow: %s', e)
            raise errors.InternalServerError('Failed to trigger workflow',
                                             instance=self.request.uri) from e

        self.set_status(http.HTTPStatus.ACCEPTED)
        self.send_response({
            'message': 'Workflow triggered successfully',
            'workflow': workflow_id_value,
            'workflow_name': workflow_config.get('name'),
            'inputs': workflow_inputs
        })

    def _get_workflow_config(self, workflow_id: str) -> typing.Optional[dict]:
        """Get workflow configuration by ID

        Args:
            workflow_id: Workflow identifier

        Returns:
            Workflow configuration dict or None if not found
        """
        # Load workflows from config
        actions_cfg = self.application.settings.get('actions', {})
        workflow_dispatch_cfg = actions_cfg.get('workflow_dispatch', {})

        if not workflow_dispatch_cfg.get('enabled', False):
            return None

        workflows = workflow_dispatch_cfg.get('workflows', [])

        # Find workflow by workflow_id
        for workflow in workflows:
            if workflow.get('workflow_id') == workflow_id:
                return workflow

        return None
