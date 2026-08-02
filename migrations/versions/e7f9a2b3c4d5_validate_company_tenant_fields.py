"""validate company tenant fields

Revision ID: e7f9a2b3c4d5
Revises: d4e8a1b2c3f4
"""

from typing import Sequence, Union
import re

from alembic import context
from alembic import op
import sqlalchemy as sa


revision: str = "e7f9a2b3c4d5"
down_revision: Union[str, Sequence[str], None] = "d4e8a1b2c3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _normalize_legacy_cnpj(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"[.\-/\s]", "", str(value).strip())
    if not normalized:
        return None
    if not normalized.isdigit() or len(normalized) != 14 or len(set(normalized)) == 1:
        raise RuntimeError("CNPJ existente invalido; corrija os dados antes da migration")

    def check_digit(base: str, weights: list[int]) -> str:
        remainder = sum(int(digit) * weight for digit, weight in zip(base, weights)) % 11
        return "0" if remainder < 2 else str(11 - remainder)

    first = check_digit(normalized[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    second = check_digit(normalized[:12] + first, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    if normalized[-2:] != first + second:
        raise RuntimeError("CNPJ existente invalido; corrija os dados antes da migration")
    return normalized


def upgrade() -> None:
    if context.is_offline_mode():
        with op.batch_alter_table("companies") as batch_op:
            batch_op.alter_column("cnpj", existing_type=sa.String(), type_=sa.String(14), nullable=True)
            batch_op.alter_column(
                "logo_url", existing_type=sa.String(), type_=sa.String(2048), nullable=True
            )
            batch_op.create_unique_constraint("uq_companies_cnpj", ["cnpj"])
        return

    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, cnpj FROM companies")).mappings().all()
    normalized_by_id = {}
    owners_by_cnpj = {}

    for row in rows:
        normalized = _normalize_legacy_cnpj(row["cnpj"])
        normalized_by_id[row["id"]] = normalized
        if normalized is not None:
            if normalized in owners_by_cnpj:
                raise RuntimeError(
                    "CNPJs duplicados apos normalizacao; corrija os dados antes da migration"
                )
            owners_by_cnpj[normalized] = row["id"]

    for company_id, normalized in normalized_by_id.items():
        connection.execute(
            sa.text("UPDATE companies SET cnpj = :cnpj WHERE id = :company_id"),
            {"cnpj": normalized, "company_id": company_id},
        )

    with op.batch_alter_table("companies") as batch_op:
        batch_op.alter_column("cnpj", existing_type=sa.String(), type_=sa.String(14), nullable=True)
        batch_op.alter_column(
            "logo_url", existing_type=sa.String(), type_=sa.String(2048), nullable=True
        )
        batch_op.create_unique_constraint("uq_companies_cnpj", ["cnpj"])


def downgrade() -> None:
    with op.batch_alter_table("companies") as batch_op:
        batch_op.drop_constraint("uq_companies_cnpj", type_="unique")
        batch_op.alter_column(
            "logo_url", existing_type=sa.String(2048), type_=sa.String(), nullable=True
        )
        batch_op.alter_column(
            "cnpj", existing_type=sa.String(14), type_=sa.String(), nullable=True
        )
