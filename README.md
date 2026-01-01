# Imbi

> A DevOps Service Management Platform for managing complex service ecosystems

Imbi provides a centralized platform to manage, track, and understand all services and applications across your
organization. It serves as a single source of truth for service metadata, dependencies, ownership, and operational
information.

## What is Imbi?

Imbi helps organizations answer critical questions about their service landscape:

- **What services do we have?** Complete inventory with ownership, type, and namespace organization
- **How are they related?** Graph-based dependency tracking and relationship visualization
- **Who owns what?** Clear ownership and team assignments
- **What's deployed where?** Environment-specific URLs and deployment tracking
- **What needs attention?** Project health scoring based on configurable factors
- **Where's the documentation?** Links to repos, CI/CD, monitoring, and other tools

### Key Benefits

- **Single Source of Truth**: Centralized service catalog with comprehensive metadata
- **Relationship Visualization**: Graph database enables intuitive dependency mapping
- **Automation Ready**: API-first design enables integration with CI/CD, webhooks, and automations
- **AI-Powered**: Built-in vector search and conversational AI support for natural language queries
- **Extensible**: Blueprint system for customizable project metadata schemas
- **Developer Friendly**: Automatic data collection via GitHub webhooks and integrations

## Version 2.0 (Alpha)

**Complete rewrite** using modern Python technologies for improved performance, scalability, and AI integration:

- **FastAPI**: Modern async web framework with automatic OpenAPI documentation
- **Neo4j**: Graph database for modeling service relationships and dependencies with native vector search
- **ClickHouse**: Analytics and time-series data storage for operations logs and metrics
- **Pydantic v2**: Type-safe data validation and settings management
- **Cypherantic**: Type-safe Neo4j integration with automatic Pydantic model mapping

**Current Status**: Core infrastructure and authentication system complete with 315 tests (~30% coverage being expanded).
REST API with health check, authentication, user/group/role management, blueprint CRUD endpoints, and email sending functional.
Additional CRUD endpoints and UI in development.

### What's New in v2

- **Graph Database**: Neo4j replaces Postgres for intuitive relationship modeling and AI-friendly Cypher queries
- **Vector Search**: Built-in support for AI-powered semantic search across the service graph
- **Modern API**: FastAPI provides automatic OpenAPI docs, async performance, and better type safety
- **Simplified Architecture**: Dropping OpenSearch dependency in favor of Neo4j's native capabilities
- **AI-Ready**: Foundation for conversational AI, MCP server integration, and natural language queries
- **Full Authentication**: OAuth2/OIDC (Google, GitHub, Keycloak) and local password authentication with JWT tokens
- **Fine-Grained Authorization**: Permission-based access control with resource-level permissions and role management
- **Analytics Ready**: ClickHouse integration for operations logs and time-series metrics

For developers, see [CLAUDE.md](CLAUDE.md) for development guide and architecture details.

## Quick Start

### Development Environment

```bash
# Bootstrap development environment (installs deps, starts Docker services)
./bootstrap

# Run development server with auto-reload
uv run imbi run-server --dev

# Access the API
curl http://localhost:8000/status
```

### Testing

```bash
# Run all tests with coverage
uv run pytest

# Run pre-commit checks
uv run pre-commit run --all-files
```

## Core Concepts

### Data Model

Imbi organizes services using a flexible, graph-based data model:

- **Projects**: Individual services or applications in your organization
    - Unique slug identifier
    - Project type (web service, library, database, etc.)
    - Namespace for organizational grouping (team, department, product line)
    - Metadata via customizable blueprints (JSON Schema-based)
    - Links to external tools (GitHub, Jira, PagerDuty, monitoring, etc.)
    - Environment-specific URLs (staging, production, etc.)

- **Dependencies**: Graph relationships between projects
    - Direct dependencies (service A → service B)
    - Component dependencies (shared libraries, databases)
    - Visualize the entire dependency tree

- **Namespaces**: Organizational grouping for projects
    - Teams, departments, or product lines
    - Hierarchical organization
    - Ownership and access control boundaries

- **Project Types**: Categorization of services
    - Web Services, APIs, Libraries, Databases, etc.
    - Custom types with specific metadata requirements
    - Environment URL configuration per type

