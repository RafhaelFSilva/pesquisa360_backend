from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pesquisa360 import crud, schemas
from pesquisa360.api.endpoints import relatorios
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.main import app as application


@compiles(JSONB, "sqlite")
def compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


def user(user_id=1, company_id=10):
    return SimpleNamespace(id=user_id, company_id=company_id)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def register_geometry_functions(connection, _):
        connection.create_function("AsEWKB", 1, lambda value: value)

    Session = sessionmaker(bind=engine)
    statements = [
        "CREATE TABLE projetos (id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT, status TEXT, data_inicio DATE, data_fim DATE, coordenador_id INTEGER, company_id INTEGER)",
        "CREATE TABLE pesquisas (id INTEGER PRIMARY KEY, titulo TEXT, tipo_pesquisa TEXT, ativo BOOLEAN, projeto_id INTEGER, cerca_eletronica BLOB, tolerancia_metros INTEGER)",
        "CREATE TABLE perguntas (id INTEGER PRIMARY KEY, texto_pergunta TEXT, tipo_pergunta TEXT, ordem INTEGER, eh_obrigatoria BOOLEAN, eh_resposta_espontanea BOOLEAN, ativo BOOLEAN, pesquisa_id INTEGER)",
        "CREATE TABLE usuarios (id INTEGER PRIMARY KEY, email TEXT, nome TEXT, senha_hash TEXT, ativo BOOLEAN, perfil_id INTEGER, company_id INTEGER)",
        "CREATE TABLE setores (id INTEGER PRIMARY KEY, nome TEXT, meta INTEGER, tolerancia INTEGER, finalidade TEXT, geometria BLOB, pesquisa_id INTEGER, agente_id INTEGER)",
        """CREATE TABLE configuracoes_relatorio_executivo (
            id INTEGER PRIMARY KEY AUTOINCREMENT, pesquisa_id INTEGER, tipo_relatorio TEXT,
            nome TEXT, descricao TEXT, parametros_gerais JSON, ativo BOOLEAN,
            criado_por_id INTEGER, atualizado_por_id INTEGER,
            criado_em DATETIME DEFAULT CURRENT_TIMESTAMP, atualizado_em DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE secoes_relatorio_executivo (
            id INTEGER PRIMARY KEY AUTOINCREMENT, configuracao_id INTEGER, ordem INTEGER,
            tipo_secao TEXT, titulo TEXT, ativo BOOLEAN
        )""",
        """CREATE TABLE analises_relatorio_executivo (
            id INTEGER PRIMARY KEY AUTOINCREMENT, secao_id INTEGER, ordem INTEGER,
            tipo_analise TEXT, titulo_customizado TEXT, parametros JSON, ativo BOOLEAN
        )""",
    ]
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
        connection.execute(text("INSERT INTO projetos VALUES (1, 'A', NULL, 'Ativo', NULL, NULL, 1, 10), (2, 'B', NULL, 'Ativo', NULL, NULL, 2, 20), (3, 'C', NULL, 'Ativo', NULL, NULL, 1, 10)"))
        connection.execute(text("INSERT INTO pesquisas VALUES (9, 'Pesquisa A', NULL, 1, 1, NULL, NULL), (10, 'Pesquisa B', NULL, 1, 2, NULL, NULL), (11, 'Pesquisa C', NULL, 1, 3, NULL, NULL)"))
        connection.execute(text("INSERT INTO usuarios VALUES (1, 'a@a', 'A', 'x', 1, 1, 10), (2, 'b@b', 'B', 'x', 1, 1, 20)"))
        connection.execute(text("INSERT INTO perguntas VALUES (42, 'Voto', 'ESCOLHA_SIMPLES', 1, 1, 0, 1, 9), (43, 'Espontanea', 'TEXTO', 2, 0, 1, 1, 9), (44, 'Perfil', 'ESCOLHA_SIMPLES', 3, 0, 0, 1, 9), (99, 'Outra', 'ESCOLHA_SIMPLES', 1, 1, 0, 1, 10)"))
        connection.execute(text("INSERT INTO setores VALUES (7, 'Analitico', 0, 0, 'RELATORIO', NULL, 9, NULL)"))
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def create_configuration(db, tipo="MAPAS"):
    return crud.create_configuracao_relatorio_executivo(
        db,
        9,
        schemas.ConfiguracaoRelatorioExecutivoCreate(
            tipo_relatorio=tipo,
            nome="Executivo territorial",
            parametros_gerais={"setor_ids": [7], "agente_ids": [1]},
        ),
        user(),
    )


