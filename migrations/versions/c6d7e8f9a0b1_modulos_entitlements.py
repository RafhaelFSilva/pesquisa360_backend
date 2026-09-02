"""catalogo modular e entitlements comerciais

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-09-01 00:00:00.000000

Fundacao aditiva: nao altera rotas existentes e nao concede licenca a tenant.
Potencial de Crescimento entra no catalogo como inativo/planejado, pois seu
motor ainda nao foi implementado.
"""
from alembic import op
import sqlalchemy as sa


revision = "c6d7e8f9a0b1"
down_revision = "b5c6d7e8f9a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "modulos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chave", sa.String(length=100), nullable=False),
        sa.Column("nome", sa.String(length=200), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("ativo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chave", name="uq_modulos_chave"),
    )
    op.create_table(
        "modulo_funcionalidades",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("modulo_id", sa.Integer(), nullable=False),
        sa.Column("chave", sa.String(length=100), nullable=False),
        sa.Column("nome", sa.String(length=200), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("ativo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["modulo_id"], ["modulos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("modulo_id", "chave", name="uq_modulo_funcionalidade_chave"),
    )
    op.create_table(
        "modulo_entitlements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("modulo_id", sa.Integer(), nullable=False),
        sa.Column("projeto_id", sa.Integer(), nullable=True),
        sa.Column("pesquisa_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'ATIVO'"), nullable=False),
        sa.Column("inicia_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("criado_por_usuario_id", sa.Integer(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "NOT (projeto_id IS NOT NULL AND pesquisa_id IS NOT NULL)",
            name="ck_modulo_entitlement_um_escopo",
        ),
        sa.CheckConstraint(
            "status IN ('ATIVO', 'SUSPENSO', 'CANCELADO')",
            name="ck_modulo_entitlement_status",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["modulo_id"], ["modulos.id"]),
        sa.ForeignKeyConstraint(["projeto_id"], ["projetos.id"]),
        sa.ForeignKeyConstraint(["pesquisa_id"], ["pesquisas.id"]),
        sa.ForeignKeyConstraint(["criado_por_usuario_id"], ["usuarios.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "modulo_entitlement_funcionalidades",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entitlement_id", sa.Integer(), nullable=False),
        sa.Column("funcionalidade_id", sa.Integer(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["entitlement_id"], ["modulo_entitlements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["funcionalidade_id"], ["modulo_funcionalidades.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entitlement_id", "funcionalidade_id", name="uq_entitlement_funcionalidade"),
    )

    for table_name, columns in (
        ("modulos", ["id"]),
        ("modulo_funcionalidades", ["id"]),
        ("modulo_funcionalidades", ["modulo_id"]),
        ("modulo_entitlements", ["id"]),
        ("modulo_entitlements", ["company_id"]),
        ("modulo_entitlements", ["modulo_id"]),
        ("modulo_entitlements", ["projeto_id"]),
        ("modulo_entitlements", ["pesquisa_id"]),
        ("modulo_entitlement_funcionalidades", ["id"]),
        ("modulo_entitlement_funcionalidades", ["entitlement_id"]),
        ("modulo_entitlement_funcionalidades", ["funcionalidade_id"]),
    ):
        op.create_index(f"ix_{table_name}_{columns[0]}", table_name, columns)

    empresa = sa.text("projeto_id IS NULL AND pesquisa_id IS NULL")
    projeto = sa.text("projeto_id IS NOT NULL AND pesquisa_id IS NULL")
    pesquisa = sa.text("projeto_id IS NULL AND pesquisa_id IS NOT NULL")
    op.create_index(
        "uq_modulo_entitlement_empresa", "modulo_entitlements", ["company_id", "modulo_id"],
        unique=True, postgresql_where=empresa, sqlite_where=empresa,
    )
    op.create_index(
        "uq_modulo_entitlement_projeto", "modulo_entitlements", ["company_id", "modulo_id", "projeto_id"],
        unique=True, postgresql_where=projeto, sqlite_where=projeto,
    )
    op.create_index(
        "uq_modulo_entitlement_pesquisa", "modulo_entitlements", ["company_id", "modulo_id", "pesquisa_id"],
        unique=True, postgresql_where=pesquisa, sqlite_where=pesquisa,
    )

    op.execute("""
        INSERT INTO modulos (chave, nome, descricao, ativo)
        SELECT 'inteligencia_eleitoral', 'Inteligência Eleitoral',
               'Capacidades de inteligencia eleitoral do Pesquisa360.', true
        WHERE NOT EXISTS (SELECT 1 FROM modulos WHERE chave = 'inteligencia_eleitoral')
    """)
    op.execute("""
        INSERT INTO modulo_funcionalidades (modulo_id, chave, nome, descricao, ativo)
        SELECT id, 'potencial_crescimento', 'Potencial de Crescimento',
               'Planejada: motor ainda nao implementado.', false
        FROM modulos
        WHERE chave = 'inteligencia_eleitoral'
          AND NOT EXISTS (
              SELECT 1 FROM modulo_funcionalidades f
              WHERE f.modulo_id = modulos.id AND f.chave = 'potencial_crescimento'
          )
    """)


def downgrade() -> None:
    op.drop_table("modulo_entitlement_funcionalidades")
    op.drop_table("modulo_entitlements")
    op.drop_table("modulo_funcionalidades")
    op.drop_table("modulos")
