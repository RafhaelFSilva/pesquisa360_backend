"""tentativas de campo

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-08-27 00:00:00.000000

PROMPT 03. Tabela `tentativas_campo`: abordagem operacional do agente
(recusa, nao elegivel, desistencia, incompleta, problema tecnico, outro,
concluida). Aditiva: nao toca em coletas, respostas nem setores. O downgrade
remove apenas a tabela.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tentativas_campo",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_uuid", sa.String(length=36), nullable=False),
        sa.Column("pesquisa_id", sa.Integer(), sa.ForeignKey("pesquisas.id"), nullable=False),
        sa.Column("setor_id", sa.Integer(), sa.ForeignKey("setores.id"), nullable=True),
        sa.Column("agente_id", sa.Integer(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("iniciada_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("encerrada_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("precisao_metros", sa.Float(), nullable=True),
        sa.Column("capturada_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resultado", sa.String(length=30), nullable=False),
        sa.Column("motivo", sa.String(length=60), nullable=True),
        sa.Column("observacao", sa.Text(), nullable=True),
        sa.Column("coleta_id", sa.Integer(), sa.ForeignKey("coletas.id"), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "company_id", "client_uuid", name="uq_tentativas_campo_company_client_uuid"
        ),
    )
    op.create_index("ix_tentativas_campo_id", "tentativas_campo", ["id"])
    op.create_index("ix_tentativas_campo_pesquisa_id", "tentativas_campo", ["pesquisa_id"])
    op.create_index("ix_tentativas_campo_setor_id", "tentativas_campo", ["setor_id"])
    op.create_index("ix_tentativas_campo_agente_id", "tentativas_campo", ["agente_id"])


def downgrade() -> None:
    op.drop_index("ix_tentativas_campo_agente_id", table_name="tentativas_campo")
    op.drop_index("ix_tentativas_campo_setor_id", table_name="tentativas_campo")
    op.drop_index("ix_tentativas_campo_pesquisa_id", table_name="tentativas_campo")
    op.drop_index("ix_tentativas_campo_id", table_name="tentativas_campo")
    op.drop_table("tentativas_campo")
