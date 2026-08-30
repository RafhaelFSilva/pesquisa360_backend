"""tokens de ativacao de conta

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-08-27 00:00:00.000000

Aditiva. Nenhuma coluna de `usuarios` muda: o estado de ativacao continua sendo
o `ativo` que ja existia, e os usuarios atuais seguem intactos (nenhum backfill
os desativa).
"""
from alembic import op
import sqlalchemy as sa


revision = "d1e2f3a4b5c6"
down_revision = "c0d1e2f3a4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_activation_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=False),
        # SHA-256 hex: 64 caracteres. Nunca o token em texto puro.
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("usado_em", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_user_activation_token_hash"),
    )
    op.create_index("ix_user_activation_tokens_id", "user_activation_tokens", ["id"])
    op.create_index("ix_user_activation_tokens_usuario_id", "user_activation_tokens", ["usuario_id"])
    op.create_index("ix_user_activation_tokens_token_hash", "user_activation_tokens", ["token_hash"])


def downgrade() -> None:
    op.drop_index("ix_user_activation_tokens_token_hash", table_name="user_activation_tokens")
    op.drop_index("ix_user_activation_tokens_usuario_id", table_name="user_activation_tokens")
    op.drop_index("ix_user_activation_tokens_id", table_name="user_activation_tokens")
    op.drop_table("user_activation_tokens")
