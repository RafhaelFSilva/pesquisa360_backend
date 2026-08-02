"""add collection idempotency

Revision ID: d4e8a1b2c3f4
Revises: c8e4b1a2d9f0
"""

from typing import Sequence, Union
import uuid

from alembic import context
from alembic import op
import sqlalchemy as sa


revision: str = "d4e8a1b2c3f4"
down_revision: Union[str, Sequence[str], None] = "c8e4b1a2d9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if context.is_offline_mode():
        with op.batch_alter_table("coletas") as batch_op:
            batch_op.add_column(sa.Column("company_id", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("client_uuid", sa.String(length=36), nullable=True))

        op.execute(sa.text("""
            UPDATE coletas
            SET company_id = (
                SELECT usuarios.company_id
                FROM usuarios
                WHERE usuarios.id = coletas.agente_id
            ),
            client_uuid = md5(random()::text || clock_timestamp()::text)::uuid
        """))

        with op.batch_alter_table("coletas") as batch_op:
            batch_op.alter_column("company_id", existing_type=sa.Integer(), nullable=False)
            batch_op.alter_column("client_uuid", existing_type=sa.String(length=36), nullable=False)
            batch_op.create_foreign_key(
                "fk_coletas_company_id_companies",
                "companies",
                ["company_id"],
                ["id"],
            )
            batch_op.create_unique_constraint(
                "uq_coletas_company_client_uuid",
                ["company_id", "client_uuid"],
            )
        return

    connection = op.get_bind()
    legacy_rows = connection.execute(sa.text("""
        SELECT coletas.id, usuarios.company_id
        FROM coletas
        LEFT JOIN usuarios ON usuarios.id = coletas.agente_id
    """)).mappings().all()
    for row in legacy_rows:
        if row["company_id"] is None:
            raise RuntimeError("Coleta existente sem tenant nao pode receber client_uuid")

    with op.batch_alter_table("coletas") as batch_op:
        batch_op.add_column(sa.Column("company_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("client_uuid", sa.String(length=36), nullable=True))

    for row in legacy_rows:
        connection.execute(
            sa.text("""
                UPDATE coletas
                SET company_id = :company_id, client_uuid = :client_uuid
                WHERE id = :coleta_id
            """),
            {
                "company_id": row["company_id"],
                "client_uuid": str(uuid.uuid4()),
                "coleta_id": row["id"],
            },
        )

    with op.batch_alter_table("coletas") as batch_op:
        batch_op.alter_column("company_id", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("client_uuid", existing_type=sa.String(length=36), nullable=False)
        batch_op.create_foreign_key(
            "fk_coletas_company_id_companies",
            "companies",
            ["company_id"],
            ["id"],
        )
        batch_op.create_unique_constraint(
            "uq_coletas_company_client_uuid",
            ["company_id", "client_uuid"],
        )


def downgrade() -> None:
    with op.batch_alter_table("coletas") as batch_op:
        batch_op.drop_constraint("uq_coletas_company_client_uuid", type_="unique")
        batch_op.drop_constraint("fk_coletas_company_id_companies", type_="foreignkey")
        batch_op.drop_column("client_uuid")
        batch_op.drop_column("company_id")