- **Blueprints**: JSON Schema-based metadata templates
    - Define custom fields for different project types
    - Enforce required metadata
    - Support for complex validation rules

### API Access

Once the server is running, explore the API:

```bash
# Health check
curl http://localhost:8000/status

# Get authentication providers
curl http://localhost:8000/auth/providers

# API documentation
open http://localhost:8000/docs  # OpenAPI/ReDoc UI
```

<<<<<<< HEAD
## Implemented Features

### Email Sending

Imbi includes a production-ready email sending module for transactional emails:

**Key Features:**
- **SMTP Integration**: Configurable SMTP client with TLS/SSL support
- **Retry Logic**: Exponential backoff for transient failures (default: 3 retries)
- **Template System**: Jinja2-based HTML and text email templates with autoescape
- **Audit Logging**: All email attempts logged to ClickHouse for tracking
- **Dry Run Mode**: Test email rendering without sending
- **Password Reset Flow**: Secure token generation stored in Neo4j

**Available Email Types:**
- Welcome emails for new users
- Password reset with secure tokens
- Email verification (planned)
- Security alerts (planned)

**Configuration:**
```bash
# Enable email sending
IMBI_EMAIL_ENABLED=true
IMBI_EMAIL_DRY_RUN=false

# SMTP settings
IMBI_EMAIL_SMTP_HOST=smtp.example.com
IMBI_EMAIL_SMTP_PORT=587
IMBI_EMAIL_SMTP_USE_TLS=true
IMBI_EMAIL_SMTP_USERNAME=user@example.com
IMBI_EMAIL_SMTP_PASSWORD=secret

# Sender information
IMBI_EMAIL_FROM_EMAIL=noreply@example.com
IMBI_EMAIL_FROM_NAME="Imbi Platform"

# Retry settings
IMBI_EMAIL_MAX_RETRIES=3
IMBI_EMAIL_INITIAL_RETRY_DELAY=1.0
IMBI_EMAIL_RETRY_BACKOFF_FACTOR=2.0
```

