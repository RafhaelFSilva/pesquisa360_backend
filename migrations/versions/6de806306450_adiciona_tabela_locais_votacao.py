"""adiciona_tabela_locais_votacao

Revision ID: 6de806306450
Revises: 28f012bafc15
Create Date: 2026-04-21 15:42:11.995775

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import geoalchemy2

# revision identifiers, used by Alembic.
revision: str = '6de806306450'
down_revision: Union[str, Sequence[str], None] = '28f012bafc15'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Criação da tabela locais_votacao
    op.create_table('locais_votacao',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nome', sa.String(), nullable=False),
        sa.Column('zona', sa.Integer(), nullable=True),
        sa.Column('secoes', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('municipio', sa.String(), nullable=True),
        sa.Column('bairro', sa.String(), nullable=True),
        sa.Column('endereco', sa.String(), nullable=True),
        sa.Column('localizacao', geoalchemy2.types.Geometry(geometry_type='POINT', srid=4326, from_text='ST_GeomFromEWKT', name='geometry', nullable=True), nullable=True),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Criação APENAS dos índices normais (o PostGIS já cuidou do geográfico)
    op.create_index(op.f('ix_locais_votacao_id'), 'locais_votacao', ['id'], unique=False)
    op.create_index(op.f('ix_locais_votacao_zona'), 'locais_votacao', ['zona'], unique=False)


def downgrade() -> None:
    # Exclusão APENAS dos índices normais e tabela
    op.drop_index(op.f('ix_locais_votacao_zona'), table_name='locais_votacao')
    op.drop_index(op.f('ix_locais_votacao_id'), table_name='locais_votacao')
    op.drop_table('locais_votacao')