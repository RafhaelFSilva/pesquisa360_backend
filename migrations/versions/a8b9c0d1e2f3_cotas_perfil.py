"""cotas de perfil amostral

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-08-27 12:00:00.000000

PROMPT 05. `planos_cota_perfil` (um por pesquisa: perguntas de sexo/idade e
mapa de valores) e `cotas_perfil` (celula municipio x sexo x faixa etaria com
meta). Aditiva; nao toca em coletas, respostas, setores nem tentativas.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "planos_cota_perfil",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pesquisa_id", sa.Integer(), sa.ForeignKey("pesquisas.id"), nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("pergunta_sexo_id", sa.Integer(), sa.ForeignKey("perguntas.id"), nullable=False),
        sa.Column("pergunta_idade_id", sa.Integer(), sa.ForeignKey("perguntas.id"), nullable=False),
        sa.Column("modo_idade", sa.String(length=20), nullable=False, server_default="NUMERICA"),
        sa.Column("sexo_valores", sa.JSON(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("pesquisa_id", name="uq_planos_cota_perfil_pesquisa"),
    )
    op.create_index("ix_planos_cota_perfil_id", "planos_cota_perfil", ["id"])
    op.create_table(
        "cotas_perfil",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "plano_id", sa.Integer(),
            sa.ForeignKey("planos_cota_perfil.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "territorio_eleitoral_id", sa.Integer(),
            sa.ForeignKey("territorio_eleitoral.id"), nullable=False,
        ),
        sa.Column("sexo", sa.String(length=20), nullable=False),
        sa.Column("faixa_rotulo", sa.String(length=40), nullable=False),
        sa.Column("idade_min", sa.Integer(), nullable=True),
        sa.Column("idade_max", sa.Integer(), nullable=True),
        sa.Column("idade_valores", sa.JSON(), nullable=True),
        sa.Column("meta", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ordem", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("meta >= 0", name="ck_cotas_perfil_meta"),
    )
    op.create_index("ix_cotas_perfil_id", "cotas_perfil", ["id"])
    op.create_index("ix_cotas_perfil_plano_id", "cotas_perfil", ["plano_id"])
    op.create_index("ix_cotas_perfil_territorio_id", "cotas_perfil", ["territorio_eleitoral_id"])


def downgrade() -> None:
    op.drop_index("ix_cotas_perfil_territorio_id", table_name="cotas_perfil")
    op.drop_index("ix_cotas_perfil_plano_id", table_name="cotas_perfil")
    op.drop_index("ix_cotas_perfil_id", table_name="cotas_perfil")
    op.drop_table("cotas_perfil")
    op.drop_index("ix_planos_cota_perfil_id", table_name="planos_cota_perfil")
    op.drop_table("planos_cota_perfil")
