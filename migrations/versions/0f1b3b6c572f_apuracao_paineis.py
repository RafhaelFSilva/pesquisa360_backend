"""paineis personalizados da apuracao eleitoral (configuracao por tenant)

Revision ID: 0f1b3b6c572f
Revises: cd95ebf79450
Create Date: 2026-10-04 15:45:00.000000

Aditiva (ADR-085). Os resultados do TSE continuam globais; o painel e a
selecao que uma empresa faz sobre eles e por isso tem `company_id`. A eleicao
e referenciada pela chave natural (origem + pleito + codigo), sem FK para
`tse_*`: nenhuma tabela de tenant depende do dominio TSE.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0f1b3b6c572f"
down_revision: Union[str, Sequence[str], None] = "cd95ebf79450"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "apuracao_paineis",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(length=120), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("origem", sa.String(length=10), nullable=False),
        sa.Column("pleito", sa.String(length=10), nullable=False),
        sa.Column("codigo_eleicao", sa.String(length=10), nullable=False),
        sa.Column("uf", sa.String(length=2), nullable=False),
        sa.Column("criado_por_id", sa.Integer(), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "atualizado_em", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["criado_por_id"], ["usuarios.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("origem IN ('OFICIAL', 'SIMULADO')", name="ck_apuracao_paineis_origem"),
    )
    op.create_index("ix_apuracao_paineis_company", "apuracao_paineis", ["company_id"])
    op.create_table(
        "apuracao_painel_itens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("painel_id", sa.Integer(), nullable=False),
        sa.Column("tipo", sa.String(length=12), nullable=False),
        sa.Column("cargo_codigo", sa.String(length=4), nullable=False),
        sa.Column("sqcand", sa.String(length=20), nullable=True),
        sa.Column("partido_numero", sa.String(length=5), nullable=True),
        sa.Column("federacao_numero", sa.String(length=10), nullable=True),
        sa.Column("ordem", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.ForeignKeyConstraint(["painel_id"], ["apuracao_paineis.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "tipo IN ('CARGO', 'CANDIDATO', 'NOMINATA')", name="ck_apuracao_painel_itens_tipo"
        ),
        sa.CheckConstraint(
            "tipo <> 'CANDIDATO' OR sqcand IS NOT NULL", name="ck_apuracao_painel_itens_candidato"
        ),
    )
    op.create_index(
        "ix_apuracao_painel_itens_painel", "apuracao_painel_itens", ["painel_id", "ordem"]
    )


def downgrade() -> None:
    op.drop_table("apuracao_painel_itens")
    op.drop_table("apuracao_paineis")