def make_client(db, current_user=None):
    app = FastAPI()
    app.include_router(relatorios.router)

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: current_user or user()
    return TestClient(app)


def test_rotas_estao_registradas_na_aplicacao(db):
    paths = {
        (route.path, method)
        for route in application.routes
        for method in (getattr(route, "methods", None) or set())
    }
    router_paths = {route.path for route in relatorios.router.routes}
    collection = "/relatorios/pesquisas/{pesquisa_id}/configuracoes-executivas/"
    assert (collection, "GET") in paths
    assert (collection, "POST") in paths
    assert any("/secoes/" in path for path, _method in paths)
    assert any("/analises/" in path for path, _method in paths)
    assert collection in router_paths


def test_get_post_cross_tenant_e_configuracao_inexistente_via_http(db):
    client = make_client(db)
    response = client.get("/relatorios/pesquisas/9/configuracoes-executivas/?tipo_relatorio=MAPAS")
    assert response.status_code == 200
    assert response.json() == []

    response = client.post(
        "/relatorios/pesquisas/9/configuracoes-executivas/",
        json={"tipo_relatorio": "MAPAS", "nome": "Configuração HTTP"},
    )
    assert response.status_code == 201
    configuration_id = response.json()["id"]

    cross_tenant = make_client(db, user(company_id=20))
    assert cross_tenant.get(
        f"/relatorios/pesquisas/9/configuracoes-executivas/{configuration_id}/"
    ).status_code == 404
    assert client.get(
        "/relatorios/pesquisas/9/configuracoes-executivas/99999/"
    ).status_code == 404


def test_crud_mapas_secoes_analises_ordem_e_soft_delete(db):
    configuration = create_configuration(db)
    configuration = crud.create_secao_relatorio_executivo(
        db, 9, configuration["id"], schemas.SecaoRelatorioExecutivoCreate(titulo="Mapas"), user()
    )
    section = configuration["secoes"][0]
    for question_id, mode in [(42, "TODAS"), (43, "ESPECIFICA")]:
        params = {"pergunta_id": question_id, "modo_resposta": mode}
        if mode == "ESPECIFICA":
            params["resposta"] = "Categoria apurada"
        configuration = crud.create_analise_relatorio_executivo(
            db,
            9,
            configuration["id"],
            section["id"],
            schemas.AnaliseRelatorioExecutivoCreate(tipo_analise="MAPA_RESULTADO_SETOR", parametros=params),
            user(),
        )

    analyses = configuration["secoes"][0]["analises"]
    assert [item["ordem"] for item in analyses] == [1, 2]
    reordered = crud.reorder_analises_relatorio_executivo(
        db,
        9,
        configuration["id"],
        section["id"],
        schemas.ReordenarRelatorioExecutivoRequest(itens=[
            {"id": analyses[0]["id"], "ordem": 2},
            {"id": analyses[1]["id"], "ordem": 1},
        ]),
        user(),
    )
    assert [item["id"] for item in reordered["secoes"][0]["analises"]] == [analyses[1]["id"], analyses[0]["id"]]

    first = reordered["secoes"][0]["analises"][0]
    updated = crud.update_analise_relatorio_executivo(
        db,
        9,
        configuration["id"],
        section["id"],
        first["id"],
        schemas.AnaliseRelatorioExecutivoUpdate(
            tipo_analise="MAPA_LIDERANCA_SETOR", parametros={"pergunta_id": 43}
        ),
        user(),
    )
    assert updated["secoes"][0]["analises"][0]["tipo_analise"] == "MAPA_LIDERANCA_SETOR"
    crud.delete_analise_relatorio_executivo(
        db, 9, configuration["id"], section["id"], first["id"], user()
    )
    assert len(crud.get_configuracao_relatorio_executivo(db, 9, configuration["id"], user())["secoes"][0]["analises"]) == 1

    crud.delete_secao_relatorio_executivo(db, 9, configuration["id"], section["id"], user())
    assert crud.get_configuracao_relatorio_executivo(db, 9, configuration["id"], user())["secoes"] == []
    crud.delete_configuracao_relatorio_executivo(db, 9, configuration["id"], user())
    with pytest.raises(HTTPException) as deleted_error:
        crud.get_configuracao_relatorio_executivo(db, 9, configuration["id"], user())
    assert deleted_error.value.status_code == 404


