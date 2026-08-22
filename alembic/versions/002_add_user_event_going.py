"""
Migration 002 — Add user_event_going table

Tracks which events a user plans to attend.
Used to:
  1. Show "I'm Going" state in the UI
  2. Drive push notification reminders at estimated stage time
  3. Collect post-show stage time reports from attendees (highest-signal reporters)

Users are anonymous UUID-based (same pattern as favorites).
Events are identified by source + source_id + date (stable composite key).
"""

from alembic import op
import sqlalchemy as sa
from typing import Union, Sequence

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_event_going",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("user_uuid", sa.String(64), nullable=False),

        # Event identity — stable composite key
        sa.Column("event_source", sa.String(32), nullable=False),   # "ticketmaster" | "jambase"
        sa.Column("event_source_id", sa.String(128), nullable=False),  # TM or JamBase event ID
        sa.Column("event_date", sa.String(16), nullable=False),     # YYYY-MM-DD

        # Event snapshot — stored at toggle time so we have it for the notification
        sa.Column("headliner_name", sa.String(256), nullable=True),
        sa.Column("venue_name", sa.String(256), nullable=True),
        sa.Column("doors_time", sa.String(16), nullable=True),             # HH:MM:SS
        sa.Column("estimated_stage_time", sa.String(8), nullable=True),    # HH:MM

        # Push notification state
        sa.Column("device_token", sa.String(512), nullable=True),   # APNs token (populated post-consent)
        sa.Column("notified_at", sa.DateTime(), nullable=True),     # when reminder was sent
        sa.Column("notification_scheduled_at", sa.DateTime(), nullable=True),  # target send time

        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
    )

    op.create_index("ix_going_user_uuid", "user_event_going", ["user_uuid"])
    op.create_index("ix_going_event_source_id", "user_event_going", ["event_source", "event_source_id"])
    op.create_index("ix_going_event_date", "user_event_going", ["event_date"])
    op.create_index("ix_going_notify", "user_event_going", ["notification_scheduled_at", "notified_at"])

    op.create_unique_constraint(
        "uq_user_event_going",
        "user_event_going",
        ["user_uuid", "event_source", "event_source_id"]
    )


def downgrade() -> None:
    op.drop_table("user_event_going")
