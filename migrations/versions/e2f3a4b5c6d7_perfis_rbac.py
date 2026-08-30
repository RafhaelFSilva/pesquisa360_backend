"""perfis do RBAC: Coordenador, Supervisor e Cliente

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-08-27 00:00:00.000000

ADR-037. O sistema so possuia Agente, Gerente e Superadmin -- os papeis
Coordenador, Supervisor e Cliente existiam na regra de negocio, mas nao no
banco, entao nao havia como atribui-los a ninguem.

Esta migration apenas SEMEIA os tres perfis. Nao cria tabela de permissoes: a
matriz vive em `core/rbac.py`, onde passa por code review. Nenhum usuario e
alterado, e o INSERT e idempotente (NOT EXISTS) -- rodar duas vezes nao duplica.
"""
from alembic import op


revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None

PERFIS = [
    ("Coordenador", "Gestao completa dos projetos aos quais tem acesso"),
    ("Supervisor", "Operacao de campo: monitoramento, setores e cotas"),
    ("Cliente", "Somente leitura dos projetos autorizados"),
]


def upgrade() -> None:
    for nome, descricao in PERFIS:
        op.execute(
            f"""
            INSERT INTO perfis (nome, descricao)
            SELECT '{nome}', '{descricao}'
             WHERE NOT EXISTS (SELECT 1 FROM perfis WHERE lower(nome) = lower('{nome}'))
            """
        )


def downgrade() -> None:
    # So remove perfis que ninguem esteja usando: apagar um perfil em uso
    # deixaria usuarios orfaos de papel.
    for nome, _ in PERFIS:
        op.execute(
            f"""
            DELETE FROM perfis
             WHERE lower(nome) = lower('{nome}')
               AND NOT EXISTS (SELECT 1 FROM usuarios u WHERE u.perfil_id = perfis.id)
            """
        )
