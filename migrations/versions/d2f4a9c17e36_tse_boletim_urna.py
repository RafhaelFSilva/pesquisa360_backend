"""boletim de urna (BU): resultado oficial por secao

Revision ID: d2f4a9c17e36
Revises: bcab956ae48e
Create Date: 2026-10-04 23:30:00.000000

Aditiva (ADR-094). Quatro tabelas globais `tse_*`, sem `company_id`:

- `tse_boletins_urna`: uma linha por versao de arquivo de BU (append-only);
- `tse_bu_cargos`: totais do cargo no boletim (aptos, comparecimento,
  nominais, legenda, brancos, nulos);
- `tse_bu_votos`: voto nominal por candidato e de legenda por partido;
- `tse_bu_controle`: estado da ingestao por secao principal e ponteiro para
  o BU corrente.

Nenhuma tabela existente e alterada: o EA20 segue sendo a fonte de UF,
municipio e zona.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d2f4a9c17e36"
down_revision: Union[str, Sequence[str], None] = "bcab956ae48e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tse_boletins_urna",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("origem", sa.String(length=10), nullable=False),
        sa.Column("pleito", sa.String(length=10), nullable=False),
        sa.Column("secao_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("tamanho_bytes", sa.Integer(), nullable=False),
        sa.Column("hash_ea18", sa.String(length=200), nullable=False),
        sa.Column("situacao_ea18", sa.String(length=40), nullable=True),
        sa.Column("recebido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tipo_bu", sa.String(length=10), nullable=False),
        sa.Column("local_votacao", sa.Integer(), nullable=True),
        sa.Column("comparecimento", sa.Integer(), nullable=False),
        sa.Column("gerado_local", sa.DateTime(timezone=False), nullable=True),
        sa.Column("emitido_local", sa.DateTime(timezone=False), nullable=True),
        sa.Column("abertura_local", sa.DateTime(timezone=False), nullable=True),
        sa.Column("encerramento_local", sa.DateTime(timezone=False), nullable=True),
        sa.Column("tipo_urna", sa.Integer(), nullable=True),
        sa.Column("tipo_arquivo", sa.Integer(), nullable=True),
        sa.Column("versao_votacao", sa.String(length=120), nullable=True),
        sa.Column("numero_interno_urna", sa.Integer(), nullable=True),
        sa.Column("codigo_carga", sa.String(length=40), nullable=True),
        sa.Column("processado_em", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["secao_id"], ["tse_secoes.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["tse_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("secao_id", "sha256", name="uq_tse_boletins_urna_arquivo"),
        sa.UniqueConstraint("snapshot_id", name="uq_tse_boletins_urna_snapshot"),
        sa.CheckConstraint("origem IN ('OFICIAL', 'SIMULADO')",
                           name="ck_tse_boletins_urna_origem"),
    )
    op.create_table(
        "tse_bu_cargos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("boletim_id", sa.Integer(), nullable=False),
        sa.Column("eleicao_id", sa.Integer(), nullable=False),
        sa.Column("cargo_id", sa.Integer(), nullable=False),
        sa.Column("tipo_cargo", sa.String(length=12), nullable=False),
        sa.Column("eleitores_aptos", sa.Integer(), nullable=False),
        sa.Column("comparecimento", sa.Integer(), nullable=False),
        sa.Column("votos_nominais", sa.Integer(), nullable=False),
        sa.Column("votos_legenda", sa.Integer(), nullable=False),
        sa.Column("votos_brancos", sa.Integer(), nullable=False),
        sa.Column("votos_nulos", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["boletim_id"], ["tse_boletins_urna.id"]),
        sa.ForeignKeyConstraint(["eleicao_id"], ["tse_eleicoes.id"]),
        sa.ForeignKeyConstraint(["cargo_id"], ["tse_cargos.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("boletim_id", "cargo_id", name="uq_tse_bu_cargos"),
    )
    op.create_index("ix_tse_bu_cargos_cargo", "tse_bu_cargos", ["cargo_id", "boletim_id"])
    op.create_table(
        "tse_bu_votos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bu_cargo_id", sa.Integer(), nullable=False),
        sa.Column("tipo", sa.String(length=8), nullable=False),
        sa.Column("numero", sa.String(length=10), nullable=False),
        sa.Column("partido_numero", sa.String(length=5), nullable=False),
        sa.Column("candidato_id", sa.Integer(), nullable=True),
        sa.Column("partido_id", sa.Integer(), nullable=True),
        sa.Column("votos", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["bu_cargo_id"], ["tse_bu_cargos.id"]),
        sa.ForeignKeyConstraint(["candidato_id"], ["tse_candidatos.id"]),
        sa.ForeignKeyConstraint(["partido_id"], ["tse_partidos.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bu_cargo_id", "tipo", "numero", name="uq_tse_bu_votos"),
        sa.CheckConstraint("tipo IN ('NOMINAL', 'LEGENDA')", name="ck_tse_bu_votos_tipo"),
    )
    op.create_index("ix_tse_bu_votos_candidato", "tse_bu_votos", ["candidato_id"])
    op.create_index("ix_tse_bu_votos_partido", "tse_bu_votos", ["partido_id"])
    op.create_table(
        "tse_bu_controle",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("secao_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("boletim_id", sa.Integer(), nullable=True),
        sa.Column("auxiliar_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tentativas", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("erro", sa.String(length=300), nullable=True),
        sa.Column("verificado_em", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["secao_id"], ["tse_secoes.id"]),
        sa.ForeignKeyConstraint(["boletim_id"], ["tse_boletins_urna.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("secao_id", name="uq_tse_bu_controle_secao"),
        sa.CheckConstraint("status IN ('PROCESSADO', 'AGUARDANDO', 'ERRO')",
                           name="ck_tse_bu_controle_status"),
    )
    op.create_index("ix_tse_bu_controle_boletim", "tse_bu_controle", ["boletim_id"])


def downgrade() -> None:
    op.drop_table("tse_bu_controle")
    op.drop_table("tse_bu_votos")
    op.drop_table("tse_bu_cargos")
    op.drop_table("tse_boletins_urna")
