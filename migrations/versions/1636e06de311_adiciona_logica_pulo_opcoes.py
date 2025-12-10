"""adiciona_logica_pulo_opcoes

Revision ID: 1636e06de311
Revises: ff01e1adcb98
Create Date: 2025-12-02 16:40:49.640640

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1636e06de311'
down_revision: Union[str, Sequence[str], None] = 'ff01e1adcb98'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- CRIA A TABELA OPCOES (Caso ela não exista) ---
    op.create_table('opcoes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('texto', sa.String(), nullable=False),
        sa.Column('ordem', sa.Integer(), nullable=True),
        sa.Column('pergunta_id', sa.Integer(), nullable=True),
        # Aqui está o campo novo para a Lógica de Pulo
        sa.Column('proxima_pergunta_id', sa.Integer(), nullable=True), 
        
        # Chaves Estrangeiras (Foreign Keys)
        sa.ForeignKeyConstraint(['pergunta_id'], ['perguntas.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['proxima_pergunta_id'], ['perguntas.id'], ondelete='SET NULL'),
        
        sa.PrimaryKeyConstraint('id')
    )

def downgrade() -> None:
    op.drop_table('opcoes')
