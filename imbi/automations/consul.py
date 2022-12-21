from __future__ import annotations

import imbi.clients.consul
from imbi.automations import base


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
        clients: dict[str, imbi.clients.consul.ConsulClient] = {}
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
                        client = imbi.clients.consul.ConsulClient(env_config)
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
