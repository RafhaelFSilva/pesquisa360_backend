"""adiciona datas ao projeto

Revision ID: e3304b309d48
Revises: 31c96b68a9fb
Create Date: 2025-09-27 13:49:13.843140

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e3304b309d48'
down_revision: Union[str, None] = '31c96b68a9fb' # O ID da migração 31c96b68a9fb
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Adiciona as colunas à tabela 'projetos'
    op.add_column('projetos', sa.Column('data_inicio', sa.Date(), nullable=False, server_default=sa.text('CURRENT_DATE')))
    op.add_column('projetos', sa.Column('data_fim', sa.Date(), nullable=True))


def downgrade() -> None:
    # Remove as colunas na ordem inversa
    op.drop_column('projetos', 'data_fim')
    op.drop_column('projetos', 'data_inicio')