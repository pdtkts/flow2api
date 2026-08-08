"""Checksummed PostgreSQL SQL migration runner."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg import sql
from psycopg.rows import dict_row


MIGRATION_LOCK_ID = 0x464C4F5732415049  # stable "FLOW2API" advisory lock
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations" / "postgres"


class PostgresMigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class MigrationFile:
    revision: str
    path: Path
    checksum: str
    sql_text: str


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[MigrationFile]:
    migrations: list[MigrationFile] = []
    for path in sorted(directory.glob("*.sql")):
        revision = path.stem.split("_", 1)[0]
        if not revision.isdigit():
            raise PostgresMigrationError(f"Invalid PostgreSQL migration filename: {path.name}")
        sql_text = path.read_text(encoding="utf-8")
        migrations.append(
            MigrationFile(
                revision=revision,
                path=path,
                checksum=hashlib.sha256(sql_text.encode("utf-8")).hexdigest(),
                sql_text=sql_text,
            )
        )
    if not migrations:
        raise PostgresMigrationError(f"No PostgreSQL migrations found in {directory}")
    revisions = [item.revision for item in migrations]
    if len(revisions) != len(set(revisions)):
        raise PostgresMigrationError("Duplicate PostgreSQL migration revisions")
    return migrations


async def run_postgres_migrations(connection: Any, schema: str) -> str:
    migrations = discover_migrations()
    await connection.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_ID,))
    try:
        await connection.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
        await connection.execute(
            sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema))
        )
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                revision TEXT PRIMARY KEY,
                checksum TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        async with connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute("SELECT revision, checksum FROM schema_migrations ORDER BY revision")
            rows = await cursor.fetchall()
        applied = {str(row["revision"]): str(row["checksum"]) for row in rows}
        known = {item.revision for item in migrations}
        unknown = sorted(set(applied) - known)
        if unknown:
            raise PostgresMigrationError(
                f"Database contains unknown schema revisions: {', '.join(unknown)}"
            )
        for migration in migrations:
            existing = applied.get(migration.revision)
            if existing and existing != migration.checksum:
                raise PostgresMigrationError(
                    f"Checksum mismatch for PostgreSQL migration {migration.path.name}"
                )
            if existing:
                continue
            async with connection.transaction():
                await connection.execute(migration.sql_text)
                await connection.execute(
                    "INSERT INTO schema_migrations (revision, checksum) VALUES (%s, %s)",
                    (migration.revision, migration.checksum),
                )
        await connection.execute(
            """
            INSERT INTO system_metadata (key, value, updated_at)
            VALUES ('schema_revision', %s, CURRENT_TIMESTAMP)
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (migrations[-1].revision,),
        )
        await connection.commit()
        return migrations[-1].revision
    except Exception:
        await connection.rollback()
        raise
    finally:
        try:
            await connection.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_ID,))
            await connection.commit()
        except Exception:
            await connection.rollback()


async def read_database_markers(connection: Any, schema: str) -> dict[str, str]:
    await connection.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute("SELECT key, value FROM system_metadata")
        rows = await cursor.fetchall()
    return {str(row["key"]): str(row["value"]) for row in rows}
