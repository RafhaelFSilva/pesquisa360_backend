"""metadados oficiais dos locais de votacao (nome e endereco), por pleito

Revision ID: a7c3e91b5d24
Revises: d2f4a9c17e36
Create Date: 2026-10-06 12:00:00.000000

Aditiva (ADR-092). Uma tabela global `tse_locais_votacao`, sem `company_id`.
Guarda so metadado: o vinculo secao -> local continua vindo do Boletim de
Urna e nenhuma tabela de voto e alterada.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a7c3e91b5d24"
down_revision: Union[str, Sequence[str], None] = "d2f4a9c17e36"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tse_locais_votacao",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("origem", sa.String(length=10), nullable=False),
        sa.Column("pleito", sa.String(length=10), nullable=False),
        sa.Column("uf", sa.String(length=2), nullable=False),
        sa.Column("municipio_codigo", sa.String(length=5), nullable=False),
        sa.Column("zona", sa.String(length=4), nullable=False),
        sa.Column("codigo_local", sa.String(length=4), nullable=False),
        sa.Column("nome", sa.String(length=200), nullable=False),
        sa.Column("endereco", sa.String(length=300), nullable=True),
        sa.Column("bairro", sa.String(length=120), nullable=True),
        sa.Column("secoes_cadastradas", sa.Integer(), nullable=False),
        sa.Column("secoes_realocadas", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("fonte", sa.String(length=60), nullable=False),
        sa.Column("fonte_url", sa.Text(), nullable=True),
        sa.Column("fonte_gerada_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("origem", "pleito", "uf", "municipio_codigo", "zona", "codigo_local",
                            name="uq_tse_locais_votacao_natural"),
        sa.CheckConstraint("origem IN ('OFICIAL', 'SIMULADO')",
                           name="ck_tse_locais_votacao_origem"),
    )


def downgrade() -> None:
    op.drop_table("tse_locais_votacao")
