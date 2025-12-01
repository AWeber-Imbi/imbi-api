import logging

from imbi import errors, models
from imbi.endpoints import base

LOGGER = logging.getLogger(__name__)


class AvailableActionsHandler(base.AuthenticatedRequestHandler):
    """Handler for fetching available actions for a project"""

    NAME = 'ui-project-actions'

    async def get(self, project_id: str) -> None:
        """Return list of available actions for the project

        Actions are filtered based on:
        - Configuration toggles
        - User has required integration tokens
        - Project has required identifiers
        """
        try:
            project_id_int = int(project_id)
        except ValueError:
            raise errors.BadRequest('Invalid project ID')

        # Load project to verify it exists
        project = await models.project(project_id_int, self.application)
        if project is None:
            raise errors.ItemNotFound(instance=self.request.uri)

        actions = []

        # Consolidate GitHub-related actions and preconditions
        actions_cfg = self.application.settings.get('actions', {})
        automations_cfg = self.application.settings.get('automations', {})
        github_automation_enabled = (automations_cfg.get('github', {}).get(
            'enabled', False))

        deployment_enabled = (actions_cfg.get('github_deployment',
                                              {}).get('enabled', False))
        workflow_dispatch_enabled = (actions_cfg.get('workflow_dispatch',
                                                     {}).get('enabled', False))

        if deployment_enabled or workflow_dispatch_enabled:
            user_has_github = await self._user_has_integration_token('github')
            project_has_github = 'github' in project.identifiers

            LOGGER.debug(
                'GitHub preconditions: user_has_github=%s, '
                'project_has_github=%s, integration_enabled=%s',
                user_has_github, project_has_github, github_automation_enabled)

            # GitHub Deployment action option
            if (deployment_enabled and user_has_github and project_has_github
                    and github_automation_enabled):
                actions.append({
                    'id': 'github_deployment',
                    'name': 'Create GitHub Deployment',
                    'integration': 'github'
                })

            # Workflow dispatch actions - dynamically loaded from config
            if (workflow_dispatch_enabled and user_has_github
                    and project_has_github and github_automation_enabled):
                workflow_dispatch_cfg = actions_cfg.get(
                    'workflow_dispatch', {})
                workflows = workflow_dispatch_cfg.get('workflows', [])

                for workflow in workflows:
                    # Check if workflow applies to this project type
                    applies_to_types = workflow.get('applies_to_project_types',
                                                    [])
                    workflow_id = workflow.get('workflow_id')
                    if not workflow_id:
                        continue
                    if (not applies_to_types
                            or project.project_type.id in applies_to_types):
                        # Check required facts (if any specified)
                        requires_facts = workflow.get('requires_facts', {})
                        if requires_facts:
                            LOGGER.debug(
                                'Checking facts for workflow %s: '
                                'requires=%s, proj_facts=%s',
                                workflow_id, requires_facts, project.facts)
                            facts_match = all(
                                project.facts.get(k) == v
                                for k, v in requires_facts.items()
                            )
                            LOGGER.debug('Facts match: %s', facts_match)
                            if not facts_match:
                                continue

                        actions.append({
                            'id': workflow_id,
                            'name': workflow.get('name', workflow_id),
                            'integration': 'github',
                            'workflow_file': workflow_id,
                            'required_inputs': list(
                                workflow.get('inputs', {}).keys())
                        })

        self.send_response(actions)

    async def _user_has_integration_token(self, integration_name: str) -> bool:
        """Check if the current user has an OAuth token for the integration"""
        result = await self.postgres_execute(
            'SELECT 1 FROM v1.user_oauth2_tokens '
            'WHERE username = %(username)s AND integration = %(integration)s',
            {
                'username': self._current_user.username,
                'integration': integration_name
            },
            metric_name='check-user-oauth-token')
        return result.row_count > 0