def test_tenant_e_pergunta_de_outra_pesquisa_sao_rejeitados(db):
    configuration = create_configuration(db)
    configuration = crud.create_secao_relatorio_executivo(
        db, 9, configuration["id"], schemas.SecaoRelatorioExecutivoCreate(), user()
    )
    with pytest.raises(HTTPException) as tenant_error:
        crud.get_configuracao_relatorio_executivo(db, 9, configuration["id"], user(company_id=20))
    assert tenant_error.value.status_code == 404

    with pytest.raises(HTTPException) as question_error:
        crud.create_analise_relatorio_executivo(
            db,
            9,
            configuration["id"],
            configuration["secoes"][0]["id"],
            schemas.AnaliseRelatorioExecutivoCreate(
                tipo_analise="MAPA_LIDERANCA_SETOR", parametros={"pergunta_id": 99}
            ),
            user(),
        )
    assert question_error.value.status_code == 404


def test_update_listagem_e_ordem_de_secoes(db):
    configuration = create_configuration(db)
    configuration = crud.update_configuracao_relatorio_executivo(
        db,
        9,
        configuration["id"],
        schemas.ConfiguracaoRelatorioExecutivoUpdate(nome="Novo nome", descricao="Descrição"),
        user(),
    )
    assert configuration["nome"] == "Novo nome"
    configuration = crud.create_secao_relatorio_executivo(
        db, 9, configuration["id"], schemas.SecaoRelatorioExecutivoCreate(titulo="Primeira"), user()
    )
    configuration = crud.create_secao_relatorio_executivo(
        db, 9, configuration["id"], schemas.SecaoRelatorioExecutivoCreate(titulo="Segunda"), user()
    )
    first, second = configuration["secoes"]
    reordered = crud.reorder_secoes_relatorio_executivo(
        db,
        9,
        configuration["id"],
        schemas.ReordenarRelatorioExecutivoRequest(itens=[
            {"id": first["id"], "ordem": 2},
            {"id": second["id"], "ordem": 1},
        ]),
        user(),
    )
    assert [item["id"] for item in reordered["secoes"]] == [second["id"], first["id"]]
    updated = crud.update_secao_relatorio_executivo(
        db,
        9,
        configuration["id"],
        second["id"],
        schemas.SecaoRelatorioExecutivoUpdate(titulo="Principal"),
        user(),
    )
    assert updated["secoes"][0]["titulo"] == "Principal"
    listed = crud.list_configuracoes_relatorio_executivo(
        db, 9, user(), schemas.TipoRelatorioExecutivo.MAPAS
    )
    assert [item["id"] for item in listed] == [configuration["id"]]


def test_validacao_parametros_e_tipo_executivo_futuro(db):
    with pytest.raises(ValueError):
        schemas.AnaliseRelatorioExecutivoCreate(
            tipo_analise="MAPA_RESULTADO_SETOR",
            parametros={"pergunta_id": 42, "modo_resposta": "ESPECIFICA"},
        )
    with pytest.raises(ValueError):
        schemas.AnaliseRelatorioExecutivoCreate(
            tipo_analise="MAPA_COMPARATIVO",
            parametros={"pergunta_a_id": 42, "pergunta_b_id": 42},
        )
    future = create_configuration(db, tipo="EXECUTIVO")
    assert future["tipo_relatorio"] == "EXECUTIVO"
    future = crud.create_secao_relatorio_executivo(
        db, 9, future["id"], schemas.SecaoRelatorioExecutivoCreate(tipo_secao="EXECUTIVO"), user()
    )
    with pytest.raises(HTTPException) as unsupported_error:
        crud.create_analise_relatorio_executivo(
            db,
            9,
            future["id"],
            future["secoes"][0]["id"],
            schemas.AnaliseRelatorioExecutivoCreate(
                tipo_analise="MAPA_COBERTURA", parametros={}
            ),
            user(),
        )
    assert unsupported_error.value.status_code == 422


def test_filtros_gerais_rejeitam_ids_fora_do_contexto(db):
    with pytest.raises(HTTPException) as sector_error:
        crud.create_configuracao_relatorio_executivo(
            db,
            9,
            schemas.ConfiguracaoRelatorioExecutivoCreate(
                tipo_relatorio="MAPAS", nome="Inválida", parametros_gerais={"setor_ids": [999]}
            ),
            user(),
        )
    assert sector_error.value.status_code == 404


