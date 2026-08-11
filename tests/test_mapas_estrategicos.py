import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from shapely import wkt as shapely_wkt
from shapely.geometry import mapping
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SECRET_KEY", "test-only-mapas-estrategicos-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.api.endpoints import relatorios
from pesquisa360.core.dependencies import get_current_user, get_db


def user(user_id: int = 1, company_id: int = 10):
    return SimpleNamespace(
        id=user_id,
        company_id=company_id,
        ativo=True,
        perfil=SimpleNamespace(nome="Gerente"),
    )


@pytest.fixture
def database():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def register_spatial_functions(connection, _):
        def geom(value):
            if value is None:
                return None
            if isinstance(value, bytes):
                value = value.decode()
            return shapely_wkt.loads(str(value).split(";", 1)[-1])

        def as_geojson(value):
            return json.dumps(mapping(geom(value))) if value else None

        connection.create_function("AsEWKB", 1, lambda value: value)
        connection.create_function("ST_GeomFromText", 2, lambda value, srid: value)
        connection.create_function("ST_AsGeoJSON", 1, as_geojson)
        connection.create_function("AsGeoJSON", 1, as_geojson)
        connection.create_function("ST_Covers", 2, lambda polygon, point: int(geom(polygon).covers(geom(point))) if polygon and point else 0)
        connection.create_function("ST_Intersection", 2, lambda left, right: geom(left).intersection(geom(right)).wkt if left and right else None)
        connection.create_function("ST_Area", 1, lambda value: geom(value).area if value else 0)
        connection.create_function("ST_X", 1, lambda value: geom(value).x if value else None)
        connection.create_function("ST_Y", 1, lambda value: geom(value).y if value else None)

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
            CREATE TABLE perguntas (
                id INTEGER PRIMARY KEY, texto_pergunta TEXT, tipo_pergunta TEXT,
                ordem INTEGER, eh_obrigatoria BOOLEAN, eh_resposta_espontanea BOOLEAN,
                ativo BOOLEAN, pesquisa_id INTEGER
            )
        """))
        connection.execute(text("""
            CREATE TABLE coletas (
                id INTEGER PRIMARY KEY, pesquisa_id INTEGER, agente_id INTEGER,
                company_id INTEGER, client_uuid TEXT, foi_offline BOOLEAN,
                endereco_estimado TEXT, status_sincronizacao TEXT,
                data_inicio_coleta DATETIME, data_fim_coleta DATETIME,
                localizacao_inicio TEXT, localizacao_fim TEXT,
                inconformidade_localizacao BOOLEAN
            )
        """))
        connection.execute(text("""
            CREATE TABLE respostas (
                id INTEGER PRIMARY KEY, pergunta_id INTEGER, coleta_id INTEGER,
                valor_resposta TEXT
            )
        """))
        connection.execute(text("""
            CREATE TABLE categorias_resposta_espontanea (
                id INTEGER PRIMARY KEY, pesquisa_id INTEGER, nome TEXT,
                nome_normalizado TEXT, ativo BOOLEAN, criado_por_id INTEGER,
                atualizado_por_id INTEGER, criado_em DATETIME, atualizado_em DATETIME
            )
        """))
        connection.execute(text("""
            CREATE TABLE mapeamentos_resposta_espontanea (
                id INTEGER PRIMARY KEY, pesquisa_id INTEGER, categoria_id INTEGER,
                chave_normalizada TEXT, texto_referencia TEXT, ativo BOOLEAN,
                criado_por_id INTEGER, atualizado_por_id INTEGER,
                criado_em DATETIME, atualizado_em DATETIME
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
              (2, 'agent@a', 'Agent A', 'x', 1, 2, 10)
        """))
        connection.execute(text("INSERT INTO projetos VALUES (100, 'Projeto A', NULL, 'Ativo', NULL, NULL, 1, 10), (200, 'Projeto B', NULL, 'Ativo', NULL, NULL, 1, 20)"))
        connection.execute(text("INSERT INTO pesquisas VALUES (1000, 'Pesquisa A', NULL, 1, 100, NULL, NULL), (2000, 'Pesquisa B', NULL, 1, 200, NULL, NULL)"))
        connection.execute(text("""
            INSERT INTO perguntas VALUES
              (100, 'Voto', 'ESCOLHA_SIMPLES', 1, 1, 0, 1, 1000),
              (101, 'Lembranca espontanea', 'TEXTO', 2, 0, 1, 1, 1000)
        """))
        connection.execute(text("""
            INSERT INTO categorias_resposta_espontanea VALUES
              (1, 1000, 'Clecio', 'clecio', 1, 1, 1, NULL, NULL),
              (2, 1000, 'Outro candidato', 'outro candidato', 0, 1, 1, NULL, NULL)
        """))
        connection.execute(text("""
            INSERT INTO mapeamentos_resposta_espontanea VALUES
              (1, 1000, 1, 'clecio', 'Clecio', 1, 1, 1, NULL, NULL),
              (2, 1000, 2, 'inativo', 'Inativo', 1, 1, 1, NULL, NULL)
        """))
        connection.execute(text("""
            INSERT INTO setores VALUES
              (10, 'Operacional', 10, 50, 'OPERACAO', 'POLYGON ((-1 -1, -0.1 -1, -0.1 -0.1, -1 -0.1, -1 -1))', 1000, 2),
              (11, 'Analitico A', 0, 0, 'RELATORIO', 'POLYGON ((0 0, 1 0, 1 1, 0 1, 0 0))', 1000, NULL),
              (12, 'Analitico B', 20, 50, 'AMBOS', 'POLYGON ((1.5 0, 2 0, 2 1, 1.5 1, 1.5 0))', 1000, 2),
              (13, 'Analitico C', 0, 0, 'RELATORIO', 'POLYGON ((0.75 0.75, 1.25 0.75, 1.25 1.25, 0.75 1.25, 0.75 0.75))', 1000, NULL),
              (20, 'Outro tenant', 0, 0, 'RELATORIO', 'POLYGON ((0 0, 1 0, 1 1, 0 1, 0 0))', 2000, NULL)
        """))
        started = datetime(2026, 1, 10, tzinfo=timezone.utc).isoformat()
        rows = [
            (1, "POINT (0.2 0.2)", None, "Sim"),
            (2, "POINT (1.75 0.5)", None, "Nao"),
            (3, "POINT (0.8 0.8)", None, "Sim"),
            (4, "POINT (5 5)", None, "Nao"),
            (5, None, None, "Sim"),
            (6, None, "POINT (1.8 0.7)", "Sim"),
            (7, "POINT (-0.5 -0.5)", None, "Nao"),
        ]
        for coleta_id, inicio, fim, resposta in rows:
            connection.execute(
                text("""
                    INSERT INTO coletas VALUES
                    (:id, 1000, 2, 10, :uuid, 0, NULL, 'ok', :started, :started, :inicio, :fim, 0)
                """),
                {"id": coleta_id, "uuid": f"uuid-{coleta_id}", "started": started, "inicio": inicio, "fim": fim},
            )
            connection.execute(
                text("INSERT INTO respostas VALUES (:id, 100, :coleta_id, :resposta)"),
                {"id": coleta_id, "coleta_id": coleta_id, "resposta": resposta},
            )
        connection.execute(text("""
            INSERT INTO respostas VALUES
              (101, 101, 1, 'Clécio'),
              (102, 101, 2, ' CLECIO '),
              (103, 101, 6, 'Nome livre')
        """))

    yield engine, Session
    engine.dispose()


@pytest.fixture
def client(database):
    _engine, Session = database
    app = FastAPI()
    app.include_router(relatorios.router)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user()
    return TestClient(app)


def test_diagnostico_classifica_base_analitica_sem_duplicar(client):
    response = client.get("/relatorios/pesquisas/1000/mapas/territorio/diagnostico/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_setores_analiticos"] == 3
    assert payload["total_coletas"] == 7
    assert payload["total_classificado"] == 3
    assert payload["total_sem_setor"] == 2
    assert payload["total_conflito_setor"] == 1
    assert payload["total_sem_coordenada"] == 1
    assert [(item["setor_a"]["id"], item["setor_b"]["id"]) for item in payload["conflitos_geometricos"]] == [(11, 13)]


def test_preview_distribuicao_usa_apenas_relatorio_e_ambos(client):
    response = client.post(
        "/relatorios/pesquisas/1000/mapas/preview/",
        json={"tipo_mapa": "DISTRIBUICAO_SETOR", "setor_ids": [11, 12]},
    )

    assert response.status_code == 200
    payload = response.json()
    valores = {item["setor_id"]: item["valor"] for item in payload["dados"]}
    assert valores == {11: 2, 12: 2}
    assert payload["total_conflito_setor"] == 0
    assert payload["total_sem_setor"] == 2
    assert payload["total_sem_coordenada"] == 1


def test_preview_rejeita_setor_operacional(client):
    response = client.post(
        "/relatorios/pesquisas/1000/mapas/preview/",
        json={"tipo_mapa": "DISTRIBUICAO_SETOR", "setor_ids": [10]},
    )

    assert response.status_code == 404


def test_preview_resultado_por_setor_com_percentual(client):
    response = client.post(
        "/relatorios/pesquisas/1000/mapas/preview/",
        json={"tipo_mapa": "RESULTADO_SETOR", "pergunta_id": 100, "resposta": "Sim"},
    )

    assert response.status_code == 200
    dados = {item["setor_id"]: item for item in response.json()["dados"]}
    assert dados[11]["valor"] == 1
    assert dados[11]["percentual"] == 100.0
    assert dados[12]["valor"] == 1
    assert dados[12]["total_respostas_validas"] == 2
    assert dados[12]["percentual"] == 50.0


def test_preview_lideranca_por_setor(client):
    response = client.post(
        "/relatorios/pesquisas/1000/mapas/preview/",
        json={"tipo_mapa": "LIDERANCA_SETOR", "pergunta_id": 100},
    )

    assert response.status_code == 200
    dados = {item["setor_id"]: item for item in response.json()["dados"]}
    assert dados[11]["lider"] == "Sim"
    assert dados[11]["margem"] == 100.0
    assert dados[12]["lider"] == "Empate"
    assert dados[12]["empatados"] == ["Nao", "Sim"]
    assert dados[12]["lider_total"] == 1
    assert dados[12]["segundo_total"] == 0
    assert dados[12]["margem"] == 0.0


def test_preview_resultado_espontaneo_agrega_categoria_normalizada(client):
    response = client.post(
        "/relatorios/pesquisas/1000/mapas/preview/",
        json={"tipo_mapa": "RESULTADO_SETOR", "pergunta_id": 101, "resposta": "Clecio"},
    )

    assert response.status_code == 200
    dados = {item["setor_id"]: item for item in response.json()["dados"]}
    assert dados[11]["valor"] == 1
    assert dados[11]["percentual"] == 100.0
    assert dados[12]["valor"] == 1
    assert dados[12]["total_respostas_validas"] == 2
    assert dados[12]["percentual"] == 50.0


def test_preview_lideranca_espontanea_preserva_nao_categorizada_e_empate(client):
    response = client.post(
        "/relatorios/pesquisas/1000/mapas/preview/",
        json={"tipo_mapa": "LIDERANCA_SETOR", "pergunta_id": 101},
    )

    assert response.status_code == 200
    dados = {item["setor_id"]: item for item in response.json()["dados"]}
    assert dados[11]["lider"] == "Clecio"
    assert dados[12]["lider"] == "Empate"
    assert dados[12]["empatados"] == ["Clecio", "Não categorizada"]
    assert dados[12]["total_respostas_validas"] == 2


def test_preview_cobertura_retorna_pontos_sem_pii(client):
    response = client.post(
        "/relatorios/pesquisas/1000/mapas/preview/",
        json={"tipo_mapa": "COBERTURA"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["pontos"]) == 6
    assert payload["pontos"][0] == {"lat": 0.2, "lng": 0.2, "peso": 1}
    assert "agente_id" not in payload["pontos"][0]
    assert "client_uuid" not in payload["pontos"][0]


def test_preview_respeita_tenant_do_usuario(database):
    _engine, Session = database
    app = FastAPI()
    app.include_router(relatorios.router)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user(company_id=20)
    client = TestClient(app)

    response = client.post(
        "/relatorios/pesquisas/1000/mapas/preview/",
        json={"tipo_mapa": "COBERTURA"},
    )

    assert response.status_code == 404
