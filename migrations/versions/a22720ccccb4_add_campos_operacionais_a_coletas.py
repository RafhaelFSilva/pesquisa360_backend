
"""add campos operacionais a coletas

Revision ID: a22720ccccb4
Revises: 3e4de16d893c
Create Date: 2025-12-13 03:14:13.857824
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a22720ccccb4'
down_revision = '3e4de16d893c'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'coletas',
        sa.Column(
            'foi_offline',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false()
        )
    )

    op.add_column(
        'coletas',
        sa.Column(
            'endereco_estimado',
            sa.Text(),
            nullable=True
        )
    )

    op.add_column(
        'coletas',
        sa.Column(
            'status_sincronizacao',
            sa.String(length=50),
            nullable=False,
            server_default='pendente'
        )
    )

    # remove defaults após criação
    with op.batch_alter_table('coletas') as batch_op:
        batch_op.alter_column('foi_offline', server_default=None)
        batch_op.alter_column('status_sincronizacao', server_default=None)


def downgrade():
    op.drop_column('coletas', 'status_sincronizacao')
    op.drop_column('coletas', 'endereco_estimado')
    op.drop_column('coletas', 'foi_offline')
