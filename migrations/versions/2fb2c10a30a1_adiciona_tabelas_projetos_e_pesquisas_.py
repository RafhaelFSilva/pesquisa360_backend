"""Adiciona tabelas projetos e pesquisas manualmente

Revision ID: 2fb2c10a30a1
Revises: 585bce4dc799
Create Date: 2025-08-07 17:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2fb2c10a30a1' # COLOQUE O ID DESTE ARQUIVO AQUI
down_revision: Union[str, None] = '585bce4dc799' # COLOQUE O ID DA MIGRAÇÃO ANTERIOR (USUARIOS) AQUI
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### Comandos para criar PROJETOS ###
    op.create_table('projetos',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('nome', sa.String(), nullable=False),
    sa.Column('descricao', sa.String(), nullable=True),
    sa.Column('status', sa.String(), nullable=True),
    #sa.Column('data_inicio', sa.Date(), nullable=True),
    #sa.Column('data_fim', sa.Date(), nullable=True),
    sa.Column('coordenador_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['coordenador_id'], ['usuarios.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_projetos_id'), 'projetos', ['id'], unique=False)
    op.create_index(op.f('ix_projetos_nome'), 'projetos', ['nome'], unique=False)

    # ### Comandos para criar PESQUISAS ###
    op.create_table('pesquisas',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('titulo', sa.String(), nullable=False),
    sa.Column('tipo_pesquisa', sa.String(), nullable=True),
    sa.Column('projeto_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['projeto_id'], ['projetos.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_pesquisas_id'), 'pesquisas', ['id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_pesquisas_id'), table_name='pesquisas')
    op.drop_table('pesquisas')
    op.drop_index(op.f('ix_projetos_nome'), table_name='projetos')
    op.drop_index(op.f('ix_projetos_id'), table_name='projetos')
    op.drop_table('projetos')
