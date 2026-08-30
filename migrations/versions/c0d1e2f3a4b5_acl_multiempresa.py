"""acl multiempresa e multiprojeto por usuario

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-08-27 00:00:00.000000

ADR-024. Aditiva: nenhuma coluna existente muda de tipo, nome ou nulidade e
`usuarios.company_id` permanece (agora como empresa PRINCIPAL/default).

O backfill e a parte que garante compatibilidade: todo usuario com
`company_id` ganha um vinculo `acesso_todos_projetos=True, principal=True` na
propria empresa, que e exatamente o alcance que ele tinha antes. Por isso NAO
se cria uma linha por projeto: o comportamento legado era "todos os projetos da
minha empresa", e representar isso projeto a projeto congelaria o passado e
excluiria projetos futuros da empresa.

`usuario_projeto_acessos` nasce vazia de proposito: acesso restrito e uma
decisao administrativa posterior, nao um estado herdado.
"""
from alembic import op
import sqlalchemy as sa


revision = "c0d1e2f3a4b5"
down_revision = "b9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usuario_empresa_acessos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column(
            "acesso_todos_projetos", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("principal", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("usuario_id", "company_id", name="uq_usuario_empresa_acesso"),
    )
    op.create_index("ix_usuario_empresa_acessos_id", "usuario_empresa_acessos", ["id"])
    op.create_index(
        "ix_usuario_empresa_acessos_usuario_id", "usuario_empresa_acessos", ["usuario_id"]
    )
    op.create_index(
        "ix_usuario_empresa_acessos_company_id", "usuario_empresa_acessos", ["company_id"]
    )

    op.create_table(
        "usuario_projeto_acessos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=False),
        sa.Column("projeto_id", sa.Integer(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"]),
        sa.ForeignKeyConstraint(["projeto_id"], ["projetos.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("usuario_id", "projeto_id", name="uq_usuario_projeto_acesso"),
    )
    op.create_index("ix_usuario_projeto_acessos_id", "usuario_projeto_acessos", ["id"])
    op.create_index(
        "ix_usuario_projeto_acessos_usuario_id", "usuario_projeto_acessos", ["usuario_id"]
    )
    op.create_index(
        "ix_usuario_projeto_acessos_projeto_id", "usuario_projeto_acessos", ["projeto_id"]
    )

    # Backfill idempotente: NOT EXISTS evita duplicar em re-execucao e respeita
    # a unique. Usuario sem company_id nao ganha tenant inventado.
    op.execute(
        """
        INSERT INTO usuario_empresa_acessos
            (usuario_id, company_id, acesso_todos_projetos, ativo, principal)
        SELECT u.id, u.company_id, TRUE, TRUE, TRUE
          FROM usuarios u
         WHERE u.company_id IS NOT NULL
           AND NOT EXISTS (
                 SELECT 1 FROM usuario_empresa_acessos a
                  WHERE a.usuario_id = u.id AND a.company_id = u.company_id
               )
        """
    )


def downgrade() -> None:
    # So remove o que esta revisao criou. `usuarios.company_id` nunca foi
    # tocado, entao o comportamento legado volta intacto.
    op.drop_index("ix_usuario_projeto_acessos_projeto_id", table_name="usuario_projeto_acessos")
    op.drop_index("ix_usuario_projeto_acessos_usuario_id", table_name="usuario_projeto_acessos")
    op.drop_index("ix_usuario_projeto_acessos_id", table_name="usuario_projeto_acessos")
    op.drop_table("usuario_projeto_acessos")

    op.drop_index("ix_usuario_empresa_acessos_company_id", table_name="usuario_empresa_acessos")
    op.drop_index("ix_usuario_empresa_acessos_usuario_id", table_name="usuario_empresa_acessos")
    op.drop_index("ix_usuario_empresa_acessos_id", table_name="usuario_empresa_acessos")
    op.drop_table("usuario_empresa_acessos")
