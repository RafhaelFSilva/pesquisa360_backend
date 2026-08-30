"""cria setor agentes

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-26 00:00:00.000000

Fundacao aditiva para Setor N:N Agentes. ``setores.agente_id`` permanece
intacto para compatibilidade. O downgrade remove apenas a nova estrutura:
vinculos N:N adicionais criados depois do upgrade nao podem ser representados
integralmente pelo campo singular legado e nao sao condensados artificialmente.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "setor_agentes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("setor_id", sa.Integer(), nullable=False),
        sa.Column("agente_id", sa.Integer(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["setor_id"], ["setores.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agente_id"], ["usuarios.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "setor_id",
            "agente_id",
            name="uq_setor_agentes_setor_agente",
        ),
    )
    op.create_index("ix_setor_agentes_setor_id", "setor_agentes", ["setor_id"])
    op.create_index("ix_setor_agentes_agente_id", "setor_agentes", ["agente_id"])

    # NOT EXISTS evita duplicidade mesmo diante de uma execucao operacional
    # atipica que ja tenha inserido o vinculo dentro desta mesma revisao.
    op.execute(
        sa.text(
            """
            INSERT INTO setor_agentes (setor_id, agente_id, ativo)
            SELECT s.id, s.agente_id, TRUE
              FROM setores AS s
             WHERE s.agente_id IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1
                     FROM setor_agentes AS sa
                    WHERE sa.setor_id = s.id
                      AND sa.agente_id = s.agente_id
               )
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_setor_agentes_agente_id", table_name="setor_agentes")
    op.drop_index("ix_setor_agentes_setor_id", table_name="setor_agentes")
    op.drop_table("setor_agentes")
