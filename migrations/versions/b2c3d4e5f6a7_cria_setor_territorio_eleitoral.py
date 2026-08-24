"""cria setor territorio eleitoral

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-23 23:10:00.000000

Composicao eleitoral do Setor: quais unidades da Base Eleitoral compoem o
universo de um Setor. Associacao N:N declarada pelo usuario -- nao ha inferencia
geometrica nem backfill.

Aditiva. Nenhum setor existente recebe vinculo: todos comecam com 0 unidades
ate configuracao explicita. Nenhum dado eleitoral e tocado.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "setor_territorio_eleitoral",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("setor_id", sa.Integer(), nullable=False),
        sa.Column("territorio_eleitoral_id", sa.Integer(), nullable=False),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # Setor tem delete fisico; o vinculo nao sobrevive sozinho.
        sa.ForeignKeyConstraint(["setor_id"], ["setores.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["territorio_eleitoral_id"], ["territorio_eleitoral.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        # Mesmo bairro duas vezes no mesmo setor e impossivel por construcao.
        # A exclusividade ENTRE setores analiticos depende de setores.finalidade
        # e por isso vive no servico, nao aqui.
        sa.UniqueConstraint(
            "setor_id", "territorio_eleitoral_id", name="uq_setor_territorio"
        ),
    )
    op.create_index("ix_setor_territorio_setor", "setor_territorio_eleitoral", ["setor_id"])
    op.create_index(
        "ix_setor_territorio_territorio",
        "setor_territorio_eleitoral",
        ["territorio_eleitoral_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_setor_territorio_territorio", table_name="setor_territorio_eleitoral")
    op.drop_index("ix_setor_territorio_setor", table_name="setor_territorio_eleitoral")
    op.drop_table("setor_territorio_eleitoral")
