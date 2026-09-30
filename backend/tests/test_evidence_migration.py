"""Real Alembic migration and downgrade coverage for revision 0003."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, inspect, text

from app.db.migrations import alembic_config


def test_0003_upgrade_and_downgrade_preserve_the_previous_head(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.sqlite3"
    database_url = f"sqlite:///{database_path}"
    config = alembic_config(database_url)
    config.attributes["configure_logger"] = False

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert "evidence_objects" in inspector.get_table_names()
        assert "evidence_custody_events" in inspector.get_table_names()
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT last_value FROM identifier_sequences WHERE prefix = 'EOBJ'")
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text("SELECT last_value FROM identifier_sequences WHERE prefix = 'CST'")
                ).scalar_one()
                == 0
            )

        command.downgrade(config, "0002")
        inspector = inspect(engine)
        assert "evidence_objects" not in inspector.get_table_names()
        assert "evidence_custody_events" not in inspector.get_table_names()
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM identifier_sequences WHERE prefix IN ('EOBJ', 'CST')"
                    )
                ).scalar_one()
                == 0
            )

        command.upgrade(config, "head")
        inspector = inspect(engine)
        assert "evidence_objects" in inspector.get_table_names()
        assert "evidence_custody_events" in inspector.get_table_names()
    finally:
        engine.dispose()
