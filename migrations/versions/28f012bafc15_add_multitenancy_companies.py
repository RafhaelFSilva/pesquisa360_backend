"""add_multitenancy_companies

Revision ID: 28f012bafc15
Revises: a22720ccccb4
Create Date: 2025-12-20 19:14:41.383638

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '28f012bafc15'
down_revision: Union[str, Sequence[str], None] = 'a22720ccccb4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Cria a tabela companies
    op.create_table('companies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('cnpj', sa.String(), nullable=True),
        sa.Column('logo_url', sa.String(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    # 2. INSERÇÃO DE DADOS (Migration de Dados)
    # Cria uma empresa padrão para abrigar os usuários existentes
    op.execute("INSERT INTO companies (name, is_active) VALUES ('Empresa Padrão (Legado)', true)")

    # 3. Adiciona as colunas como NULLABLE primeiro (para não dar erro)
    op.add_column('usuarios', sa.Column('company_id', sa.Integer(), nullable=True))
    op.add_column('projetos', sa.Column('company_id', sa.Integer(), nullable=True))

    # 4. Atualiza os registros existentes para apontar para a Empresa ID 1
    op.execute("UPDATE usuarios SET company_id = (SELECT id FROM companies LIMIT 1)")
    op.execute("UPDATE projetos SET company_id = (SELECT id FROM companies LIMIT 1)")

    # 5. Agora que todos têm dados, alteramos para NOT NULL
    with op.batch_alter_table('usuarios') as batch_op:
        batch_op.alter_column('company_id', existing_type=sa.Integer(), nullable=False)
        batch_op.create_foreign_key(
            'fk_usuarios_company', 'companies', ['company_id'], ['id']
        )
    with op.batch_alter_table('projetos') as batch_op:
        batch_op.alter_column('company_id', existing_type=sa.Integer(), nullable=False)
        batch_op.create_foreign_key(
            'fk_projetos_company', 'companies', ['company_id'], ['id']
        )

def downgrade() -> None:
    """Remove somente as estruturas criadas por esta migration."""
    op.drop_constraint('fk_usuarios_company', 'usuarios', type_='foreignkey')
    op.drop_column('usuarios', 'company_id')
    op.drop_constraint('fk_projetos_company', 'projetos', type_='foreignkey')
    op.drop_column('projetos', 'company_id')
    op.drop_table('companies')
