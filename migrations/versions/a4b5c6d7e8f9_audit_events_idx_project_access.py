"""ADR-039: indice composto para deduplicacao de PROJECT_ACCESS.

A consulta "ultimo PROJECT_ACCESS deste usuario neste projeto" filtra por
(event_type, user_id, project_id) e ordena por occurred_at DESC. Os indices
simples existentes obrigariam a intersectar tres deles; este cobre a consulta
inteira.

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
"""
from alembic import op

revision = "a4b5c6d7e8f9"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None

INDICE = "ix_audit_events_tipo_usuario_projeto_data"


def upgrade() -> None:
    op.create_index(INDICE, "audit_events", ["event_type", "user_id", "project_id", "occurred_at"])


def downgrade() -> None:
    op.drop_index(INDICE, table_name="audit_events")
