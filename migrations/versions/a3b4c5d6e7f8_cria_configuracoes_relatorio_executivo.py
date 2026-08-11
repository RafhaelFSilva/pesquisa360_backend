"""cria configuracoes de relatorio executivo

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-08-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, Sequence[str], None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "configuracoes_relatorio_executivo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pesquisa_id", sa.Integer(), nullable=False),
        sa.Column("tipo_relatorio", sa.String(), nullable=False),
        sa.Column("nome", sa.String(), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("parametros_gerais", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ativo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("criado_por_id", sa.Integer(), nullable=False),
        sa.Column("atualizado_por_id", sa.Integer(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["atualizado_por_id"], ["usuarios.id"]),
        sa.ForeignKeyConstraint(["criado_por_id"], ["usuarios.id"]),
        sa.ForeignKeyConstraint(["pesquisa_id"], ["pesquisas.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_configuracoes_relatorio_executivo_id", "configuracoes_relatorio_executivo", ["id"])
    op.create_index("ix_configuracoes_relatorio_executivo_pesquisa_id", "configuracoes_relatorio_executivo", ["pesquisa_id"])
    op.create_index("ix_configuracoes_relatorio_executivo_tipo_relatorio", "configuracoes_relatorio_executivo", ["tipo_relatorio"])
    op.create_index("ix_configuracoes_relatorio_executivo_ativo", "configuracoes_relatorio_executivo", ["ativo"])

    op.create_table(
        "secoes_relatorio_executivo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("configuracao_id", sa.Integer(), nullable=False),
        sa.Column("ordem", sa.Integer(), nullable=False),
        sa.Column("tipo_secao", sa.String(), nullable=False),
        sa.Column("titulo", sa.String(), nullable=True),
        sa.Column("ativo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.ForeignKeyConstraint(["configuracao_id"], ["configuracoes_relatorio_executivo.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_secoes_relatorio_executivo_id", "secoes_relatorio_executivo", ["id"])
    op.create_index("ix_secoes_relatorio_executivo_configuracao_id", "secoes_relatorio_executivo", ["configuracao_id"])
    op.create_index("ix_secoes_relatorio_executivo_ativo", "secoes_relatorio_executivo", ["ativo"])
    op.create_index("ix_secoes_relatorio_executivo_configuracao_ordem", "secoes_relatorio_executivo", ["configuracao_id", "ordem"])

    op.create_table(
        "analises_relatorio_executivo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("secao_id", sa.Integer(), nullable=False),
        sa.Column("ordem", sa.Integer(), nullable=False),
        sa.Column("tipo_analise", sa.String(), nullable=False),
        sa.Column("titulo_customizado", sa.String(), nullable=True),
        sa.Column("parametros", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ativo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.ForeignKeyConstraint(["secao_id"], ["secoes_relatorio_executivo.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analises_relatorio_executivo_id", "analises_relatorio_executivo", ["id"])
    op.create_index("ix_analises_relatorio_executivo_secao_id", "analises_relatorio_executivo", ["secao_id"])
    op.create_index("ix_analises_relatorio_executivo_tipo_analise", "analises_relatorio_executivo", ["tipo_analise"])
    op.create_index("ix_analises_relatorio_executivo_ativo", "analises_relatorio_executivo", ["ativo"])
    op.create_index("ix_analises_relatorio_executivo_secao_ordem", "analises_relatorio_executivo", ["secao_id", "ordem"])


def downgrade() -> None:
    op.drop_table("analises_relatorio_executivo")
    op.drop_table("secoes_relatorio_executivo")
    op.drop_table("configuracoes_relatorio_executivo")
