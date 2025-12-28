# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Imbi is a DevOps Service Management Platform designed to manage large environments containing many services and applications. Version 2 (currently in alpha) is a complete rewrite using FastAPI, Neo4j for graph data, and ClickHouse for analytics.

**See [ROADMAP.md](ROADMAP.md)** for full v2 vision and planned features including conversational AI, webhook workflows, MCP server integration, and ecosystem of services.

## Development Setup

### Initial Setup
```bash
./bootstrap
```
This script:
- Creates/activates a Python virtual environment
- Installs the package with dev dependencies
- Installs pre-commit hooks
- Starts Docker Compose services (Neo4j, ClickHouse)
- Waits for services to be healthy (120s timeout)

### Environment Configuration
Services run without authentication in development (configured in `compose.yaml`):
- Neo4j: ports 7474 (HTTP), 7687 (Bolt) - `NEO4J_AUTH=none`
- ClickHouse: ports 8123 (HTTP), 9000 (Native) - default/password

Override settings via environment variables or `.env` file:
```bash
NEO4J_URL=neo4j://localhost:7687
NEO4J_USER=username
NEO4J_PASSWORD=password
```

**Neo4j URL credential extraction**: The settings model automatically extracts credentials from URLs like `neo4j://user:pass@host:7687`, URL-decodes them, and strips them from the connection URL for security. Explicit `NEO4J_USER`/`NEO4J_PASSWORD` take precedence over URL credentials.

## Common Development Commands

### Running the Application
```bash
# Run development server with auto-reload
imbi run-server --dev

# Run production server (uses IMBI_ENVIRONMENT setting)
imbi run-server

# Access the API
curl http://localhost:8000/status
```

The server starts on `localhost:8000` by default (configurable via `IMBI_HOST` and `IMBI_PORT`). Development mode enables auto-reload when source files change.

### Code Quality
```bash
# Run all pre-commit checks (includes ruff linting + formatting)
pre-commit run --all-files

# Run ruff directly
ruff check .                    # Lint
ruff check --fix .             # Lint with auto-fix
ruff format .                   # Format code
```

### Testing
```bash
# Run all tests with coverage
coverage run && coverage report

# Run specific test file
python -m pytest tests/neo4j/test_client.py

# Run specific test class or method
python -m pytest tests/neo4j/test_client.py::Neo4jClientTestCase
python -m pytest tests/neo4j/test_client.py::Neo4jClientTestCase::test_singleton

# Run with verbose output
python -m pytest -v tests/
```

**Coverage requirement**: 90% minimum (enforced in `pyproject.toml`)

### Docker Services
```bash
# Start services
docker compose up --wait

# Stop and clean
docker compose down --remove-orphans --volumes

# Check service status
docker compose ps

# View logs
docker compose logs -f neo4j
docker compose logs -f clickhouse
```

## Code Architecture

### High-Level Structure
- **`src/imbi/`**: Main application code
  - `app.py`: FastAPI application factory with lifespan management
  - `entrypoint.py`: CLI commands (Typer-based)
  - `models.py`: Core domain models (Blueprint, Namespace, ProjectType, Project)
  - `settings.py`: Configuration via Pydantic Settings with URL credential extraction
  - `endpoints/`: API endpoint routers
    - `status.py`: Health check endpoint
  - `neo4j/`: Neo4j graph database integration layer
    - `client.py`: Singleton driver with event loop awareness
    - `__init__.py`: High-level API and cypherantic wrappers
    - `constants.py`: Index definitions and vector configuration
- **`tests/`**: Test suite with 100% code coverage (55 tests)

### Neo4j Integration Pattern

The Neo4j module uses a **singleton pattern with event loop awareness**:

```python
from imbi import neo4j

# Module-level APIs (preferred):
async with neo4j.session() as sess:
    # Use session
    pass

async with neo4j.run('MATCH (n) RETURN n', param=value) as result:
    records = await result.data()

# High-level operations:
await neo4j.initialize()  # Set up indexes
element_id = await neo4j.upsert(node_model, {'id': '123'})
await neo4j.aclose()  # Cleanup

# Cypherantic wrapper functions (type-safe Pydantic integration):
node_id = await neo4j.create_node(model_instance)  # Create node from model
edge_id = await neo4j.create_relationship(
    source_model, target_model, rel_type='DEPENDS_ON'
)
await neo4j.refresh_relationship(model, 'dependencies')  # Lazy-load relationships
edges = await neo4j.retrieve_relationship_edges(model, 'dependencies')
```

**Implementation details** (`src/imbi/neo4j/client.py`):
- `Neo4j.get_instance()`: Returns singleton driver instance
- Automatically reinitializes if event loop changes (important for FastAPI)
- Manages connection pool with keep-alive and max connection settings
- `initialize()` creates indexes defined in `neo4j/constants.py`

**Upsert pattern** (`neo4j/__init__.py:upsert()`):
- Uses Cypher `MERGE` with `ON CREATE SET` and `ON MATCH SET`
- Takes constraint dict for matching (e.g., `{'id': '123'}`)
- Automatically maps Pydantic model properties to node properties
- Returns Neo4j elementId of created/updated node

