"""cenarios de base eleitoral operacional na gestao de liderancas

Revision ID: 0d497634c513
Revises: 98d2bd125070
Create Date: 2026-09-16 12:00:00.000000

Aditiva (ADR-075). Cria `lideranca_cenarios` (cenario metodologico por onda)
e `lideranca_cenario_setores` (universo operacional por setor, com snapshot da
referencia oficial). Nao toca em base_eleitoral, territorio_eleitoral nem
setores: a Base Eleitoral oficial permanece somente leitura, e nenhum cenario
e inventado para projetos existentes -- sem cenario ATIVO a Gestao de
Liderancas segue o comportamento legado.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0d497634c513"
down_revision: Union[str, Sequence[str], None] = "98d2bd125070"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


STATUS_CENARIO = ("RASCUNHO", "ATIVO", "ARQUIVADO")


def _sql_in(coluna: str, valores) -> str:
    return "{} IN ({})".format(coluna, ", ".join("'{}'".format(item) for item in valores))


def upgrade() -> None:
    # --- 1. lideranca_cenarios -----------------------------------------------
    # Contexto da onda, como lideranca_pesquisa_config. Tenant deriva de
    # pesquisa -> projeto -> company_id; nao ha company_id aqui.
    op.create_table(
        "lideranca_cenarios",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pesquisa_id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(), nullable=False),
        sa.Column("metodologia", sa.Text(), nullable=True),
        sa.Column("data_referencia", sa.Date(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="RASCUNHO"),
        sa.Column("criado_por_id", sa.Integer(), nullable=True),
        sa.Column("ativado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ativado_por_id", sa.Integer(), nullable=True),
        sa.Column("arquivado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["pesquisa_id"], ["pesquisas.id"]),
        sa.ForeignKeyConstraint(["criado_por_id"], ["usuarios.id"]),
        sa.ForeignKeyConstraint(["ativado_por_id"], ["usuarios.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(_sql_in("status", STATUS_CENARIO), name="ck_lideranca_cenarios_status"),
    )
    op.create_index("ix_lideranca_cenarios_pesquisa", "lideranca_cenarios", ["pesquisa_id"])
    op.create_index("ix_lideranca_cenarios_status", "lideranca_cenarios", ["status"])
    # Um unico ATIVO por onda, garantido pelo banco: a aplicacao serializa a
    # ativacao, mas a ultima palavra e do indice parcial.
    ativo = sa.text("status = 'ATIVO'")
    op.create_index(
        "uq_lideranca_cenarios_ativo_por_pesquisa",
        "lideranca_cenarios",
        ["pesquisa_id"],
        unique=True,
        postgresql_where=ativo,
        sqlite_where=ativo,
    )

    # --- 2. lideranca_cenario_setores ----------------------------------------
    op.create_table(
        "lideranca_cenario_setores",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cenario_id", sa.Integer(), nullable=False),
        sa.Column("setor_id", sa.Integer(), nullable=False),
        sa.Column("eleitorado_oficial_referencia", sa.Integer(), nullable=True),
        sa.Column("eleitorado_operacional", sa.Integer(), nullable=False),
        sa.Column("observacao", sa.Text(), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["cenario_id"], ["lideranca_cenarios.id"], ondelete="CASCADE"),
        # Setor tem delete fisico (mesma politica de setor_territorio_eleitoral).
        sa.ForeignKeyConstraint(["setor_id"], ["setores.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cenario_id", "setor_id", name="uq_lideranca_cenario_setor"),
        sa.CheckConstraint(
            "eleitorado_operacional >= 0", name="ck_lideranca_cenario_setor_operacional"
        ),
        sa.CheckConstraint(
            "eleitorado_oficial_referencia IS NULL OR eleitorado_oficial_referencia >= 0",
            name="ck_lideranca_cenario_setor_oficial",
        ),
    )
    op.create_index(
        "ix_lideranca_cenario_setores_cenario", "lideranca_cenario_setores", ["cenario_id"]
    )
    op.create_index(
        "ix_lideranca_cenario_setores_setor", "lideranca_cenario_setores", ["setor_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_lideranca_cenario_setores_setor", table_name="lideranca_cenario_setores")
    op.drop_index("ix_lideranca_cenario_setores_cenario", table_name="lideranca_cenario_setores")
    op.drop_table("lideranca_cenario_setores")

    op.drop_index("uq_lideranca_cenarios_ativo_por_pesquisa", table_name="lideranca_cenarios")
    op.drop_index("ix_lideranca_cenarios_status", table_name="lideranca_cenarios")
    op.drop_index("ix_lideranca_cenarios_pesquisa", table_name="lideranca_cenarios")
    op.drop_table("lideranca_cenarios")
