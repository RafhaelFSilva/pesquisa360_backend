"""cria tabelas para apuracao de relatorios

Revision ID: ff01e1adcb98
Revises: e3304b309d48
Create Date: 2025-09-30 18:50:41.598661

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'ff01e1adcb98'
down_revision: Union[str, None] = 'e3304b309d48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Tabela principal para a apuração
    op.create_table('apuracoes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('nome', sa.String(), nullable=False),
    sa.Column('descricao', sa.String(), nullable=True),
    sa.Column('data_criacao', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('pesquisa_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['pesquisa_id'], ['pesquisas.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_apuracoes_id'), 'apuracoes', ['id'], unique=False)

    # Tabela para cada análise salva dentro de uma apuração
    op.create_table('analises_salvas',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tipo_analise', sa.String(), nullable=False),
    sa.Column('configuracao', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('ordem', sa.Integer(), nullable=False),
    sa.Column('apuracao_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['apuracao_id'], ['apuracoes.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_analises_salvas_id'), 'analises_salvas', ['id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_analises_salvas_id'), table_name='analises_salvas')
    op.drop_table('analises_salvas')
    op.drop_index(op.f('ix_apuracoes_id'), table_name='apuracoes')
    op.drop_table('apuracoes')
