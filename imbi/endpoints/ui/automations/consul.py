from __future__ import annotations

from imbi.automations import consul
from imbi.endpoints import base
from imbi.endpoints.ui.automations import mixins


class CreateTokensRequestHandler(mixins.PrepareFailureMixin,
                                 base.AuthenticatedRequestHandler):
    async def post(self):
        request = self.get_request_body()
        project_id = int(request['project_id'])
        async with self.postgres_transaction() as transaction:
            automation = consul.ConsulCreateTokenAutomation(
                self.application, project_id, await self.get_current_user(),
                transaction)
            failures = await automation.prepare()
            if failures:
                raise self.handle_prepare_failures('Create Tokens', failures)
            await automation.run()
        self.set_status(204)
