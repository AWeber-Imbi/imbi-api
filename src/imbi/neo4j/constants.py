"""Constants for the Neo4J integration."""

_EMBEDDING_INDEX_CONFIG = """\
{
    `vector.dimensions`: 1536,
    `vector.similarity_function`: 'cosine'
}
"""

INDEXES: list[str] = [
    'CREATE CONSTRAINT blueprint_pkey IF NOT EXISTS FOR (n:Blueprint) '
    'REQUIRE (n.name, n.type) IS UNIQUE;',
]
