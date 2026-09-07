"""adiciona identificacao auditavel de coletas sinteticas

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-09-06 00:00:00.000000

Migration aditiva: coletas existentes permanecem reais por default, sem
seed_run_id, origem ou operador sintetico.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c6d7e8f9a0b1"
down_revision: Union[str, Sequence[str], None] = "b5c6d7e8f9a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX = "ix_coletas_company_seed_run_id"
CHECK = "ck_coletas_synthetic_metadata"
FK = "fk_coletas_synthetic_operator_id_usuarios"


def upgrade() -> None:
    with op.batch_alter_table("coletas") as batch:
        batch.add_column(
            sa.Column(
                "is_synthetic", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch.add_column(sa.Column("seed_run_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("synthetic_source", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("synthetic_operator_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            FK, "usuarios", ["synthetic_operator_id"], ["id"]
        )
        batch.create_check_constraint(
            CHECK,
            "(is_synthetic IS TRUE AND seed_run_id IS NOT NULL "
            "AND synthetic_source IS NOT NULL AND synthetic_operator_id IS NOT NULL) OR "
            "(is_synthetic IS FALSE AND seed_run_id IS NULL "
            "AND synthetic_source IS NULL AND synthetic_operator_id IS NULL)",
        )
    op.create_index(
        INDEX,
        "coletas",
        ["company_id", "seed_run_id"],
        unique=False,
        postgresql_where=sa.text("seed_run_id IS NOT NULL"),
        sqlite_where=sa.text("seed_run_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="coletas")
    with op.batch_alter_table("coletas") as batch:
        batch.drop_constraint(CHECK, type_="check")
        batch.drop_constraint(FK, type_="foreignkey")
        batch.drop_column("synthetic_operator_id")
        batch.drop_column("synthetic_source")
        batch.drop_column("seed_run_id")
        batch.drop_column("is_synthetic")
