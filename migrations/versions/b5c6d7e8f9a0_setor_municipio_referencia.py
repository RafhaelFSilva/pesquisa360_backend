"""setor: municipio operacional de referencia (ADR-035)

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-08-28 00:00:00.000000

Formaliza "este setor operacional pertence a qual municipio?":
`setores.municipio_territorio_id` -> `territorio_eleitoral.id` (tipo MUNICIPIO
da Base Eleitoral principal do Projeto). Aditiva e nullable: setores
historicos e analiticos multi-municipais seguem validos.

Backfill SOMENTE de setores (nunca coletas/respostas): quando a composicao
eleitoral resolve exatamente UM municipio, ele e persistido; ambiguo ou sem
composicao fica NULL -- nunca "o primeiro". O downgrade remove apenas a coluna.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b5c6d7e8f9a0"
down_revision: Union[str, Sequence[str], None] = "a4b5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDICE = "ix_setores_municipio_territorio_id"
FK = "fk_setores_municipio_territorio_id"


def upgrade() -> None:
    with op.batch_alter_table("setores") as batch:
        batch.add_column(sa.Column("municipio_territorio_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(FK, "territorio_eleitoral", ["municipio_territorio_id"], ["id"])
    op.create_index(INDICE, "setores", ["municipio_territorio_id"])

    # Backfill pela composicao eleitoral: bairro -> municipio_id -> MUNICIPIO.
    # So preenche quando ha exatamente um municipio distinto.
    op.execute(
        """
        UPDATE setores
           SET municipio_territorio_id = (
                SELECT MIN(m.id)
                  FROM setor_territorio_eleitoral st
                  JOIN territorio_eleitoral b ON b.id = st.territorio_eleitoral_id
                  JOIN territorio_eleitoral m ON m.id = b.municipio_id
                                             AND m.tipo = 'MUNICIPIO'
                                             AND m.base_eleitoral_id = b.base_eleitoral_id
                 WHERE st.setor_id = setores.id
           )
         WHERE municipio_territorio_id IS NULL
           AND (
                SELECT COUNT(DISTINCT m.id)
                  FROM setor_territorio_eleitoral st
                  JOIN territorio_eleitoral b ON b.id = st.territorio_eleitoral_id
                  JOIN territorio_eleitoral m ON m.id = b.municipio_id
                                             AND m.tipo = 'MUNICIPIO'
                                             AND m.base_eleitoral_id = b.base_eleitoral_id
                 WHERE st.setor_id = setores.id
           ) = 1
        """
    )


def downgrade() -> None:
    op.drop_index(INDICE, table_name="setores")
    with op.batch_alter_table("setores") as batch:
        batch.drop_constraint(FK, type_="foreignkey")
        batch.drop_column("municipio_territorio_id")
