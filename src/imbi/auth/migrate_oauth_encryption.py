"""Migrate existing OAuth tokens to encrypted format (Phase 5).

This script encrypts plaintext OAuth access/refresh tokens that were
stored before Phase 5. It should be run once after deploying Phase 5
to upgrade existing OAuth identities.

Usage:
    python -m imbi.auth.migrate_oauth_encryption
"""

import asyncio
import logging

from imbi import neo4j
from imbi.auth.encryption import TokenEncryption

LOGGER = logging.getLogger(__name__)


async def migrate_oauth_tokens() -> dict[str, int]:
    """Migrate plaintext OAuth tokens to encrypted format.

    This function identifies OAuth identities with plaintext tokens
    (based on a heuristic) and encrypts them using Fernet symmetric
    encryption.

    Returns:
        dict: Statistics with 'migrated' and 'skipped' counts

    """
    # Initialize Neo4j
    await neo4j.initialize()

    try:
        # Get encryption instance
        encryptor = TokenEncryption.get_instance()

        # Fetch all OAuth identities
        query = """
        MATCH (identity:OAuthIdentity)
        RETURN identity
        """
        async with neo4j.run(query) as result:
            records = await result.data()

        migrated = 0
        skipped = 0

        for record in records:
            identity_data = record['identity']

            # Heuristic: Fernet-encrypted tokens are base64-encoded with
            # padding (end with ==). Plaintext provider tokens typically
            # don't have this pattern.
            needs_migration = False

            access_token = identity_data.get('access_token')
            refresh_token = identity_data.get('refresh_token')

            # Check if access_token needs encryption
            if access_token and not access_token.endswith('=='):
                # Likely plaintext - encrypt it
                encrypted_access = encryptor.encrypt(access_token)
                identity_data['access_token'] = encrypted_access
                needs_migration = True

            # Check if refresh_token needs encryption
            if refresh_token and not refresh_token.endswith('=='):
                # Likely plaintext - encrypt it
                encrypted_refresh = encryptor.encrypt(refresh_token)
                identity_data['refresh_token'] = encrypted_refresh
                needs_migration = True

            if needs_migration:
                # Update identity in Neo4j
                update_query = """
                MATCH (identity:OAuthIdentity {
                    provider: $provider,
                    provider_user_id: $provider_user_id
                })
                SET identity.access_token = $access_token,
                    identity.refresh_token = $refresh_token
                """
                async with neo4j.run(
                    update_query,
                    provider=identity_data['provider'],
                    provider_user_id=identity_data['provider_user_id'],
                    access_token=identity_data['access_token'],
                    refresh_token=identity_data.get('refresh_token'),
                ) as result:
                    await result.consume()

                migrated += 1
                LOGGER.info(
                    'Migrated OAuth identity: %s/%s',
                    identity_data['provider'],
                    identity_data['provider_user_id'],
                )
            else:
                skipped += 1

        LOGGER.info(
            'OAuth token migration complete: migrated=%d skipped=%d',
            migrated,
            skipped,
        )

        return {'migrated': migrated, 'skipped': skipped}

    finally:
        # Cleanup Neo4j connection
        await neo4j.aclose()


async def main() -> None:
    """Main entry point for migration script."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )

    LOGGER.info('Starting OAuth token encryption migration')

    stats = await migrate_oauth_tokens()

    LOGGER.info('Migration results:')
    LOGGER.info('  Migrated: %d', stats['migrated'])
    LOGGER.info('  Skipped: %d', stats['skipped'])


if __name__ == '__main__':
    asyncio.run(main())
