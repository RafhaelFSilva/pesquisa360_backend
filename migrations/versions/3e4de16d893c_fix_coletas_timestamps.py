"""Fix coletas timestamps

Revision ID: 3e4de16d893c
Revises: 91fbe6db1f17
Create Date: 2025-12-08 22:30:11.944448

"""
from alembic import op
import sqlalchemy as sa

# Revisão automática preenchida pela Alembic
revision = '3e4de16d893c'
down_revision = '91fbe6db1f17'
branch_labels = None
depends_on = None


def upgrade():
    # Remove server_default e torna NOT NULL
    op.alter_column(
        'coletas',
        'data_inicio_coleta',
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=None
    )

    # Garante que data_fim_coleta continua nullable
    op.alter_column(
        'coletas',
        'data_fim_coleta',
        existing_type=sa.DateTime(timezone=True),
        nullable=True
    )


def downgrade():
    # Reverte as alterações
    op.alter_column(
        'coletas',
        'data_inicio_coleta',
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
        server_default=sa.func.now()
    )

    op.alter_column(
        'coletas',
        'data_fim_coleta',
        existing_type=sa.DateTime(timezone=True),
        nullable=True
    )
