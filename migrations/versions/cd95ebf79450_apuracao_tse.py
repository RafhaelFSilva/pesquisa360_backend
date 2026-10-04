"""dominio de apuracao TSE: tabelas globais de resultados oficiais

Revision ID: cd95ebf79450
Revises: be8036a45b28
Create Date: 2026-10-04 15:10:00.000000

Aditiva (ADR-076 a ADR-083). Cria somente tabelas `tse_*`; nao toca em
nenhuma tabela existente. Sao dados publicos e globais: nenhuma tem
`company_id` nem FK para tabelas de tenant. `origem` (OFICIAL | SIMULADO)
separa o ambiente de simulacao do TSE do resultado oficial.

`tse_resultados_secao` NAO e criada: depende da decodificacao do BU com a
especificacao ASN.1 oficial, ainda pendente.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "cd95ebf79450"
down_revision: Union[str, Sequence[str], None] = "be8036a45b28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ORIGENS = "origem IN ('OFICIAL', 'SIMULADO')"


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    op.create_table(
        "tse_eleicoes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("origem", sa.String(length=10), nullable=False),
        sa.Column("ambiente", sa.String(length=40), nullable=False),
        sa.Column("ciclo", sa.String(length=20), nullable=False),
        sa.Column("pleito", sa.String(length=10), nullable=False),
        sa.Column("codigo_eleicao", sa.String(length=10), nullable=False),
        sa.Column("nome", sa.String(length=200), nullable=False),
        sa.Column("turno", sa.Integer(), nullable=False),
        sa.Column("tipo", sa.String(length=5), nullable=True),
        sa.Column("data_eleicao", sa.Date(), nullable=True),
        sa.Column("codigo_segundo_turno", sa.String(length=10), nullable=True),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        _ts("criado_em"),
        _ts("atualizado_em"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("origem", "pleito", "codigo_eleicao", name="uq_tse_eleicoes_natural"),
        sa.CheckConstraint(ORIGENS, name="ck_tse_eleicoes_origem"),
    )
    op.create_table(
        "tse_cargos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("codigo", sa.String(length=4), nullable=False),
        sa.Column("nome", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("codigo", name="uq_tse_cargos_codigo"),
    )
    op.create_table(
        "tse_abrangencias",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("eleicao_id", sa.Integer(), nullable=False),
        sa.Column("tipo", sa.String(length=10), nullable=False),
        sa.Column("chave", sa.String(length=40), nullable=False),
        sa.Column("uf", sa.String(length=2), nullable=True),
        sa.Column("municipio_codigo", sa.String(length=5), nullable=True),
        sa.Column("municipio_nome", sa.String(length=120), nullable=True),
        sa.Column("zona", sa.String(length=4), nullable=True),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["eleicao_id"], ["tse_eleicoes.id"]),
        sa.ForeignKeyConstraint(["parent_id"], ["tse_abrangencias.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("eleicao_id", "chave", name="uq_tse_abrangencias_chave"),
        sa.CheckConstraint(
            "tipo IN ('BR', 'UF', 'MUNICIPIO', 'ZONA')", name="ck_tse_abrangencias_tipo"
        ),
    )
    op.create_index("ix_tse_abrangencias_parent", "tse_abrangencias", ["parent_id"])
    op.create_index(
        "ix_tse_abrangencias_territorio", "tse_abrangencias",
        ["eleicao_id", "uf", "municipio_codigo", "zona"],
    )
    op.create_table(
        "tse_federacoes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("eleicao_id", sa.Integer(), nullable=False),
        sa.Column("numero", sa.String(length=10), nullable=False),
        sa.Column("sigla", sa.String(length=60), nullable=False),
        sa.Column("nome", sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(["eleicao_id"], ["tse_eleicoes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("eleicao_id", "numero", name="uq_tse_federacoes_numero"),
    )
    op.create_table(
        "tse_partidos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("eleicao_id", sa.Integer(), nullable=False),
        sa.Column("numero", sa.String(length=5), nullable=False),
        sa.Column("sigla", sa.String(length=40), nullable=False),
        sa.Column("nome", sa.String(length=200), nullable=False),
        sa.Column("federacao_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["eleicao_id"], ["tse_eleicoes.id"]),
        sa.ForeignKeyConstraint(["federacao_id"], ["tse_federacoes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("eleicao_id", "numero", name="uq_tse_partidos_numero"),
    )
    op.create_table(
        "tse_candidatos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("eleicao_id", sa.Integer(), nullable=False),
        sa.Column("cargo_id", sa.Integer(), nullable=False),
        sa.Column("uf", sa.String(length=2), nullable=False),
        sa.Column("sqcand", sa.String(length=20), nullable=False),
        sa.Column("numero", sa.String(length=10), nullable=False),
        sa.Column("nome", sa.String(length=200), nullable=False),
        sa.Column("nome_urna", sa.String(length=200), nullable=False),
        sa.Column("partido_id", sa.Integer(), nullable=False),
        _ts("criado_em"),
        sa.ForeignKeyConstraint(["eleicao_id"], ["tse_eleicoes.id"]),
        sa.ForeignKeyConstraint(["cargo_id"], ["tse_cargos.id"]),
        sa.ForeignKeyConstraint(["partido_id"], ["tse_partidos.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("eleicao_id", "sqcand", name="uq_tse_candidatos_sqcand"),
    )
    op.create_index(
        "ix_tse_candidatos_numero", "tse_candidatos", ["eleicao_id", "cargo_id", "uf", "numero"]
    )
    op.create_index("ix_tse_candidatos_partido", "tse_candidatos", ["partido_id"])
    op.create_table(
        "tse_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("origem", sa.String(length=10), nullable=False),
        sa.Column("tipo", sa.String(length=10), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("etag", sa.String(length=200), nullable=True),
        sa.Column("last_modified", sa.String(length=60), nullable=True),
        sa.Column("idg", sa.String(length=20), nullable=True),
        sa.Column("gerado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("tamanho_bytes", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("arquivo_path", sa.Text(), nullable=True),
        _ts("capturado_em"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url", "sha256", name="uq_tse_snapshots_url_sha256"),
        sa.CheckConstraint(ORIGENS, name="ck_tse_snapshots_origem"),
    )
    op.create_index("ix_tse_snapshots_url_capturado", "tse_snapshots", ["url", "capturado_em"])
    op.create_index("ix_tse_snapshots_tipo", "tse_snapshots", ["origem", "tipo"])
    op.create_table(
        "tse_totalizacoes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("eleicao_id", sa.Integer(), nullable=False),
        sa.Column("cargo_id", sa.Integer(), nullable=False),
        sa.Column("abrangencia_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("idg", sa.String(length=20), nullable=True),
        sa.Column("gerado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultima_totalizacao", sa.DateTime(timezone=True), nullable=True),
        sa.Column("andamento", sa.String(length=2), nullable=True),
        sa.Column("totalizacao_final", sa.String(length=2), nullable=True),
        sa.Column("vagas", sa.Integer(), nullable=True),
        sa.Column("secoes_total", sa.Integer(), nullable=True),
        sa.Column("secoes_totalizadas", sa.Integer(), nullable=True),
        sa.Column("eleitores", sa.Integer(), nullable=True),
        sa.Column("comparecimento", sa.Integer(), nullable=True),
        sa.Column("abstencoes", sa.Integer(), nullable=True),
        sa.Column("votos_total", sa.Integer(), nullable=True),
        sa.Column("votos_validos", sa.Integer(), nullable=True),
        sa.Column("votos_nominais", sa.Integer(), nullable=True),
        sa.Column("votos_legenda", sa.Integer(), nullable=True),
        sa.Column("votos_brancos", sa.Integer(), nullable=True),
        sa.Column("votos_nulos", sa.Integer(), nullable=True),
        sa.Column("votos_anulados", sa.Integer(), nullable=True),
        sa.Column("votos_anulados_sub_judice", sa.Integer(), nullable=True),
        sa.Column("quociente_eleitoral", sa.Integer(), nullable=True),
        sa.Column("conteudo_hash", sa.String(length=64), nullable=False),
        _ts("capturado_em"),
        sa.ForeignKeyConstraint(["eleicao_id"], ["tse_eleicoes.id"]),
        sa.ForeignKeyConstraint(["cargo_id"], ["tse_cargos.id"]),
        sa.ForeignKeyConstraint(["abrangencia_id"], ["tse_abrangencias.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["tse_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "cargo_id", "abrangencia_id", "snapshot_id", name="uq_tse_totalizacoes_snapshot"
        ),
    )
    op.create_index(
        "ix_tse_totalizacoes_atual", "tse_totalizacoes", ["abrangencia_id", "cargo_id", "id"]
    )
    op.create_index("ix_tse_totalizacoes_eleicao", "tse_totalizacoes", ["eleicao_id", "cargo_id"])
    op.create_table(
        "tse_resultados_candidato",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("totalizacao_id", sa.Integer(), nullable=False),
        sa.Column("candidato_id", sa.Integer(), nullable=False),
        sa.Column("votos", sa.Integer(), nullable=False),
        sa.Column("percentual", sa.Numeric(7, 2), nullable=True),
        sa.Column("situacao", sa.String(length=60), nullable=True),
        sa.Column("eleito", sa.String(length=2), nullable=True),
        sa.Column("destinacao_voto", sa.String(length=60), nullable=True),
        sa.ForeignKeyConstraint(["totalizacao_id"], ["tse_totalizacoes.id"]),
        sa.ForeignKeyConstraint(["candidato_id"], ["tse_candidatos.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("totalizacao_id", "candidato_id", name="uq_tse_resultados_candidato"),
    )
    op.create_index(
        "ix_tse_resultados_candidato_candidato", "tse_resultados_candidato", ["candidato_id"]
    )
    op.create_table(
        "tse_resultados_partido",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("totalizacao_id", sa.Integer(), nullable=False),
        sa.Column("partido_id", sa.Integer(), nullable=False),
        sa.Column("votos_nominais", sa.Integer(), nullable=True),
        sa.Column("votos_legenda", sa.Integer(), nullable=True),
        sa.Column("destinacao_voto", sa.String(length=60), nullable=True),
        sa.ForeignKeyConstraint(["totalizacao_id"], ["tse_totalizacoes.id"]),
        sa.ForeignKeyConstraint(["partido_id"], ["tse_partidos.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("totalizacao_id", "partido_id", name="uq_tse_resultados_partido"),
    )
    op.create_index(
        "ix_tse_resultados_partido_partido", "tse_resultados_partido", ["partido_id"]
    )
    op.create_table(
        "tse_secoes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("origem", sa.String(length=10), nullable=False),
        sa.Column("pleito", sa.String(length=10), nullable=False),
        sa.Column("uf", sa.String(length=2), nullable=False),
        sa.Column("municipio_codigo", sa.String(length=5), nullable=False),
        sa.Column("zona", sa.String(length=4), nullable=False),
        sa.Column("secao", sa.String(length=4), nullable=False),
        sa.Column("eh_principal", sa.Boolean(), nullable=False),
        sa.Column("secao_principal", sa.String(length=4), nullable=True),
        sa.Column("auxiliar_em", sa.DateTime(timezone=True), nullable=True),
        _ts("criado_em"),
        _ts("atualizado_em"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "origem", "pleito", "uf", "municipio_codigo", "zona", "secao",
            name="uq_tse_secoes_natural",
        ),
        sa.CheckConstraint(ORIGENS, name="ck_tse_secoes_origem"),
        sa.CheckConstraint(
            "(eh_principal AND secao_principal IS NULL) OR "
            "(NOT eh_principal AND secao_principal IS NOT NULL)",
            name="ck_tse_secoes_agregacao",
        ),
    )
    op.create_index(
        "ix_tse_secoes_zona", "tse_secoes", ["origem", "pleito", "uf", "municipio_codigo", "zona"]
    )
    op.create_table(
        "tse_arquivos_secao",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("secao_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("hash", sa.String(length=200), nullable=False),
        sa.Column("situacao", sa.String(length=40), nullable=True),
        sa.Column("recebido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tipo_arquivo", sa.String(length=20), nullable=False),
        sa.Column("nome_arquivo", sa.String(length=200), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        _ts("capturado_em"),
        sa.ForeignKeyConstraint(["secao_id"], ["tse_secoes.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["tse_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("secao_id", "hash", "nome_arquivo", name="uq_tse_arquivos_secao"),
    )


def downgrade() -> None:
    # Ordem inversa das dependencias. Remove apenas tabelas `tse_*`.
    for tabela in (
        "tse_arquivos_secao",
        "tse_secoes",
        "tse_resultados_partido",
        "tse_resultados_candidato",
        "tse_totalizacoes",
        "tse_snapshots",
        "tse_candidatos",
        "tse_partidos",
        "tse_federacoes",
        "tse_abrangencias",
        "tse_cargos",
        "tse_eleicoes",
    ):
        op.drop_table(tabela)
