"""
Add error column to annotation_normalized.

A failed enrichment (the PDF couldn't be fetched or OCR'd, etc.) records the reason here so
failures are queryable, not only visible in worker logs. NULL means a good result.

Revision ID: d7a4f21c9e08
Revises: b11d1b13e447
Create Date: 2026-07-20 02:30:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "d7a4f21c9e08"
down_revision = "b11d1b13e447"


def upgrade():
    op.add_column(
        "annotation_normalized",
        sa.Column("error", sa.UnicodeText(), nullable=True),
    )


def downgrade():
    op.drop_column("annotation_normalized", "error")
