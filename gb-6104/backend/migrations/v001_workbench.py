from src.models import audit, cases, restore, reviews, runs, users  # Register the tables.
from src.models.base import metadata


def upgrade(connection):
    # Version 1 creates the original workbench tables. Newer columns and the
    # restore tables are added additively by version 2, so an existing volume
    # only ever runs the ALTER statements it is missing.
    metadata.create_all(connection, tables=[
        metadata.tables["users"], metadata.tables["cases"],
        metadata.tables["runs"], metadata.tables["reviews"],
        metadata.tables["audit_events"],
    ])