**Development Testing:**
For local development, use [Mailpit](https://mailpit.axllent.org/) (included in `compose.yaml`):
```bash
# Mailpit SMTP runs on port 1025
# Web UI available at http://localhost:8025
docker compose up -d mailpit
```

**Test Coverage:**
- 48 comprehensive tests (47 passing, 1 skipped)
- 90%+ coverage across all email modules
- Unit tests, integration tests, and Mailpit tests
=======
**Available Endpoints**:
- `GET /status` - Health check
- `GET /auth/providers` - List available authentication providers
- `POST /auth/login` - Authenticate with email/password
- `POST /auth/token/refresh` - Refresh access token
- `POST /auth/logout` - Logout (revoke tokens)
- `GET /auth/oauth/{provider}` - OAuth login redirect
- `GET /auth/oauth/{provider}/callback` - OAuth callback handler
- `GET /blueprints` - List blueprints (requires authentication)
- `POST /blueprints` - Create blueprint (requires `blueprint:write` permission)
- `GET /blueprints/{slug}` - Get blueprint by slug
- `PUT /blueprints/{slug}` - Update blueprint (requires `blueprint:write` permission)
- `DELETE /blueprints/{slug}` - Delete blueprint (requires `blueprint:delete` permission)
- `GET /users` - List users (requires `user:read` permission)
- `POST /users` - Create user (requires `user:write` permission)
- `GET /groups` - List groups (requires `group:read` permission)
- `POST /groups` - Create group (requires `group:write` permission)
- `GET /roles` - List roles (requires `role:read` permission)
- `POST /roles` - Create role (requires `role:write` permission)
>>>>>>> ef3cf08 (Update documentation to reflect current implementation status)

## Roadmap

### New Features and Improvements

1. **Integrated deployment functionality** based on Tom's Deployment Dashboard, but using the integrated data collected
   from the webhook service and a GitHub Application, instead of querying the GitHub API in real-time

2. **Conversational AI** - Provide a way to work with Imbi agentically:
    - *"When did x get y?"*
    - *"Check the logs for x, spot any issues?"*
    - *"Update everything using foo v1.2.3 to foo v1.4.5"*
    - *"Create a new consumer project for me …"*

3. **Webhook server with configurable workflows**
    - Updating project facts based on GitHub events
    - Automatic logging of deployments
    - Recording of PagerDuty Events

4. **Imbi-Automations as a background service**
    - This will allow for workflows to be triggered and running in k8s, not just locally on laptops
    - Bigger vision is the Imbi Automations workflow engine ends up being the core for how we do everything from mapping
      values from Webhook calls to handling conversational AI tasks

5. **Built-in MCP server**
    - Expose Imbi's functionality to remote chatbots like AJ or Agents like Claude

### Core Technical Changes

1. **Move to Graph database from Postgres**
    - The Postgres database required complex SQL queries to join across all the relations and mixed business logic with
      the storage layer
    - The data layer architecture ended up requiring us to implement OpenSearch to make the data easily searchable for
      humans and AI
    - The Cypher language is much easier to reason about for agents and we can more easily implement a query builder
      with the node and relationship nature of a Graph database
    - In addition, Neo4j supports vector based searching that we can implement in relationships to models to make it
      easy for AI to search the entire graph

2. **Move to ClickHouse for event / operations log** ✅
    - If we're moving off of Postgres for the operations log, it makes sense to think about using the right tool for the
      job with regard to how we should store it moving forward
    - ClickHouse client integrated with async support, schema management, and insert/query operations

3. **Ecosystem of services**
    - Instead of merging different types of functionality into one monolithic API, we move to speciality APIs, all
      bundled in a single Docker image
    - The core API for Imbi will provide the CRUD layer to all of the business logic, but we'll likely have separate
      APIs for things like Webhooks, LLM interaction, etc that will make each component easier to maintain
    - Perhaps even a API specific to the UI that is independent of the CRUD API

4. **Move to FastAPI from Tornado** ✅
    - Move to a modern framework that makes it easier to implement endpoints
    - We'll also be able to drop the OpenAPI repository all together as FastAPI auto-generates OpenAPI documents based
      on Pydantic models

5. **Rewrite the UI**
    - The Imbi UI was a great learning experience for me with regard to writing a fully functional React application
    - But I invented a lot of conventions to keep the code DRY
    - There are frameworks for the things I've done, and with a Figma UI mockup, AI can rebuild the new UI much faster
      using standard component libraries like Shadcn

6. **Dropping OpenSearch**
    - While OpenSearch enabled core functionality in Imbi like project searching and LLM integration, we will not need
      it when we move to the Graph database

### Other Improvements

- **Removal of multiple auth models**: OAuth2 for base user auth, JWT for inter-service / frontend to backend requests ✅
    - OAuth2/OIDC (Google, GitHub, Keycloak) and local password authentication implemented
    - JWT access tokens (15 min) and refresh tokens (7 days)

- **For token based auth move to `Authorization: Bearer`** ✅
    - Follow a standard default header that LLMs will assume is the header to use for token based auth
    - All authenticated endpoints use `Authorization: Bearer <token>` header

- **Events impacting project score**
    - Rolling 90 day window of PagerDuty issues
    - Age of last CI build

- **Project score factors** become native to the object types in the graph, not a standalone configurable and is managed
  by direct associations instead of lookup tables

- **Project score changes** recorded in ClickHouse and initiated by changes through the core API, not by database
  triggers

- **[Investigate gRPC](https://medium.com/@arturocuicas/fastapi-and-grpc-19c9b329b211)** for inter-service communication

- **[Instrument with OTEL](https://opentelemetry.io/docs/languages/python/instrumentation/)** for observability
    - Jaeger service configured in Docker Compose for trace collection
    - OpenTelemetry configuration generated by bootstrap script

- **Explore moving SBOM component information** to an internal instance
  of [Dependency Track](https://docs.dependencytrack.org)
    - If we can integrate with Dependency Track at the API level, it's a system specifically designed for what we want
      out of component tracking
    - It's a single system for tracking project package dependencies and is integrated with security databases and will
      allow us to find which projects have CVE issues automatically

## License

BSD 3-Clause License

Copyright (c) 2018 - 2026, AWeber
