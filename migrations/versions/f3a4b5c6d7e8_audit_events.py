"""audit_events: trilha de seguranca e acesso

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-08-27 00:00:00.000000

ADR-039. Aditiva: cria uma tabela append-only e seus indices. Nenhuma tabela
existente e tocada. FKs nullable e SEM cascade -- apagar usuario/projeto nao
pode apagar a trilha.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "f3a4b5c6d7e8"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("company_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("attempted_email", sa.String(length=320), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("http_method", sa.String(length=10), nullable=True),
        sa.Column("path", sa.String(length=512), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("details", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["usuarios.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projetos.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for coluna in ("id", "occurred_at", "event_type", "severity", "user_id", "company_id", "project_id", "request_id"):
        op.create_index(f"ix_audit_events_{coluna}", "audit_events", [coluna])


def downgrade() -> None:
    for coluna in ("request_id", "project_id", "company_id", "user_id", "severity", "event_type", "occurred_at", "id"):
        op.drop_index(f"ix_audit_events_{coluna}", table_name="audit_events")
    op.drop_table("audit_events")
