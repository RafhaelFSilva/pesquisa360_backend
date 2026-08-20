"""cria base eleitoral versionada

Revision ID: 8c2097e0a3de
Revises: c4d5e6f7a8b9
Create Date: 2026-08-20 02:19:17.574636

Fundacao estrutural da Base Eleitoral versionada. Nao toca em tabelas legadas
(bairros, locais_votacao, setores, coletas) e nao carrega nenhum dado.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import geoalchemy2


# revision identifiers, used by Alembic.
revision: str = '8c2097e0a3de'
down_revision: Union[str, Sequence[str], None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


STATUS_BASE_ELEITORAL = ("IMPORTADA", "EM_CONFERENCIA", "VALIDADA", "SUBSTITUIDA")
TIPOS_TERRITORIO_ELEITORAL = (
    "ESTADO",
    "MUNICIPIO",
    "BAIRRO",
    "LOCALIDADE",
    "LOCAL_VOTACAO",
    "SECAO",
)

# CHECK PostGIS: LOCAL_VOTACAO/SECAO usam ponto, os demais niveis usam poligono.
# Depende de GeometryType(), que nao existe em SQLite, entao so e aplicado no
# PostgreSQL. O mesmo predicado esta no model com `.ddl_if(dialect="postgresql")`.
CHECK_GEOMETRIA_TIPO = (
    "geometria IS NULL"
    " OR (tipo IN ('LOCAL_VOTACAO', 'SECAO') AND GeometryType(geometria) = 'POINT')"
    " OR (tipo IN ('ESTADO', 'MUNICIPIO', 'BAIRRO', 'LOCALIDADE')"
    " AND GeometryType(geometria) IN ('POLYGON', 'MULTIPOLYGON'))"
)


def _json_type():
    """JSONB no PostgreSQL e JSON no SQLite, seguindo metadados_analiticos."""
    return postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def _sql_in(coluna: str, valores) -> str:
    return "{} IN ({})".format(coluna, ", ".join("'{}'".format(item) for item in valores))


def upgrade() -> None:
    # --- 1. base_eleitoral ---------------------------------------------------
    op.create_table(
        "base_eleitoral",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(), nullable=False),
        sa.Column("ano", sa.Integer(), nullable=False),
        sa.Column("uf", sa.String(length=2), nullable=False),
        sa.Column("fonte", sa.String(), nullable=False),
        sa.Column("fonte_referencia", sa.String(), nullable=True),
        sa.Column("versao", sa.String(), nullable=False),
        sa.Column("data_referencia", sa.Date(), nullable=False),
        sa.Column(
            "status", sa.String(), nullable=False, server_default=sa.text("'IMPORTADA'")
        ),
        sa.Column("substituida_por_id", sa.Integer(), nullable=True),
        # NULL = base oficial/global; preenchido = base privada do tenant.
        sa.Column("company_id", sa.Integer(), nullable=True),
        sa.Column("comparecimento_estimado", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("percentual_votos_validos", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("criado_por_id", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(["substituida_por_id"], ["base_eleitoral.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["criado_por_id"], ["usuarios.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            _sql_in("status", STATUS_BASE_ELEITORAL), name="ck_base_eleitoral_status"
        ),
        sa.CheckConstraint(
            "comparecimento_estimado IS NULL"
            " OR (comparecimento_estimado >= 0 AND comparecimento_estimado <= 1)",
            name="ck_base_eleitoral_comparecimento",
        ),
        sa.CheckConstraint(
            "percentual_votos_validos IS NULL"
            " OR (percentual_votos_validos >= 0 AND percentual_votos_validos <= 1)",
            name="ck_base_eleitoral_votos_validos",
        ),
    )
    # Dois indices parciais em vez de UNIQUE(uf, ano, versao, company_id): NULL nao
    # colide em UNIQUE, entao duas bases oficiais identicas passariam despercebidas.
    op.create_index(
        "uq_base_eleitoral_oficial",
        "base_eleitoral",
        ["uf", "ano", "versao"],
        unique=True,
        postgresql_where=sa.text("company_id IS NULL"),
        sqlite_where=sa.text("company_id IS NULL"),
    )
    op.create_index(
        "uq_base_eleitoral_privada",
        "base_eleitoral",
        ["uf", "ano", "versao", "company_id"],
        unique=True,
        postgresql_where=sa.text("company_id IS NOT NULL"),
        sqlite_where=sa.text("company_id IS NOT NULL"),
    )
    op.create_index("ix_base_eleitoral_uf_ano", "base_eleitoral", ["uf", "ano"], unique=False)
    op.create_index(
        "ix_base_eleitoral_company_id", "base_eleitoral", ["company_id"], unique=False
    )
    op.create_index("ix_base_eleitoral_status", "base_eleitoral", ["status"], unique=False)

    # --- 2. territorio_eleitoral ---------------------------------------------
    # O indice espacial e criado automaticamente pelo GeoAlchemy2 (mesmo padrao de
    # bairros/locais_votacao); nao declarar GIST manualmente para nao duplicar.
    op.create_table(
        "territorio_eleitoral",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("base_eleitoral_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("tipo", sa.String(), nullable=False),
        sa.Column("codigo", sa.String(), nullable=True),
        sa.Column("nome", sa.String(), nullable=False),
        sa.Column("nome_normalizado", sa.String(), nullable=False),
        sa.Column("municipio_id", sa.Integer(), nullable=True),
        sa.Column("zona_eleitoral", sa.Integer(), nullable=True),
        sa.Column("numero_secao", sa.Integer(), nullable=True),
        sa.Column("eleitorado_apto", sa.Integer(), nullable=True),
        sa.Column("eleitorado_apto_origem", sa.Integer(), nullable=True),
        sa.Column(
            "eleitorado_apto_divergente",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "status_validacao",
            sa.String(),
            nullable=False,
            server_default=sa.text("'IMPORTADA'"),
        ),
        sa.Column(
            "geometria",
            geoalchemy2.types.Geometry(
                geometry_type="GEOMETRY",
                srid=4326,
                from_text="ST_GeomFromEWKT",
                name="geometry",
                nullable=True,
            ),
            nullable=True,
        ),
        sa.Column("metadados", _json_type(), nullable=False, server_default=sa.text("'{}'")),
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
        sa.PrimaryKeyConstraint("id"),
        # Alvo das FKs compostas abaixo.
        sa.UniqueConstraint("id", "base_eleitoral_id", name="uq_territorio_eleitoral_id_base"),
        sa.ForeignKeyConstraint(
            ["base_eleitoral_id"], ["base_eleitoral.id"], ondelete="CASCADE"
        ),
        # Impede no banco que um no aponte para pai/municipio de outra versao de base.
        # parent_id/municipio_id NULL desativam a checagem (MATCH SIMPLE), o que
        # preserva raizes e territorios sem municipio.
        sa.ForeignKeyConstraint(
            ["parent_id", "base_eleitoral_id"],
            ["territorio_eleitoral.id", "territorio_eleitoral.base_eleitoral_id"],
            name="fk_territorio_parent_mesma_base",
        ),
        sa.ForeignKeyConstraint(
            ["municipio_id", "base_eleitoral_id"],
            ["territorio_eleitoral.id", "territorio_eleitoral.base_eleitoral_id"],
            name="fk_territorio_municipio_mesma_base",
        ),
        sa.CheckConstraint(
            _sql_in("tipo", TIPOS_TERRITORIO_ELEITORAL), name="ck_territorio_tipo"
        ),
        sa.CheckConstraint(
            _sql_in("status_validacao", STATUS_BASE_ELEITORAL),
            name="ck_territorio_status_validacao",
        ),
        sa.CheckConstraint(
            "parent_id IS NOT NULL OR tipo = 'ESTADO'", name="ck_territorio_raiz"
        ),
        sa.CheckConstraint(
            "tipo <> 'SECAO' OR numero_secao IS NOT NULL", name="ck_territorio_secao"
        ),
        sa.CheckConstraint(
            "eleitorado_apto IS NULL OR eleitorado_apto >= 0",
            name="ck_territorio_eleitorado_apto",
        ),
        sa.CheckConstraint(
            "eleitorado_apto_origem IS NULL OR eleitorado_apto_origem >= 0",
            name="ck_territorio_eleitorado_origem",
        ),
    )
    op.create_index(
        "ix_territorio_base_tipo", "territorio_eleitoral", ["base_eleitoral_id", "tipo"]
    )
    op.create_index("ix_territorio_parent", "territorio_eleitoral", ["parent_id"])
    op.create_index("ix_territorio_municipio", "territorio_eleitoral", ["municipio_id"])
    op.create_index(
        "ix_territorio_nome_norm",
        "territorio_eleitoral",
        ["base_eleitoral_id", "nome_normalizado"],
    )
    op.create_index("ix_territorio_zona", "territorio_eleitoral", ["zona_eleitoral"])
    # Codigo e opcional: a unicidade so vale quando ele existe.
    op.create_index(
        "uq_territorio_base_tipo_codigo",
        "territorio_eleitoral",
        ["base_eleitoral_id", "tipo", "codigo"],
        unique=True,
        postgresql_where=sa.text("codigo IS NOT NULL"),
        sqlite_where=sa.text("codigo IS NOT NULL"),
    )

    # --- 3. projeto_base_eleitoral -------------------------------------------
    op.create_table(
        "projeto_base_eleitoral",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("projeto_id", sa.Integer(), nullable=False),
        sa.Column("base_eleitoral_id", sa.Integer(), nullable=False),
        sa.Column("principal", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "vinculado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["projeto_id"], ["projetos.id"]),
        sa.ForeignKeyConstraint(["base_eleitoral_id"], ["base_eleitoral.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("projeto_id", "base_eleitoral_id", name="uq_projeto_base"),
    )
    op.create_index("ix_projeto_base_projeto", "projeto_base_eleitoral", ["projeto_id"])
    # Historico de vinculos e permitido; apenas uma base principal por projeto.
    op.create_index(
        "uq_projeto_base_principal",
        "projeto_base_eleitoral",
        ["projeto_id"],
        unique=True,
        postgresql_where=sa.text("principal IS TRUE"),
        sqlite_where=sa.text("principal = 1"),
    )

    # --- 4. importacao_base_eleitoral ----------------------------------------
    op.create_table(
        "importacao_base_eleitoral",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("base_eleitoral_id", sa.Integer(), nullable=False),
        sa.Column("arquivo_origem", sa.String(), nullable=False),
        sa.Column("hash_arquivo", sa.String(), nullable=True),
        sa.Column("total_linhas", sa.Integer(), nullable=False),
        sa.Column("total_importadas", sa.Integer(), nullable=False),
        sa.Column("total_divergencias", sa.Integer(), nullable=False),
        sa.Column("divergencias", _json_type(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("executado_por_id", sa.Integer(), nullable=False),
        sa.Column(
            "executado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["base_eleitoral_id"], ["base_eleitoral.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["executado_por_id"], ["usuarios.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("total_linhas >= 0", name="ck_importacao_total_linhas"),
        sa.CheckConstraint("total_importadas >= 0", name="ck_importacao_total_importadas"),
        sa.CheckConstraint("total_divergencias >= 0", name="ck_importacao_total_divergencias"),
    )

    # --- 5. CHECK geometrico: somente PostgreSQL -----------------------------
    if op.get_bind().dialect.name == "postgresql":
        op.create_check_constraint(
            "ck_territorio_geometria_tipo", "territorio_eleitoral", CHECK_GEOMETRIA_TIPO
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(
            "ck_territorio_geometria_tipo", "territorio_eleitoral", type_="check"
        )

    op.drop_table("importacao_base_eleitoral")

    op.drop_index("uq_projeto_base_principal", table_name="projeto_base_eleitoral")
    op.drop_index("ix_projeto_base_projeto", table_name="projeto_base_eleitoral")
    op.drop_table("projeto_base_eleitoral")

    op.drop_index("uq_territorio_base_tipo_codigo", table_name="territorio_eleitoral")
    op.drop_index("ix_territorio_zona", table_name="territorio_eleitoral")
    op.drop_index("ix_territorio_nome_norm", table_name="territorio_eleitoral")
    op.drop_index("ix_territorio_municipio", table_name="territorio_eleitoral")
    op.drop_index("ix_territorio_parent", table_name="territorio_eleitoral")
    op.drop_index("ix_territorio_base_tipo", table_name="territorio_eleitoral")
    op.drop_table("territorio_eleitoral")

    op.drop_index("ix_base_eleitoral_status", table_name="base_eleitoral")
    op.drop_index("ix_base_eleitoral_company_id", table_name="base_eleitoral")
    op.drop_index("ix_base_eleitoral_uf_ano", table_name="base_eleitoral")
    op.drop_index("uq_base_eleitoral_privada", table_name="base_eleitoral")
    op.drop_index("uq_base_eleitoral_oficial", table_name="base_eleitoral")
    op.drop_table("base_eleitoral")
