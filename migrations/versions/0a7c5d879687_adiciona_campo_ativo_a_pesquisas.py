"""Adiciona campo ativo a pesquisas

Revision ID: 0a7c5d879687
Revises: a5a73a29317e
Create Date: 2025-09-02 00:07:01.404501

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0a7c5d879687'      # COLOQUE O ID DESTE ARQUIVO AQUI
down_revision: Union[str, None] = 'a5a73a29317e' # COLOQUE O ID DA MIGRAÇÃO ANTERIOR
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column('pesquisas', sa.Column('ativo', sa.Boolean(), nullable=False, server_default=sa.text('true')))

def downgrade() -> None:
    op.drop_column('pesquisas', 'ativo')
