"""One-shot data migrations.

Each is idempotent, non-destructive and resumable, and returns a report rather
than printing one, so a test can assert on what it did.
"""