def test_nome_customizado_trim_descricao_e_limites(db):
    configuration = crud.create_configuracao_relatorio_executivo(
        db,
        9,
        schemas.ConfiguracaoRelatorioExecutivoCreate(
            tipo_relatorio="MAPAS",
            nome="  Cenario Zona Norte  ",
            descricao="Versao para apresentacao",
        ),
        user(),
    )
    assert configuration["nome"] == "Cenario Zona Norte"
    assert configuration["descricao"] == "Versao para apresentacao"

    with pytest.raises(ValueError):
        schemas.ConfiguracaoRelatorioExecutivoCreate(tipo_relatorio="MAPAS", nome="   ")
    with pytest.raises(ValueError):
        schemas.ConfiguracaoRelatorioExecutivoCreate(tipo_relatorio="MAPAS", nome="x" * 121)


def test_renomear_preserva_analises_e_atualiza_descricao(db):
    configuration = create_configuration(db)
    configuration = crud.create_secao_relatorio_executivo(
        db, 9, configuration["id"], schemas.SecaoRelatorioExecutivoCreate(), user()
    )
    section_id = configuration["secoes"][0]["id"]
    configuration = crud.create_analise_relatorio_executivo(
        db,
        9,
        configuration["id"],
        section_id,
        schemas.AnaliseRelatorioExecutivoCreate(
            tipo_analise="MAPA_COBERTURA", parametros={}
        ),
        user(),
    )
    analysis_id = configuration["secoes"][0]["analises"][0]["id"]

    updated = crud.update_configuracao_relatorio_executivo(
        db,
        9,
        configuration["id"],
        schemas.ConfiguracaoRelatorioExecutivoUpdate(
            nome="  Cenario Final  ", descricao="Descricao atualizada"
        ),
        user(),
    )
    assert updated["id"] == configuration["id"]
    assert updated["nome"] == "Cenario Final"
    assert updated["descricao"] == "Descricao atualizada"
    assert updated["secoes"][0]["analises"][0]["id"] == analysis_id


def test_duplicidade_ativa_respeita_pesquisa_tipo_e_proprio_registro(db):
    configuration = create_configuration(db)
    with pytest.raises(HTTPException) as duplicate_error:
        crud.create_configuracao_relatorio_executivo(
            db,
            9,
            schemas.ConfiguracaoRelatorioExecutivoCreate(
                tipo_relatorio="MAPAS", nome="  executivo TERRITORIAL "
            ),
            user(),
        )
    assert duplicate_error.value.status_code == 409

    same = crud.update_configuracao_relatorio_executivo(
        db,
        9,
        configuration["id"],
        schemas.ConfiguracaoRelatorioExecutivoUpdate(nome=" EXECUTIVO TERRITORIAL "),
        user(),
    )
    assert same["id"] == configuration["id"]

    second = crud.create_configuracao_relatorio_executivo(
        db,
        9,
        schemas.ConfiguracaoRelatorioExecutivoCreate(
            tipo_relatorio="MAPAS", nome="Cenario secundario"
        ),
        user(),
    )
    with pytest.raises(HTTPException) as update_duplicate_error:
        crud.update_configuracao_relatorio_executivo(
            db,
            9,
            second["id"],
            schemas.ConfiguracaoRelatorioExecutivoUpdate(nome="executivo territorial"),
            user(),
        )
    assert update_duplicate_error.value.status_code == 409

    other_survey = crud.create_configuracao_relatorio_executivo(
        db,
        11,
        schemas.ConfiguracaoRelatorioExecutivoCreate(
            tipo_relatorio="MAPAS", nome="Executivo territorial"
        ),
        user(),
    )
    assert other_survey["pesquisa_id"] == 11

    other_type = crud.create_configuracao_relatorio_executivo(
        db,
        9,
        schemas.ConfiguracaoRelatorioExecutivoCreate(
            tipo_relatorio="EXECUTIVO", nome="Executivo territorial"
        ),
        user(),
    )
    assert other_type["tipo_relatorio"] == "EXECUTIVO"

    crud.delete_configuracao_relatorio_executivo(
        db, 9, configuration["id"], user()
    )
    replacement = create_configuration(db)
    assert replacement["id"] != configuration["id"]


