"""add setor id to coletas

Revision ID: d5e6f7a8b9c0
Revises: c3d4e5f6a7b8
Create Date: 2026-08-26 00:00:00.000000

Associacao operacional aditiva e nullable. Nao realiza backfill espacial.
A FK nao usa CASCADE para preservar a integridade historica das coletas.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("coletas") as batch_op:
            batch_op.add_column(sa.Column("setor_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_coletas_setor_id_setores",
                "setores",
                ["setor_id"],
                ["id"],
            )
            batch_op.create_index("ix_coletas_setor_id", ["setor_id"])
        return

    op.add_column("coletas", sa.Column("setor_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_coletas_setor_id_setores",
        "coletas",
        "setores",
        ["setor_id"],
        ["id"],
    )
    op.create_index("ix_coletas_setor_id", "coletas", ["setor_id"])


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("coletas") as batch_op:
            batch_op.drop_index("ix_coletas_setor_id")
            batch_op.drop_constraint("fk_coletas_setor_id_setores", type_="foreignkey")
            batch_op.drop_column("setor_id")
        return

    op.drop_index("ix_coletas_setor_id", table_name="coletas")
    op.drop_constraint(
        "fk_coletas_setor_id_setores",
        "coletas",
        type_="foreignkey",
    )
    op.drop_column("coletas", "setor_id")
