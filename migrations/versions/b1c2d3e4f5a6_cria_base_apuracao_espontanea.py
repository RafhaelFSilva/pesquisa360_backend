"""cria base de apuracao espontanea

Revision ID: b1c2d3e4f5a6
Revises: e7f9a2b3c4d5
Create Date: 2026-08-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "e7f9a2b3c4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "perguntas",
        sa.Column(
            "eh_resposta_espontanea",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_perguntas_eh_resposta_espontanea",
        "perguntas",
        ["eh_resposta_espontanea"],
        unique=False,
    )

    op.create_table(
        "categorias_resposta_espontanea",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pesquisa_id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(), nullable=False),
        sa.Column("nome_normalizado", sa.String(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("criado_por_id", sa.Integer(), nullable=False),
        sa.Column("atualizado_por_id", sa.Integer(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["pesquisa_id"], ["pesquisas.id"]),
        sa.ForeignKeyConstraint(["criado_por_id"], ["usuarios.id"]),
        sa.ForeignKeyConstraint(["atualizado_por_id"], ["usuarios.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_categorias_resposta_espontanea_pesquisa_id",
        "categorias_resposta_espontanea",
        ["pesquisa_id"],
        unique=False,
    )
    op.create_index(
        "ix_categorias_resposta_espontanea_ativo",
        "categorias_resposta_espontanea",
        ["ativo"],
        unique=False,
    )
    op.create_index(
        "uq_categoria_resposta_espontanea_pesquisa_nome_ativo",
        "categorias_resposta_espontanea",
        ["pesquisa_id", "nome_normalizado"],
        unique=True,
        postgresql_where=sa.text("ativo IS TRUE"),
        sqlite_where=sa.text("ativo = 1"),
    )

    op.create_table(
        "mapeamentos_resposta_espontanea",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pesquisa_id", sa.Integer(), nullable=False),
        sa.Column("categoria_id", sa.Integer(), nullable=False),
        sa.Column("chave_normalizada", sa.String(), nullable=False),
        sa.Column("texto_referencia", sa.Text(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("criado_por_id", sa.Integer(), nullable=False),
        sa.Column("atualizado_por_id", sa.Integer(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["pesquisa_id"], ["pesquisas.id"]),
        sa.ForeignKeyConstraint(["categoria_id"], ["categorias_resposta_espontanea.id"]),
        sa.ForeignKeyConstraint(["criado_por_id"], ["usuarios.id"]),
        sa.ForeignKeyConstraint(["atualizado_por_id"], ["usuarios.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_mapeamentos_resposta_espontanea_pesquisa_id",
        "mapeamentos_resposta_espontanea",
        ["pesquisa_id"],
        unique=False,
    )
    op.create_index(
        "ix_mapeamentos_resposta_espontanea_categoria_id",
        "mapeamentos_resposta_espontanea",
        ["categoria_id"],
        unique=False,
    )
    op.create_index(
        "ix_mapeamentos_resposta_espontanea_ativo",
        "mapeamentos_resposta_espontanea",
        ["ativo"],
        unique=False,
    )
    op.create_index(
        "uq_mapeamento_resposta_espontanea_pesquisa_chave_ativo",
        "mapeamentos_resposta_espontanea",
        ["pesquisa_id", "chave_normalizada"],
        unique=True,
        postgresql_where=sa.text("ativo IS TRUE"),
        sqlite_where=sa.text("ativo = 1"),
    )


def downgrade() -> None:
    op.drop_index("uq_mapeamento_resposta_espontanea_pesquisa_chave_ativo", table_name="mapeamentos_resposta_espontanea")
    op.drop_index("ix_mapeamentos_resposta_espontanea_ativo", table_name="mapeamentos_resposta_espontanea")
    op.drop_index("ix_mapeamentos_resposta_espontanea_categoria_id", table_name="mapeamentos_resposta_espontanea")
    op.drop_index("ix_mapeamentos_resposta_espontanea_pesquisa_id", table_name="mapeamentos_resposta_espontanea")
    op.drop_table("mapeamentos_resposta_espontanea")

    op.drop_index("uq_categoria_resposta_espontanea_pesquisa_nome_ativo", table_name="categorias_resposta_espontanea")
    op.drop_index("ix_categorias_resposta_espontanea_ativo", table_name="categorias_resposta_espontanea")
    op.drop_index("ix_categorias_resposta_espontanea_pesquisa_id", table_name="categorias_resposta_espontanea")
    op.drop_table("categorias_resposta_espontanea")

    op.drop_index("ix_perguntas_eh_resposta_espontanea", table_name="perguntas")
    op.drop_column("perguntas", "eh_resposta_espontanea")
