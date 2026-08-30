"""pergunta aplicabilidade territorial

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-08-26 00:00:00.000000

FASE F. Duas alteracoes aditivas:

1. `perguntas.aplicabilidade` (GLOBAL | TERRITORIAL), com backfill GLOBAL para
   TODAS as perguntas existentes -- o questionario legado nao muda.
2. `pergunta_territorio_eleitoral`: municipios (TerritorioEleitoral de tipo
   MUNICIPIO) em que uma pergunta TERRITORIAL e apresentada.

Nao toca em coletas, respostas, setores nem base eleitoral. O downgrade remove
a tabela e a coluna e nada mais.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default garante o backfill no proprio ADD COLUMN: cada linha
    # existente nasce GLOBAL, e insercoes que ainda nao conhecem o campo
    # (cliente antigo) tambem.
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("perguntas") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "aplicabilidade",
                    sa.String(length=20),
                    nullable=False,
                    server_default="GLOBAL",
                )
            )
    else:
        op.add_column(
            "perguntas",
            sa.Column(
                "aplicabilidade",
                sa.String(length=20),
                nullable=False,
                server_default="GLOBAL",
            ),
        )

    # Backfill explicito alem do default: deixa a intencao registrada mesmo
    # em dialetos que preencham NULL antes do default.
    op.execute(
        sa.text(
            "UPDATE perguntas SET aplicabilidade = 'GLOBAL' WHERE aplicabilidade IS NULL"
        )
    )

    op.create_table(
        "pergunta_territorio_eleitoral",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pergunta_id", sa.Integer(), nullable=False),
        sa.Column("territorio_eleitoral_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["pergunta_id"], ["perguntas.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["territorio_eleitoral_id"], ["territorio_eleitoral.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pergunta_id", "territorio_eleitoral_id", name="uq_pergunta_territorio"
        ),
    )
    op.create_index(
        "ix_pergunta_territorio_pergunta",
        "pergunta_territorio_eleitoral",
        ["pergunta_id"],
    )
    op.create_index(
        "ix_pergunta_territorio_territorio",
        "pergunta_territorio_eleitoral",
        ["territorio_eleitoral_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pergunta_territorio_territorio", table_name="pergunta_territorio_eleitoral"
    )
    op.drop_index(
        "ix_pergunta_territorio_pergunta", table_name="pergunta_territorio_eleitoral"
    )
    op.drop_table("pergunta_territorio_eleitoral")

    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("perguntas") as batch_op:
            batch_op.drop_column("aplicabilidade")
    else:
        op.drop_column("perguntas", "aplicabilidade")
