"""setor referenciado por cenario de liderancas nao e excluivel (RESTRICT)

Revision ID: be8036a45b28
Revises: 0d497634c513
Create Date: 2026-09-16 18:00:00.000000

Hardening P0 (ADR-075). A migration 0d497634c513 criou
`lideranca_cenario_setores.setor_id -> setores.id` com ON DELETE CASCADE:
apagar um Setor apagaria em silencio a linha historica (snapshot oficial,
operacional, observacao) de TODOS os cenarios -- RASCUNHO, ATIVO e ARQUIVADO.
Esta revision troca a FK por ON DELETE RESTRICT, sem tocar em dados. A FK de
`cenario_id` (CASCADE) permanece: remocao controlada do proprio cenario segue
levando suas linhas. Reversivel: o downgrade restaura exatamente o CASCADE.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "be8036a45b28"
down_revision: Union[str, Sequence[str], None] = "0d497634c513"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABELA = "lideranca_cenario_setores"
# Nome dado pelo PostgreSQL a FK anonima criada em 0d497634c513.
FK_ANTIGA_PG = "lideranca_cenario_setores_setor_id_fkey"
FK_NOVA = "fk_lideranca_cenario_setores_setor_id_setores"

# SQLite nao nomeia FKs anonimas nem suporta ALTER CONSTRAINT: o batch mode
# recria a tabela, e a convencao da nome a FK refletida para que ela possa ser
# apagada. A convencao produz exatamente FK_NOVA para (setor_id -> setores).
CONVENCAO = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _trocar_fk(nome_atual: str, nome_novo: str, ondelete: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(nome_atual, TABELA, type_="foreignkey")
        op.create_foreign_key(nome_novo, TABELA, "setores", ["setor_id"], ["id"], ondelete=ondelete)
        return
    # SQLite: a FK anonima recebe o nome da convencao (= FK_NOVA) ao ser
    # refletida; depois do upgrade ela ja se chama FK_NOVA explicitamente.
    with op.batch_alter_table(TABELA, naming_convention=CONVENCAO) as batch:
        batch.drop_constraint(FK_NOVA, type_="foreignkey")
        batch.create_foreign_key(FK_NOVA, "setores", ["setor_id"], ["id"], ondelete=ondelete)


def upgrade() -> None:
    _trocar_fk(FK_ANTIGA_PG, FK_NOVA, "RESTRICT")


def downgrade() -> None:
    # Restaura EXATAMENTE o estado anterior, inclusive o nome que o PostgreSQL
    # deu a FK anonima: um novo upgrade precisa encontra-la por esse nome.
    _trocar_fk(FK_NOVA, FK_ANTIGA_PG, "CASCADE")
