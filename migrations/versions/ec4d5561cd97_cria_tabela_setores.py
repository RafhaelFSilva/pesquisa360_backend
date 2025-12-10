"""cria_tabela_setores

Revision ID: ec4d5561cd97
Revises: 82e6e6df3ce4
Create Date: 2025-12-03 00:18:50.668755

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import geoalchemy2
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'ec4d5561cd97'
down_revision: Union[str, Sequence[str], None] = '82e6e6df3ce4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # --- MANTENHA APENAS A CRIAÇÃO DA TABELA SETORES ---
    op.create_table('setores',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nome', sa.String(), nullable=False),
        sa.Column('meta', sa.Integer(), nullable=False),
        # Note o tipo Geometry aqui
        sa.Column('geometria', geoalchemy2.types.Geometry(geometry_type='POLYGON', srid=4326, from_text='ST_GeomFromEWKT', name='geometry'), nullable=True),
        sa.Column('pesquisa_id', sa.Integer(), nullable=False),
        sa.Column('agente_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['agente_id'], ['usuarios.id'], ),
        sa.ForeignKeyConstraint(['pesquisa_id'], ['pesquisas.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    # A tabela setores já cria o índice sozinha. 
    # SE TIVER 'op.create_index' PARA 'idx_setores_geometria' ABAIXO, APAGUE TAMBÉM!

    # --- APAGUE TUDO QUE FOR op.drop_table('countysub_lookup'), 'faces', etc ---
    
def downgrade():
    # No downgrade, apenas apague a tabela setores
    op.drop_table('setores')
    # (Se tiver comandos recriando as tabelas do postgis aqui, pode apagar também)