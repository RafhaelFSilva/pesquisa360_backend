"""Adiciona tabelas coletas e respostas manualmente

Revision ID: 7aa2d03b52b5
Revises: 89c139bfcbe9
Create Date: 2025-08-08 14:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geometry

# revision identifiers, used by Alembic.
revision: str = '7aa2d03b52b5'
down_revision: Union[str, None] = '89c139bfcbe9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 🔑 GARANTE QUE O POSTGIS EXISTA
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    # ---- tabela coletas ----
    op.create_table(
        'coletas',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('pesquisa_id', sa.Integer(), nullable=False),
        sa.Column('agente_id', sa.Integer(), nullable=False),
        sa.Column(
            'data_inicio_coleta',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=True
        ),
        sa.Column(
            'data_fim_coleta',
            sa.DateTime(timezone=True),
            nullable=True
        ),
        sa.Column(
            'localizacao_inicio',
            Geometry(geometry_type='POINT', srid=4326),
            nullable=True
        ),
        sa.Column(
            'localizacao_fim',
            Geometry(geometry_type='POINT', srid=4326),
            nullable=True
        ),
        sa.ForeignKeyConstraint(
            ['pesquisa_id'],
            ['pesquisas.id'],
            ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['agente_id'],
            ['usuarios.id'],
            ondelete='CASCADE'
        ),
    )

    op.create_index(
        'ix_coletas_id',
        'coletas',
        ['id'],
        unique=False
    )

    # ---- tabela respostas ----
    op.create_table(
        'respostas',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('pergunta_id', sa.Integer(), nullable=False),
        sa.Column('coleta_id', sa.Integer(), nullable=False),
        sa.Column('valor_resposta', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ['coleta_id'],
            ['coletas.id'],
            ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['pergunta_id'],
            ['perguntas.id'],
            ondelete='CASCADE'
        ),
    )

    op.create_index(
        'ix_respostas_id',
        'respostas',
        ['id'],
        unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_respostas_id', table_name='respostas')
    op.drop_table('respostas')

    op.drop_index('ix_coletas_id', table_name='coletas')
    op.drop_table('coletas')
