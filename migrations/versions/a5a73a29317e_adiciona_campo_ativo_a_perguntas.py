"""Adiciona campo ativo a perguntas

Revision ID: a5a73a29317e
Revises: 7aa2d03b52b5
Create Date: 2025-08-31 12:09:41.359652

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a5a73a29317e' # COLOQUE O ID DESTE ARQUIVO AQUI
down_revision: Union[str, None] = '7aa2d03b52b5' # COLOQUE O ID DA MIGRAÇÃO ANTERIOR (COLETAS)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column('perguntas', sa.Column('ativo', sa.Boolean(), nullable=False, server_default=sa.text('true')))

def downgrade() -> None:
    op.drop_column('perguntas', 'ativo')
