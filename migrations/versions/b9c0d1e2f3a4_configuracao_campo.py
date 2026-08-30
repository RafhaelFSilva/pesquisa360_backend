"""configuracao de campo da pesquisa

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-08-27 15:00:00.000000

PROMPT 06. `configuracoes_campo_pesquisa`: parametros operacionais de campo
por pesquisa (hoje: distancia recomendada entre abordagens, em metros,
nullable). Tabela propria em vez de coluna em `pesquisas` para nao ampliar o
contrato legado. Aditiva; downgrade remove so a tabela.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b9c0d1e2f3a4"
down_revision: Union[str, Sequence[str], None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "configuracoes_campo_pesquisa",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pesquisa_id", sa.Integer(), sa.ForeignKey("pesquisas.id"), nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("distancia_recomendada_entre_abordagens_metros", sa.Integer(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("pesquisa_id", name="uq_configuracoes_campo_pesquisa"),
        sa.CheckConstraint(
            "distancia_recomendada_entre_abordagens_metros IS NULL OR distancia_recomendada_entre_abordagens_metros > 0",
            name="ck_configuracoes_campo_distancia",
        ),
    )
    op.create_index("ix_configuracoes_campo_pesquisa_id", "configuracoes_campo_pesquisa", ["id"])


def downgrade() -> None:
    op.drop_index("ix_configuracoes_campo_pesquisa_id", table_name="configuracoes_campo_pesquisa")
    op.drop_table("configuracoes_campo_pesquisa")
