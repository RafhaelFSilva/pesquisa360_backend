"""cria gestao de liderancas

Revision ID: 78d895f396e9
Revises: 8c2097e0a3de
Create Date: 2026-08-20 14:06:37.235218

Lideranca politica (pessoa da campanha), sua configuracao por onda
(setor + cota de votos validos) e os bairros da Base Eleitoral onde atua.
Nao toca em nenhuma tabela existente.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import geoalchemy2


# revision identifiers, used by Alembic.
revision: str = '78d895f396e9'
down_revision: Union[str, Sequence[str], None] = '8c2097e0a3de'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- 1. liderancas_politicas ---------------------------------------------
    # Raiz no Projeto: a lideranca sobrevive as ondas. O tenant vem de
    # projetos.company_id, por isso nao ha company_id aqui.
    op.create_table(
        "liderancas_politicas",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("projeto_id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(), nullable=False),
        sa.Column(
            "localizacao",
            geoalchemy2.types.Geometry(
                geometry_type="POINT",
                srid=4326,
                from_text="ST_GeomFromEWKT",
                name="geometry",
                nullable=True,
            ),
            nullable=True,
        ),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["projeto_id"], ["projetos.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_liderancas_politicas_projeto", "liderancas_politicas", ["projeto_id"])
    op.create_index("ix_liderancas_politicas_ativo", "liderancas_politicas", ["ativo"])

    # --- 2. lideranca_pesquisa_config ----------------------------------------
    # Setor e cota mudam entre ondas; guardar na lideranca perderia historico.
    op.create_table(
        "lideranca_pesquisa_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lideranca_id", sa.Integer(), nullable=False),
        sa.Column("pesquisa_id", sa.Integer(), nullable=False),
        sa.Column("setor_id", sa.Integer(), nullable=True),
        sa.Column("cota_votos_validos", sa.Integer(), nullable=True),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["lideranca_id"], ["liderancas_politicas.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["pesquisa_id"], ["pesquisas.id"]),
        # Setor tem delete fisico e cascade da Pesquisa: SET NULL preserva a
        # configuracao historica da lideranca.
        sa.ForeignKeyConstraint(["setor_id"], ["setores.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lideranca_id", "pesquisa_id", name="uq_lideranca_pesquisa"),
        # NULL = cota nao configurada; 0 = cota definida como zero.
        sa.CheckConstraint(
            "cota_votos_validos IS NULL OR cota_votos_validos >= 0",
            name="ck_lideranca_config_cota",
        ),
    )
    op.create_index("ix_lideranca_config_pesquisa", "lideranca_pesquisa_config", ["pesquisa_id"])
    op.create_index("ix_lideranca_config_setor", "lideranca_pesquisa_config", ["setor_id"])

    # --- 3. lideranca_territorio_eleitoral -----------------------------------
    # Ancora territorial estavel: bairros da Base Eleitoral, N:N.
    op.create_table(
        "lideranca_territorio_eleitoral",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lideranca_id", sa.Integer(), nullable=False),
        sa.Column("territorio_eleitoral_id", sa.Integer(), nullable=False),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["lideranca_id"], ["liderancas_politicas.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["territorio_eleitoral_id"], ["territorio_eleitoral.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "lideranca_id", "territorio_eleitoral_id", name="uq_lideranca_territorio"
        ),
    )
    op.create_index(
        "ix_lideranca_territorio_territorio",
        "lideranca_territorio_eleitoral",
        ["territorio_eleitoral_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_lideranca_territorio_territorio", table_name="lideranca_territorio_eleitoral"
    )
    op.drop_table("lideranca_territorio_eleitoral")

    op.drop_index("ix_lideranca_config_setor", table_name="lideranca_pesquisa_config")
    op.drop_index("ix_lideranca_config_pesquisa", table_name="lideranca_pesquisa_config")
    op.drop_table("lideranca_pesquisa_config")

    op.drop_index("ix_liderancas_politicas_ativo", table_name="liderancas_politicas")
    op.drop_index("ix_liderancas_politicas_projeto", table_name="liderancas_politicas")
    op.drop_table("liderancas_politicas")
