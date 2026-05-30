"""Standalone forward-only migrate + FTS reindex for a DPD sqlite DB.

Restore entry point for `export_sql` dumps (#60). A faithful dump is restored
into a fresh sqlite with `sqlite3 NEW.sqlite < dump.sql`; that DB carries the
origin `PRAGMA user_version` but has no FTS index (excluded from the dump as a
derived artifact). This module brings such a DB up to the current schema and
rebuilds the index:

    <python> -m dpd_mcp_server.migrate NEW.sqlite

Use the interpreter that has ``dpd_mcp_server`` installed (under the Claude Code
plugin that is the plugin venv, not a bare ``python`` on PATH). The ``import_sql``
tool emits the exact command with the right interpreter pre-filled.

Forward-only: a dump from a schema newer than this build cannot be downgraded
and is rejected rather than silently corrupted.
"""
from __future__ import annotations

import sqlite3
import sys

from .storage import SCHEMA_VERSION, Storage

# Derived from schema.sql's trailing `PRAGMA user_version` (single source of
# truth), so the forward-only restore guard cannot drift when the schema bumps.
CURRENT_SCHEMA_VERSION = SCHEMA_VERSION


def migrate(*, db_path: str) -> None:
    """Bring *db_path* to the current schema and rebuild the FTS index.

    Routes through ``Storage.open`` (the full forward-only migration chain),
    then rebuilds ``subgraphs_fts`` for every closed/archived subgraph, since a
    freshly restored dump has an empty index (the FTS table is excluded from
    ``export_sql``). Rejects a DB whose schema is newer than this build.
    """
    with sqlite3.connect(db_path) as probe:
        version = probe.execute("PRAGMA user_version").fetchone()[0]
    if version > CURRENT_SCHEMA_VERSION:
        raise ValueError(
            f"database schema v{version} is newer than this build "
            f"(v{CURRENT_SCHEMA_VERSION}); migration is forward-only and "
            f"downgrade is unsupported (#60)"
        )

    storage = Storage.open(db_path)
    with storage.connect() as conn:
        starts = conn.execute(
            "SELECT id FROM nodes WHERE type = 'start' "
            "AND state IN ('closed', 'archived')"
        ).fetchall()
        for row in starts:
            storage._reindex_subgraph_on(conn, start_node_id=row["id"])


def _cli(argv: list[str]) -> int:
    if len(argv) != 2:
        # argv[0] is this module's own invocation path; echoing the running
        # interpreter keeps the usage hint copy-pasteable under the plugin venv
        # (a bare `python` may not have dpd_mcp_server importable).
        print(
            f"Usage: {sys.executable} -m dpd_mcp_server.migrate <db_path>",
            file=sys.stderr,
        )
        return 2
    db_path = argv[1]
    migrate(db_path=db_path)
    print(f"Migrated {db_path} to current schema (user_version={CURRENT_SCHEMA_VERSION}) and rebuilt FTS index")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))
