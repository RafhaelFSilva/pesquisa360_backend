"""ensure_superadmin_profile

Revision ID: c8e4b1a2d9f0
Revises: b6f126cd7905
Create Date: 2026-05-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "c8e4b1a2d9f0"
down_revision: Union[str, Sequence[str], None] = "b6f126cd7905"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO perfis (nome, descricao)
        SELECT 'Superadmin', 'Administracao global da plataforma SaaS'
        WHERE NOT EXISTS (
            SELECT 1 FROM perfis WHERE lower(nome) = lower('Superadmin')
        )
        """
    )


def downgrade() -> None:
    pass
