# Imbi 2.0 Roadmap

# New Features and Improvements

1. Integrated deployment functionality based on Tom's Deployment Dashboard, but using the integrated data collected from
   the webhook service and a GitHub Application, instead of querying the GitHub API in real-time*
2. Conversational AI - Provide a way to work with Imbi agentically:
   *”When did x get y?” “Check the logs for x, spot any issues?” “Update everything using foo v1.2.3 to foo v1.4.5”
   “Create a new consumer project for me …”*
3. Webhook server with configurable workflows
    - *Updating project facts based on GitHub events*
    - *Automatic logging of deployments*
    - *Recording of PagerDuty Events*
4. Imbi-Automations as a background service
   *This will allow for workflows to be triggered and running in k8s, not just locally on laptops. Bigger vision is the
   Imbi Automations workflow engine ends up being the core for how we do everything from mapping values from Webook
   calls to handling conversational AI tasks.*
5. Built-in MCP server
   *Expose Imbi’s functionality to remote chatbots like AJ or Agents like Claude.*

# Core Technical Changes

1. Move to Graph database from Postgres
   *The Postgres database required complex SQL queries to join across all the relations and mixed business logic with
   the storage layer. The data layer architecture ended up requiring us to implement OpenSearch to make the data easily
   searchable for humans and AI. The Cypher language is much easier to reason about for agents and we can more easily
   implement a query builder with the node and relationship nature of a Graph database. In addition, Neo4j supports
   vector based searching that we can implement in relationships to models to make it easy for AI to search the entire
   graph.*
2. Move to ClickHouse for event / operations log
   *If we’re moving off of Postgres for the operations log, it makes sense to think about using the right tool for the
   job with regard to how we should store it moving forward.*
3. Ecosystem of services
   *Instead of merging different types of functionality into one monolithic API, we move to speciality APIs, all bundled
   in a single Docker image. The core API for Imbi will provide the CRUD layer to all of the business logic, but we’ll
   likely have separate APIs for things like Webhooks, LLM interaction, etc that will make each component easier to
   maintain. Perhaps even a API specific to the UI that is independent of the CRUD API.*
4. Move to FastAPI from Tornado
   *Move to a modern framework that makes it easier to implement endpoints. We’ll also be able to drop the OpenAPI
   repository all together as FastAPI auto-generates OpenAPI documents based on Pydantic models.*
5. Rewrite the UI
   *The Imbi UI was a great learning experience for me with regard to writing a fully functional React application. But
   I invented a lot of conventions to keep the code DRY. There are frameworks for the things I’ve done, and witha Figma
   UI mockup, AI can rebuild the new UI much faster using standard component libraries like Shancn.*
6. Dropping OpenSearch
   *While OpenSearch enabled core functionality in Imbi like project searching and LLM integration, we will not need it
   when we move to the Graph database.*

# Other

- Removal of multiple auth models: OAuth2 for base user auth, JWT for inter-service / frontend to backend requests
  *Simplify authentication options - may want to consider local users/groups*
- For token based auth move to `Authentication: Bearer`
  *Follow a standard default header that LLMs will assume is the header to use for token based auth*
- Events impacting project score
    - *Rolling 90 day window of PagerDuty issues*
    - *Age of last CI build*
- Project score factors become native to the object types in the graph, not a standalone configurable and is managed by
  direct associations instead of lookup tables.
- Project score changes recorded in ClickHouse and initiated by changes through the core API, not by database triggers
- [Investigate gRPC](https://medium.com/@arturocuicas/fastapi-and-grpc-19c9b329b211) for inter-service communication
- [Instrument with OTEL](https://opentelemetry.io/docs/languages/python/instrumentation/)
- Explore moving SBOM component information to an internal instance
  of [Dependency Track](https://docs.dependencytrack.org).
  *If we can integrate with Dependency Track at the API level, it’s a system specifically designed for what we want out
  of component tracking. It’s a single system for tracking project package dependencies and is integrated with security
  databases and will allow us to find which projects have CVE issues automatically.*
