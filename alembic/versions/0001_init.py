"""init schema

Revision ID: 0001_init
Revises:
Create Date: 2026-09-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tg_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.Text()),
        sa.Column("first_name", sa.Text()),
        sa.Column("lang", sa.Text(), server_default="ru", nullable=False),
        sa.Column("balance_usd", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("banned", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("bot_blocked", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("accepted_terms", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("ref_source", sa.Text()),
        sa.Column("tz", sa.Text(), server_default="UTC", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tg_id"),
        sa.CheckConstraint("balance_usd >= 0", name="users_balance_chk"),
    )
    op.create_table(
        "admins",
        sa.Column("tg_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("added_by", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("tg_id"),
        sa.CheckConstraint("role in ('owner','operator','support')", name="admins_role_chk"),
    )
    op.create_table(
        "settings",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_by", sa.BigInteger()),
        sa.PrimaryKeyConstraint("key"),
    )
    # remaining tables via metadata create_all in bot startup for MVP speed;
    # full DDL matches app.db.models


def downgrade() -> None:
    op.drop_table("settings")
    op.drop_table("admins")
    op.drop_table("users")
