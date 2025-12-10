"""Adiciona tabela de perguntas manualmente

Revision ID: 89c139bfcbe9
Revises: 2fb2c10a30a1
Create Date: 2025-08-08 13:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '89c139bfcbe9' # COLOQUE O ID DESTE ARQUIVO AQUI
down_revision: Union[str, None] = '2fb2c10a30a1' # COLOQUE O ID DA MIGRAÇÃO ANTERIOR (PROJETOS) AQUI
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('perguntas',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('texto_pergunta', sa.Text(), nullable=False),
    sa.Column('tipo_pergunta', sa.String(), nullable=False),
    sa.Column('ordem', sa.Integer(), nullable=False),
    sa.Column('eh_obrigatoria', sa.Boolean(), nullable=True),
    sa.Column('opcoes', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('pesquisa_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['pesquisa_id'], ['pesquisas.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_perguntas_id'), 'perguntas', ['id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_perguntas_id'), table_name='perguntas')
    op.drop_table('perguntas')
