"""
Create annotation_normalized table.

Revision ID: b11d1b13e447
Revises: b6be2385d907
Create Date: 2026-07-19 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

from h.db import types

revision = "b11d1b13e447"
down_revision = "b6be2385d907"


def upgrade():
    op.create_table(
        "annotation_normalized",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column(
            "created", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("annotation_id", types.URLSafeUUID, nullable=False, unique=True),
        sa.Column("normalized_quote", sa.UnicodeText(), nullable=False),
        sa.Column("method", sa.UnicodeText(), nullable=False),
        sa.ForeignKeyConstraint(
            ["annotation_id"], ["annotation.id"], ondelete="cascade"
        ),
    )


def downgrade():
    op.drop_table("annotation_normalized")
