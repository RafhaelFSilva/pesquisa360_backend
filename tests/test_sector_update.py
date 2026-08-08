import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from shapely.geometry import mapping
from shapely import wkt as shapely_wkt
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


os.environ.setdefault("SECRET_KEY", "test-only-sector-update-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360 import crud, schemas
from pesquisa360.api.endpoints import projetos
from pesquisa360.core.dependencies import get_current_user, get_db


def user(user_id: int, company_id: int, profile: str, active: bool = True):
    return SimpleNamespace(
        id=user_id,
        company_id=company_id,
        ativo=active,
        perfil=SimpleNamespace(nome=profile),
    )


@pytest.fixture
def database():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    raw = engine.raw_connection()
    raw.create_function("ST_GeomFromText", 2, lambda value, srid: value)
    as_geojson = lambda value: json.dumps(mapping(shapely_wkt.loads(value))) if value else None
    raw.create_function("AsEWKB", 1, lambda value: value)
    raw.create_function("ST_AsGeoJSON", 1, as_geojson)
    raw.create_function("AsGeoJSON", 1, as_geojson)
    raw.close()
    Session = sessionmaker(bind=engine)

    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE companies (
                id INTEGER PRIMARY KEY, name TEXT, cnpj TEXT, logo_url TEXT,
                is_active BOOLEAN, created_at DATETIME
            )
        """))
        connection.execute(text("""
            CREATE TABLE perfis (id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT)
        """))
        connection.execute(text("""
            CREATE TABLE usuarios (
                id INTEGER PRIMARY KEY, email TEXT, nome TEXT, senha_hash TEXT,
                ativo BOOLEAN, perfil_id INTEGER, company_id INTEGER
            )
        """))
        connection.execute(text("""
            CREATE TABLE projetos (
                id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT, status TEXT,
                data_inicio DATE, data_fim DATE, coordenador_id INTEGER, company_id INTEGER
            )
        """))
        connection.execute(text("""
            CREATE TABLE pesquisas (
                id INTEGER PRIMARY KEY, titulo TEXT, tipo_pesquisa TEXT, ativo BOOLEAN,
                projeto_id INTEGER, cerca_eletronica TEXT, tolerancia_metros INTEGER
            )
        """))
        connection.execute(text("""
            CREATE TABLE setores (
                id INTEGER PRIMARY KEY, nome TEXT, meta INTEGER, tolerancia INTEGER,
                finalidade TEXT DEFAULT 'OPERACAO' NOT NULL,
                geometria TEXT, pesquisa_id INTEGER, agente_id INTEGER
            )
        """))
        connection.execute(text("INSERT INTO companies VALUES (10, 'A', NULL, NULL, 1, NULL), (20, 'B', NULL, NULL, 1, NULL)"))
        connection.execute(text("INSERT INTO perfis VALUES (1, 'Gerente', NULL), (2, 'Agente', NULL)"))
        connection.execute(text("""
            INSERT INTO usuarios VALUES
              (1, 'manager@a', 'Manager A', 'x', 1, 1, 10),
              (2, 'agent@a', 'Agent A', 'x', 1, 2, 10),
              (3, 'agent2@a', 'Agent A2', 'x', 1, 2, 10),
              (4, 'agent@b', 'Agent B', 'x', 1, 2, 20),
              (5, 'inactive@a', 'Inactive A', 'x', 0, 2, 10)
        """))
        connection.execute(text("""
            INSERT INTO projetos VALUES
              (100, 'Project A', NULL, 'Ativo', NULL, NULL, 1, 10),
              (200, 'Project B', NULL, 'Ativo', NULL, NULL, 1, 20)
        """))
        connection.execute(text("""
            INSERT INTO pesquisas VALUES
              (1000, 'Survey A', NULL, 1, 100, NULL, NULL),
              (1001, 'Survey A2', NULL, 1, 100, NULL, NULL),
              (2000, 'Survey B', NULL, 1, 200, NULL, NULL)
        """))
        connection.execute(text("""
            INSERT INTO setores VALUES
              (500, 'Original', 10, 50, 'OPERACAO', 'POLYGON ((-51 0, -50 0, -50 1, -51 0))', 1000, 2),
              (501, 'Other survey', 20, 60, 'OPERACAO', 'POLYGON ((-49 0, -48 0, -48 1, -49 0))', 1001, 2),
              (502, 'Analitico', 0, 0, 'RELATORIO', 'POLYGON ((-51 1, -50 1, -50 2, -51 1))', 1000, NULL)
        """))

    yield engine, Session
    engine.dispose()


def update(db, payload, **ids):
    return crud.update_setor(
        db=db,
        projeto_id=ids.get("project_id", 100),
        pesquisa_id=ids.get("survey_id", 1000),
        setor_id=ids.get("sector_id", 500),
        setor_update=schemas.SetorUpdate(**payload),
        current_user=ids.get("current_user", user(1, 10, "Gerente")),
    )


def row(db, sector_id=500):
    return db.execute(
        text("SELECT id, nome, meta, tolerancia, finalidade, geometria, pesquisa_id, agente_id FROM setores WHERE id=:id"),
        {"id": sector_id},
    ).mappings().one()


def assert_not_found(operation):
    with pytest.raises(HTTPException) as exc:
        operation()
    assert exc.value.status_code == 404


def test_updates_only_name_and_preserves_id_and_geometry(database):
    _, Session = database
    with Session() as db:
        before = row(db)
        updated = update(db, {"nome": "Updated"})
        after = row(db)
    assert updated.id == 500
    assert after["id"] == 500
    assert after["nome"] == "Updated"
    assert after["geometria"] == before["geometria"]
    assert after["meta"] == before["meta"]


def test_updates_meta_and_tolerance(database):
    _, Session = database
    with Session() as db:
        update(db, {"meta": 94, "tolerancia_metros": 100})
        after = row(db)
    assert (after["meta"], after["tolerancia"]) == (94, 100)


def test_updates_finalidade(database):
    _, Session = database
    with Session() as db:
        update(db, {"finalidade": "RELATORIO"})
        after = row(db)
    assert after["finalidade"] == "RELATORIO"


def test_changes_operacao_to_relatorio_and_removes_agent(database):
    _, Session = database
    with Session() as db:
        update(db, {"finalidade": "RELATORIO", "agente_id": None})
        after = row(db)
    assert after["finalidade"] == "RELATORIO"
    assert after["agente_id"] is None


def test_changes_relatorio_to_operacao_preserving_current_rules(database):
    _, Session = database
    with Session() as db:
        update(
            db,
            {"finalidade": "OPERACAO", "agente_id": 2},
            sector_id=502,
        )
        after = row(db, 502)
    assert after["finalidade"] == "OPERACAO"
    assert after["agente_id"] == 2


def test_create_without_finalidade_defaults_to_operacao(database):
    _, Session = database
    with Session() as db:
        setor = crud.create_setor(
            db=db,
            pesquisa_id=1000,
            current_user=user(1, 10, "Gerente"),
            setor_in=schemas.SetorCreate(
                nome="Novo operacional",
                meta=10,
                geometria_coords=[[0, -51], [0, -50], [1, -50]],
            ),
        )
        created = row(db, setor.id)
    assert created["finalidade"] == "OPERACAO"
    assert created["agente_id"] is None


def test_create_relatorio_without_agent_is_valid(database):
    _, Session = database
    with Session() as db:
        setor = crud.create_setor(
            db=db,
            pesquisa_id=1000,
            current_user=user(1, 10, "Gerente"),
            setor_in=schemas.SetorCreate(
                nome="Analitico",
                meta=10,
                finalidade="RELATORIO",
                geometria_coords=[[0, -51], [0, -50], [1, -50]],
            ),
        )
        created = row(db, setor.id)
    assert created["finalidade"] == "RELATORIO"
    assert created["agente_id"] is None


def test_create_operacao_and_ambos_preserve_current_optional_agent_rule(database):
    _, Session = database
    for finalidade in ("OPERACAO", "AMBOS"):
        with Session() as db:
            setor = crud.create_setor(
                db=db,
                pesquisa_id=1000,
                current_user=user(1, 10, "Gerente"),
                setor_in=schemas.SetorCreate(
                    nome=f"Setor {finalidade}",
                    meta=10,
                    finalidade=finalidade,
                    geometria_coords=[[0, -51], [0, -50], [1, -50]],
                ),
            )
            created = row(db, setor.id)
        assert created["finalidade"] == finalidade
        assert created["agente_id"] is None


def test_changes_to_active_agent_from_same_tenant(database):
    _, Session = database
    with Session() as db:
        update(db, {"agente_id": 3})
        assert row(db)["agente_id"] == 3


def test_updates_geometry_as_polygon_4326(database):
    _, Session = database
    with Session() as db:
        update(db, {"poligono": [
            {"lat": 0, "lng": -52},
            {"lat": 0, "lng": -51},
            {"lat": 1, "lng": -51},
        ]})
        geometry = shapely_wkt.loads(row(db)["geometria"])
    assert geometry.geom_type == "Polygon"
    assert geometry.bounds == (-52.0, 0.0, -51.0, 1.0)


@pytest.mark.parametrize(
    ("ids", "detail"),
    (
        ({"sector_id": 999}, "Setor"),
        ({"project_id": 200, "survey_id": 2000}, "Projeto"),
        ({"survey_id": 2000}, "Pesquisa"),
        ({"sector_id": 501}, "Setor"),
    ),
)
def test_rejects_resources_outside_valid_chain(database, ids, detail):
    _, Session = database
    with Session() as db:
        with pytest.raises(HTTPException, match=detail) as exc:
            update(db, {"nome": "No"}, **ids)
    assert exc.value.status_code == 404


@pytest.mark.parametrize("agent_id", (4, 5))
def test_rejects_agent_from_other_tenant_or_inactive(database, agent_id):
    _, Session = database
    with Session() as db:
        assert_not_found(lambda: update(db, {"agente_id": agent_id}))


def test_invalid_polygon_leaves_all_fields_unchanged(database):
    _, Session = database
    with Session() as db:
        before = dict(row(db))
        with pytest.raises(ValueError, match="Polygon invalido"):
            update(db, {
                "nome": "Must not persist",
                "poligono": [
                    {"lat": 0, "lng": 0},
                    {"lat": 1, "lng": 1},
                    {"lat": 0, "lng": 1},
                    {"lat": 1, "lng": 0},
                ],
            })
        assert dict(row(db)) == before


def test_polygon_with_fewer_than_three_points_is_rejected():
    payload = schemas.SetorUpdate(poligono=[{"lat": 0, "lng": 0}, {"lat": 1, "lng": 1}])
    with pytest.raises(ValueError, match="tres pontos"):
        payload.get_coords()


def test_rolls_back_when_commit_fails(database):
    engine, Session = database
    with Session() as db, patch.object(db, "commit", side_effect=RuntimeError("commit failed")):
        with pytest.raises(RuntimeError, match="commit failed"):
            update(db, {"nome": "Must rollback"})
    with Session() as verification:
        assert row(verification)["nome"] == "Original"


def test_patch_route_requires_administrative_profile(database):
    _, Session = database
    app = FastAPI()
    app.include_router(projetos.router)

    def override_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user(2, 10, "Agente")
    with TestClient(app) as client:
        response = client.patch(
            "/projetos/100/pesquisas/1000/setores/500",
            json={"nome": "Forbidden"},
        )
    assert response.status_code == 403


def test_patch_returns_listing_format_and_preserves_id(database):
    _, Session = database
    app = FastAPI()
    app.include_router(projetos.router)

    def override_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user(1, 10, "Gerente")
    with TestClient(app) as client:
        response = client.patch(
            "/projetos/100/pesquisas/1000/setores/500",
            json={"nome": "Through PATCH"},
        )
    assert response.status_code == 200
    assert response.json()["id"] == 500
    assert response.json()["nome"] == "Through PATCH"
    assert response.json()["geometria"]["type"] == "Polygon"
    assert response.json()["finalidade"] == "OPERACAO"


def test_list_returns_finalidade(database):
    _, Session = database
    app = FastAPI()
    app.include_router(projetos.router)

    def override_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user(1, 10, "Gerente")
    with TestClient(app) as client:
        response = client.get("/projetos/100/pesquisas/1000/setores")
    assert response.status_code == 200
    assert response.json()[0]["finalidade"] == "OPERACAO"


def test_list_can_filter_by_finalidade_without_changing_default(database):
    _, Session = database
    app = FastAPI()
    app.include_router(projetos.router)

    def override_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user(1, 10, "Gerente")
    with TestClient(app) as client:
        all_response = client.get("/projetos/100/pesquisas/1000/setores")
        relatorio_response = client.get("/projetos/100/pesquisas/1000/setores?finalidade=RELATORIO")
    assert all_response.status_code == 200
    assert relatorio_response.status_code == 200
    assert {item["finalidade"] for item in all_response.json()} == {"OPERACAO", "RELATORIO"}
    assert [item["id"] for item in relatorio_response.json()] == [502]


def test_create_route_rejects_invalid_finalidade_with_422(database):
    _, Session = database
    app = FastAPI()
    app.include_router(projetos.router)

    def override_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user(1, 10, "Gerente")
    with TestClient(app) as client:
        response = client.post(
            "/projetos/100/pesquisas/1000/setores",
            json={
                "nome": "Invalido",
                "meta": 10,
                "finalidade": "ANALITICO",
                "poligono": [
                    {"lat": 0, "lng": -51},
                    {"lat": 0, "lng": -50},
                    {"lat": 1, "lng": -50},
                ],
            },
        )
    assert response.status_code == 422


def test_patch_returns_400_for_topologically_invalid_polygon(database):
    _, Session = database
    app = FastAPI()
    app.include_router(projetos.router)

    def override_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user(1, 10, "Gerente")
    with TestClient(app) as client:
        response = client.patch(
            "/projetos/100/pesquisas/1000/setores/500",
            json={
                "nome": "Must not persist",
                "poligono": [
                    {"lat": 0, "lng": 0},
                    {"lat": 1, "lng": 1},
                    {"lat": 0, "lng": 1},
                    {"lat": 1, "lng": 0},
                ],
            },
        )
    assert response.status_code == 400
    with Session() as verification:
        assert row(verification)["nome"] == "Original"
