"""add posicionamento to liderancas politicas

Revision ID: a1b2c3d4e5f6
Revises: 78d895f396e9
Create Date: 2026-08-23 10:00:00.000000

BASE / OPOSICAO / INDEFINIDA como ATRIBUTO da lideranca. Nao ha entidade
separada para oposicao: a mesma liderancas_politicas descreve os dois campos
politicos.

Aditiva e reversivel. Lideranca ja cadastrada recebe INDEFINIDA pelo
server_default: presumir BASE alteraria silenciosamente o significado de dados
existentes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "78d895f396e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


POSICIONAMENTOS_LIDERANCA = ("BASE", "OPOSICAO", "INDEFINIDA")
POSICIONAMENTO_LIDERANCA_PADRAO = "INDEFINIDA"

CK_POSICIONAMENTO = "ck_liderancas_politicas_posicionamento"
IX_POSICIONAMENTO = "ix_liderancas_politicas_posicionamento"


def _sql_in(coluna: str, valores) -> str:
    return "{} IN ({})".format(coluna, ", ".join("'{}'".format(item) for item in valores))


def upgrade() -> None:
    op.add_column(
        "liderancas_politicas",
        sa.Column(
            "posicionamento",
            sa.String(),
            nullable=False,
            server_default=POSICIONAMENTO_LIDERANCA_PADRAO,
        ),
    )
    op.create_index(IX_POSICIONAMENTO, "liderancas_politicas", ["posicionamento"])

    # SQLite nao suporta ALTER TABLE ADD CONSTRAINT e a suite de migrations roda
    # nele. Mesma estrategia por dialeto ja usada em 8c2097e0a3de; o predicado
    # tambem vive no model, entao um create_all traz o CHECK em qualquer banco.
    if op.get_bind().dialect.name == "postgresql":
        op.create_check_constraint(
            CK_POSICIONAMENTO,
            "liderancas_politicas",
            _sql_in("posicionamento", POSICIONAMENTOS_LIDERANCA),
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(CK_POSICIONAMENTO, "liderancas_politicas", type_="check")
    op.drop_index(IX_POSICIONAMENTO, table_name="liderancas_politicas")
    op.drop_column("liderancas_politicas", "posicionamento")
