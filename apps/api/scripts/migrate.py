"""Run the pending data migrations against a real database.

Usage, from ``apps/api``::

    .venv/bin/python scripts/migrate.py --dry-run
    .venv/bin/python scripts/migrate.py

``--dry-run`` writes nothing and reports what would change. Run it first.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from biostruct.config import Settings
from biostruct.db import database, ensure_indexes
from biostruct.migrations.projects_to_sessions import migrate


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    settings = Settings()  # type: ignore[call-arg]
    db = await database.connect(settings.mongodb_uri, settings.mongodb_db)
    try:
        if not args.dry_run:
            # Indexes first: the unique message index is part of the schema the
            # migrated data has to satisfy.
            await ensure_indexes(db)
        report = await migrate(db, dry_run=args.dry_run)
    finally:
        await database.close()

    prefix = "would migrate" if args.dry_run else "migrated"
    print(f"projects -> research_sessions: {prefix} — {report.summary()}")
    for project_id, reason in report.failed:
        print(f"  FAILED {project_id}: {reason}", file=sys.stderr)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
