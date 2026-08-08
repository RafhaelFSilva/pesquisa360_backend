"""add finalidade to setores

Revision ID: f2a3b4c5d6e7
Revises: b1c2d3e4f5a6
Create Date: 2026-08-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "setores",
        sa.Column(
            "finalidade",
            sa.String(),
            nullable=False,
            server_default="OPERACAO",
        ),
    )


def downgrade() -> None:
    op.drop_column("setores", "finalidade")
