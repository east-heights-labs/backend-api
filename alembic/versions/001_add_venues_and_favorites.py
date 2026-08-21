"""add_venues_and_favorites

Revision ID: 001
Revises: 
Create Date: 2026-08-21 15:23:04

Creates:
  - venues table (venue catalog, source-agnostic)
  - user_venue_favorites table (anonymous UUID-based favorites)
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- venues ---
    op.create_table(
        "venues",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("city", sa.String(128), nullable=False),
        sa.Column("state", sa.String(8), nullable=True),
        sa.Column("country", sa.String(8), nullable=False, server_default="US"),
        sa.Column("address", sa.String(256), nullable=True),
        sa.Column("zip_code", sa.String(16), nullable=True),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        sa.Column("website", sa.String(512), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("is_claimed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("claimed_by_email", sa.String(256), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_venues_city", "venues", ["city"])
    op.create_index("ix_venues_lat_lng", "venues", ["lat", "lng"])
    op.create_index("ix_venues_source_id", "venues", ["source", "source_id"])

    # Full-text search index on venue name (PostgreSQL)
    op.execute(
        "CREATE INDEX ix_venues_name_gin ON venues USING gin(to_tsvector('english', name))"
    )

    # --- user_venue_favorites ---
    op.create_table(
        "user_venue_favorites",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("user_uuid", sa.String(64), nullable=False),
        sa.Column("venue_id", sa.String(128),
                  sa.ForeignKey("venues.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("user_uuid", "venue_id", name="uq_user_venue_favorite"),
    )
    op.create_index("ix_favorites_user_uuid", "user_venue_favorites", ["user_uuid"])
    op.create_index("ix_favorites_venue_id", "user_venue_favorites", ["venue_id"])


def downgrade() -> None:
    op.drop_table("user_venue_favorites")
    op.drop_index("ix_venues_name_gin", table_name="venues")
    op.drop_index("ix_venues_source_id", table_name="venues")
    op.drop_index("ix_venues_lat_lng", table_name="venues")
    op.drop_index("ix_venues_city", table_name="venues")
    op.drop_table("venues")