def test_configuracao_simple_preserva_perguntas_ordem_filtros_e_crud(db):
    payload = schemas.ConfiguracaoRelatorioExecutivoCreate(
        tipo_relatorio="SIMPLE",
        nome="Relatorio eleitoral",
        parametros_gerais={
            "question_order": [43, 42],
            "question_settings": [
                {"question_id": 43, "mode": "desc"},
                {"question_id": 42, "mode": "custom", "custom_order": ["B", "A"]},
            ],
            "chart_type": "barras",
            "setor_ids": [7],
            "agente_ids": [1],
        },
    )
    created = crud.create_configuracao_relatorio_executivo(db, 9, payload, user())
    assert created["tipo_relatorio"] == "SIMPLE"
    assert created["parametros_gerais"]["question_order"] == [43, 42]
    assert created["parametros_gerais"]["question_settings"][1]["custom_order"] == ["B", "A"]

    listed = crud.list_configuracoes_relatorio_executivo(
        db, 9, user(), schemas.TipoRelatorioExecutivo.SIMPLE
    )
    assert [item["id"] for item in listed] == [created["id"]]

    updated = crud.update_configuracao_relatorio_executivo(
        db,
        9,
        created["id"],
        schemas.ConfiguracaoRelatorioExecutivoUpdate(
            nome="Relatorio eleitoral atualizado",
            parametros_gerais={
                "question_order": [42, 43],
                "question_settings": [{"question_id": 42, "mode": "form"}],
                "chart_type": "pizza",
            },
        ),
        user(),
    )
    assert updated["nome"] == "Relatorio eleitoral atualizado"
    assert updated["parametros_gerais"]["question_order"] == [42, 43]

    with pytest.raises(HTTPException) as tenant_error:
        crud.get_configuracao_relatorio_executivo(
            db, 9, created["id"], user(company_id=20)
        )
    assert tenant_error.value.status_code == 404

    crud.delete_configuracao_relatorio_executivo(db, 9, created["id"], user())
    assert crud.list_configuracoes_relatorio_executivo(
        db, 9, user(), schemas.TipoRelatorioExecutivo.SIMPLE
    ) == []


def test_configuracao_crosstab_preserva_pares_ordem_e_valida_contexto(db):
    created = crud.create_configuracao_relatorio_executivo(
        db,
        9,
        schemas.ConfiguracaoRelatorioExecutivoCreate(
            tipo_relatorio="CROSSTAB",
            nome="Perfil eleitoral",
            parametros_gerais={
                "crosses": [
                    {"row_question_id": 42, "column_question_id": 43},
                    {"row_question_id": 44, "column_question_id": 42},
                ],
                "chart_type": "bar",
            },
        ),
        user(),
    )
    assert created["parametros_gerais"]["crosses"] == [
        {"row_question_id": 42, "column_question_id": 43},
        {"row_question_id": 44, "column_question_id": 42},
    ]

    updated = crud.update_configuracao_relatorio_executivo(
        db,
        9,
        created["id"],
        schemas.ConfiguracaoRelatorioExecutivoUpdate(
            parametros_gerais={
                "crosses": [
                    {"row_question_id": 44, "column_question_id": 42},
                    {"row_question_id": 42, "column_question_id": 43},
                ],
                "chart_type": "doughnut",
            }
        ),
        user(),
    )
    assert updated["parametros_gerais"]["crosses"][0] == {
        "row_question_id": 44,
        "column_question_id": 42,
    }

    with pytest.raises(HTTPException) as tenant_error:
        crud.get_configuracao_relatorio_executivo(
            db, 9, created["id"], user(company_id=20)
        )
    assert tenant_error.value.status_code == 404

    with pytest.raises(HTTPException) as foreign_question:
        crud.create_configuracao_relatorio_executivo(
            db,
            9,
            schemas.ConfiguracaoRelatorioExecutivoCreate(
                tipo_relatorio="CROSSTAB",
                nome="Invalida",
                parametros_gerais={
                    "crosses": [{"row_question_id": 42, "column_question_id": 99}],
                    "chart_type": "pie",
                },
            ),
            user(),
        )
    assert foreign_question.value.status_code == 404

    with pytest.raises(HTTPException) as duplicate_pair:
        crud.update_configuracao_relatorio_executivo(
            db,
            9,
            created["id"],
            schemas.ConfiguracaoRelatorioExecutivoUpdate(
                parametros_gerais={
                    "crosses": [
                        {"row_question_id": 42, "column_question_id": 43},
                        {"row_question_id": 43, "column_question_id": 42},
                    ],
                    "chart_type": "doughnut",
                }
            ),
            user(),
        )
    assert duplicate_pair.value.status_code == 422
