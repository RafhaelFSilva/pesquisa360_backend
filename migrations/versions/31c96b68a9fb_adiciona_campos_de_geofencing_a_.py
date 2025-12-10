"""Adiciona campos de geofencing a pesquisas e coletas

Revision ID: 31c96b68a9fb
Revises: 0a7c5d879687
Create Date: 2025-09-20 12:51:15.863950

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geometry # Importa o tipo Geometry

# revision identifiers, used by Alembic.
revision: str = '31c96b68a9fb' # COLOQUE O ID DESTE FICHEIRO AQUI
down_revision: Union[str, None] = '0a7c5d879687' # COLOQUE O ID DA MIGRAÇÃO ANTERIOR
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Adiciona colunas à tabela 'pesquisas'
    op.add_column('pesquisas', sa.Column('cerca_eletronica', Geometry(geometry_type='POLYGON', srid=4326), nullable=True))
    op.add_column('pesquisas', sa.Column('tolerancia_metros', sa.Integer(), nullable=True))

    # Adiciona coluna à tabela 'coletas'
    op.add_column('coletas', sa.Column('inconformidade_localizacao', sa.Boolean(), nullable=False, server_default=sa.text('false')))


def downgrade() -> None:
    # Remove as colunas na ordem inversa
    op.drop_column('coletas', 'inconformidade_localizacao')
    op.drop_column('pesquisas', 'tolerancia_metros')
    op.drop_column('pesquisas', 'cerca_eletronica')
