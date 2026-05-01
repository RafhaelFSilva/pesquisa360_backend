"""
corrige_coluna_pulo

Revision ID: 82e6e6df3ce4
Revises: 1636e06de311
Create Date: 2025-12-02 23:32:02.549704
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '82e6e6df3ce4'
down_revision: Union[str, Sequence[str], None] = '1636e06de311'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Corrige lógica de pulo de perguntas e ajustes de constraints.
    NÃO mexe em tabelas de extensões (PostGIS / Tiger).
    """

    # ---- opcoes -> perguntas (FKs) ----
    op.drop_constraint(
        'opcoes_proxima_pergunta_id_fkey',
        'opcoes',
        type_='foreignkey'
    )
    op.drop_constraint(
        'opcoes_pergunta_id_fkey',
        'opcoes',
        type_='foreignkey'
    )

    op.create_foreign_key(
        None,
        'opcoes',
        'perguntas',
        ['proxima_pergunta_id'],
        ['id'],
        ondelete='SET NULL'
    )

    op.create_foreign_key(
        None,
        'opcoes',
        'perguntas',
        ['pergunta_id'],
        ['id'],
        ondelete='CASCADE'
    )

    # ---- perguntas ----
    op.alter_column(
        'perguntas',
        'eh_obrigatoria',
        existing_type=sa.Boolean(),
        nullable=False
    )

    # Remove coluna antiga (JSON de opções)
    op.drop_column('perguntas', 'opcoes')

    # ---- projetos ----
