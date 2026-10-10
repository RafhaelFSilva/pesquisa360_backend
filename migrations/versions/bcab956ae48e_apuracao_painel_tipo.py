"""tipo do painel de apuracao (GERAL | DISTRIBUICAO_TERRITORIAL)

Revision ID: bcab956ae48e
Revises: 0f1b3b6c572f
Create Date: 2026-10-04 20:30:00.000000

Aditiva. Paineis existentes passam a ser GERAL (comportamento atual). O novo
tipo DISTRIBUICAO_TERRITORIAL reusa `apuracao_painel_itens` (CANDIDATO e
NOMINATA de um unico cargo): nao ha coluna JSON de configuracao.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "bcab956ae48e"
down_revision: Union[str, Sequence[str], None] = "0f1b3b6c572f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CHECK = "ck_apuracao_paineis_tipo"


def upgrade() -> None:
    # batch: o SQLite da suite nao adiciona CHECK por ALTER TABLE.
    with op.batch_alter_table("apuracao_paineis") as batch:
        batch.add_column(sa.Column(
            "tipo", sa.String(length=30), nullable=False, server_default=sa.text("'GERAL'")))
        batch.create_check_constraint(CHECK, "tipo IN ('GERAL', 'DISTRIBUICAO_TERRITORIAL')")


def downgrade() -> None:
    with op.batch_alter_table("apuracao_paineis") as batch:
        batch.drop_constraint(CHECK, type_="check")
        batch.drop_column("tipo")