**Cypherantic integration** (`neo4j/__init__.py`):
- `create_node()`: Create Neo4j nodes from Pydantic models with automatic label/property mapping
- `create_relationship()`: Create typed relationships between nodes with optional properties
- `refresh_relationship()`: Lazy-load relationship properties from graph (on-demand fetching)
- `retrieve_relationship_edges()`: Fetch relationship edges as Pydantic models
- Full type safety with TypeVars preserving model types through operations

### FastAPI Application Structure

**Application factory** (`src/imbi/app.py`):
```python
from imbi.app import create_app

app = create_app()  # Returns configured FastAPI instance
```

**Lifespan management**: The application uses FastAPI's lifespan context manager to:
- Initialize Neo4j indexes on startup (`neo4j.initialize()`)
- Clean up Neo4j connections on shutdown (`neo4j.aclose()`)
- Ensures proper resource management across application lifecycle

**Endpoint registration** (`src/imbi/endpoints/`):
- Each endpoint module exports an `APIRouter`
- Routers collected in `endpoints/__init__.py:routers` list
- Automatically registered in `create_app()` via `app.include_router()`

**CLI interface** (`src/imbi/entrypoint.py`):
- Built with Typer for command-line operations
- `run-server`: Start uvicorn with development/production modes
- Configures logging, auto-reload, proxy headers, and custom Server header

### Data Modeling Conventions

1. **Pydantic models** (`src/imbi/models.py`):
   - Domain entities use `pydantic.BaseModel`
   - Keep models simple, focused on data structure
   - Model class names become Neo4j labels (lowercase)

2. **Settings** (`src/imbi/settings.py`):
   - Use `pydantic_settings.BaseSettings` for configuration
   - Prefix environment variables (e.g., `NEO4J_URL`)
   - Support `.env` files with `BASE_SETTINGS` config dict

3. **Neo4j models** (`src/imbi/neo4j/models.py`):
   - `Node`: Represents graph nodes with labels and properties
   - `coerce_neo4j_datetime()`: Convert Neo4j DateTime to Python datetime

### Testing Patterns

Tests use `unittest.IsolatedAsyncioTestCase` for async support:

```python
class MyTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        # Reset singleton for test isolation
        client.Neo4j._instance = None
        # Set up mocks

    async def test_something(self) -> None:
        # Test async code
        pass
```

**Mocking async context managers**:
```python
mock_session = unittest.mock.AsyncMock()
mock_session.__aenter__.return_value = mock_session
mock_session.__aexit__.return_value = None
```

## Code Style

**Configured in `pyproject.toml`**:
- **Line length**: 79 characters
- **Quote style**: Single quotes
- **Python version**: 3.12+
- **Formatter**: Ruff (replaces Black)
- **Linter**: Ruff with comprehensive rules (see `tool.ruff.lint.select`)

**Key conventions**:
- Async/await for all I/O operations
- Context managers (`async with`) for resource management
- Module-level loggers: `LOGGER = logging.getLogger(__name__)`
- Type hints using modern syntax (`str | None`, not `Optional[str]`)
- `typing.LiteralString` for Cypher queries to ensure safety
- Security tests disabled in test files (`[tool.ruff.lint.per-file-ignores]`)

**Ignored rules**:
- `E501`: Line too long (many long Pydantic model descriptions)
- `N818`: Exception class names don't need to end in "Error"
- `UP040`: Allow non-PEP 695 type aliases
- `UP047`: Allow non-PEP 695 generic functions (TypeVars for cypherantic compatibility)

## CI/CD

**GitHub Actions workflows** (`.github/workflows/`):
- `testing.yaml`: Runs on Python 3.12, includes pre-commit checks, pytest with 90% coverage, Codecov upload
- `docs.yaml`: Builds and deploys MkDocs documentation to GitHub Pages

**Pre-commit hooks** (`.pre-commit-config.yaml`):
- Standard checks: trailing whitespace, EOF, YAML/TOML validation, merge conflicts
- Ruff: Linting with `--fix` and formatting

## Important Notes

**Current development status**: This is a v2 alpha rewrite. Core infrastructure is complete with 100% test coverage (55 tests):

✅ **Implemented**:
- FastAPI application with lifespan management (Neo4j init/cleanup)
- Status endpoint with health check (`GET /status`)
- CLI with `run-server` command (development and production modes)
- Neo4j integration with singleton pattern, cypherantic wrappers, indexes, upsert operations
- Settings management via Pydantic with URL credential extraction
- Core domain models (Blueprint, Namespace, ProjectType, Project)
- Docker Compose development environment (Neo4j, ClickHouse)
- Pre-commit hooks with Ruff linting and formatting
- Bootstrap script for automated setup
- Comprehensive test suite with 100% code coverage

🚧 **In Progress**:
- ClickHouse integration (dependency present, service running, but not yet integrated in code)
- Additional API endpoints (projects, namespaces, blueprints CRUD)
- Authentication/authorization
- Webhook service
- Conversational AI features

**Database strategy**:
- **Neo4j**: Graph database for service relationships and dependencies
- **ClickHouse**: Analytics and time-series data (planned)

**Vector embeddings**: Configuration present for 1536-dimensional vectors with cosine similarity for AI-powered search (see `neo4j/constants.py`)
