"""add question analytics metadata

Revision ID: c4d5e6f7a8b9
Revises: a3b4c5d6e7f8
Create Date: 2026-08-19 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "a3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    json_type = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")
    op.add_column("perguntas", sa.Column("papel_analitico", sa.String(50), nullable=True))
    op.add_column(
        "perguntas",
        sa.Column(
            "metadados_analiticos",
            json_type,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_perguntas_papel_analitico",
        "perguntas",
        ["papel_analitico"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_perguntas_papel_analitico", table_name="perguntas")
    op.drop_column("perguntas", "metadados_analiticos")
    op.drop_column("perguntas", "papel_analitico")
