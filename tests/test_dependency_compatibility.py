"""Regressoes da pilha SQLAlchemy/GeoAlchemy2 usada pelas migrations."""

from geoalchemy2 import Geometry  # noqa: F401 - ativa os listeners DDL globais
from sqlalchemy import Column, Integer, MetaData, Table, create_mock_engine


def test_geoalchemy_listener_accepts_non_spatial_create_table():
    """O hook global deve aceitar as tabelas nao espaciais criadas pelo Alembic."""

    metadata = MetaData()
    table = Table("compat_non_spatial", metadata, Column("id", Integer, primary_key=True))
    engine = create_mock_engine("postgresql+psycopg2://", lambda *args, **kwargs: None)

    table.create(engine)
