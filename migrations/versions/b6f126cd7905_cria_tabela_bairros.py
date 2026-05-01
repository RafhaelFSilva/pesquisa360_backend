"""cria_tabela_bairros

Revision ID: b6f126cd7905
Revises: 6de806306450
Create Date: 2026-04-21 21:57:30.613857

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import geoalchemy2 # <-- Importação obrigatória adicionada

# revision identifiers, used by Alembic.
revision: str = 'b6f126cd7905'
down_revision: Union[str, Sequence[str], None] = '6de806306450'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Criação APENAS da nossa tabela 'bairros'
    op.create_table('bairros',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nome', sa.String(), nullable=False),
        sa.Column('area_ha', sa.Float(), nullable=True),
        sa.Column('populacao', sa.Integer(), nullable=True),
        sa.Column('eleitores', sa.Integer(), nullable=True),
        # O GeoAlchemy cuidará do índice GIST desta coluna automaticamente
        sa.Column('geometria', geoalchemy2.types.Geometry(geometry_type='MULTIPOLYGON', srid=4326, dimension=2, from_text='ST_GeomFromEWKT', name='geometry', nullable=False), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    # 2. Índices normais
    op.create_index(op.f('ix_bairros_id'), 'bairros', ['id'], unique=False)
    op.create_index(op.f('ix_bairros_nome'), 'bairros', ['nome'], unique=False)


def downgrade() -> None:
    # 3. Exclusão APENAS da nossa tabela e seus índices
    op.drop_index(op.f('ix_bairros_nome'), table_name='bairros')
    op.drop_index(op.f('ix_bairros_id'), table_name='bairros')
    op.drop_table('bairros')