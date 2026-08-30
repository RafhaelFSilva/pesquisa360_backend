import json
import os
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from shapely import wkt as shapely_wkt
from shapely.geometry import mapping
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SECRET_KEY", "test-only-multidimensional-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360 import crud, schemas
from pesquisa360.api.endpoints import relatorios
from pesquisa360.core.dependencies import get_current_user, get_db
from tests.acl_fixture import criar_tabelas_acl


@pytest.fixture
def context():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def sqlite_functions(connection, _):
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
        connection.create_function("ST_X", 1, lambda value: geom(value).x if value else None)
        connection.create_function("ST_Y", 1, lambda value: geom(value).y if value else None)
        connection.create_function(
            "ST_Covers", 2,
            lambda polygon, point: int(geom(polygon).covers(geom(point))) if polygon and point else 0,
        )

    # ACL multiempresa (ADR-024): a expressao de autorizacao consulta as
    # tabelas de acesso em toda leitura; sem linhas vale o fallback da empresa.
    criar_tabelas_acl(engine)
    Session = sessionmaker(bind=engine)
    with engine.begin() as connection:
        for statement in (
            # RBAC (ADR-037): a permissao e resolvida pelo NOME do perfil do usuario.
            "CREATE TABLE perfis (id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT)",
            "CREATE TABLE usuarios (id INTEGER PRIMARY KEY, email TEXT, nome TEXT, senha_hash TEXT, ativo BOOLEAN, perfil_id INTEGER, company_id INTEGER)",
            "CREATE TABLE projetos (id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT, status TEXT, data_inicio DATE, data_fim DATE, coordenador_id INTEGER, company_id INTEGER)",
            "CREATE TABLE pesquisas (id INTEGER PRIMARY KEY, titulo TEXT, tipo_pesquisa TEXT, ativo BOOLEAN, projeto_id INTEGER, cerca_eletronica TEXT, tolerancia_metros INTEGER)",
            "CREATE TABLE perguntas (id INTEGER PRIMARY KEY, texto_pergunta TEXT, tipo_pergunta TEXT, ordem INTEGER, eh_obrigatoria BOOLEAN, eh_resposta_espontanea BOOLEAN, papel_analitico VARCHAR(50), metadados_analiticos JSON NOT NULL DEFAULT '{}', ativo BOOLEAN, pesquisa_id INTEGER, aplicabilidade VARCHAR(20) NOT NULL DEFAULT 'GLOBAL')",
            "CREATE TABLE opcoes (id INTEGER PRIMARY KEY, texto TEXT, ordem INTEGER, pergunta_id INTEGER, proxima_pergunta_id INTEGER)",
            "CREATE TABLE coletas (id INTEGER PRIMARY KEY, pesquisa_id INTEGER, agente_id INTEGER, company_id INTEGER, client_uuid TEXT, foi_offline BOOLEAN, endereco_estimado TEXT, status_sincronizacao TEXT, data_inicio_coleta DATETIME, data_fim_coleta DATETIME, localizacao_inicio TEXT, localizacao_fim TEXT, inconformidade_localizacao BOOLEAN)",
            "CREATE TABLE respostas (id INTEGER PRIMARY KEY, pergunta_id INTEGER, coleta_id INTEGER, valor_resposta TEXT)",
            "CREATE TABLE setores (id INTEGER PRIMARY KEY, nome TEXT, meta INTEGER, tolerancia INTEGER, finalidade TEXT, geometria TEXT, pesquisa_id INTEGER, agente_id INTEGER, municipio_territorio_id INTEGER)",
            "CREATE TABLE categorias_resposta_espontanea (id INTEGER PRIMARY KEY, pesquisa_id INTEGER, nome TEXT, nome_normalizado TEXT, ativo BOOLEAN, criado_por_id INTEGER, atualizado_por_id INTEGER, criado_em DATETIME, atualizado_em DATETIME)",
            "CREATE TABLE mapeamentos_resposta_espontanea (id INTEGER PRIMARY KEY, pesquisa_id INTEGER, categoria_id INTEGER, chave_normalizada TEXT, texto_referencia TEXT, ativo BOOLEAN, criado_por_id INTEGER, atualizado_por_id INTEGER, criado_em DATETIME, atualizado_em DATETIME)",
        ):
            connection.execute(text(statement))
        # Gerente possui INTELIGENCIA_VER (rbac.MATRIZ). Usuario 2 pertence a outro
        # tenant e serve ao cenario negativo de escopo (404), nao ao de RBAC.
        connection.execute(text("INSERT INTO perfis (id, nome) VALUES (1, 'Gerente')"))
        connection.execute(text("INSERT INTO usuarios (id, email, nome, senha_hash, ativo, perfil_id, company_id) VALUES (1, 'gerente@a', 'Gerente A', 'x', 1, 1, 10), (2, 'gerente@b', 'Gerente B', 'x', 1, 1, 20)"))
        connection.execute(text("INSERT INTO projetos VALUES (1, 'Projeto A', NULL, 'Ativo', NULL, NULL, 1, 10), (2, 'Projeto B', NULL, 'Ativo', NULL, NULL, 2, 20)"))
        connection.execute(text("INSERT INTO pesquisas VALUES (1, 'Pesquisa A', NULL, 1, 1, NULL, NULL), (2, 'Pesquisa B', NULL, 1, 2, NULL, NULL)"))
        connection.execute(text("""
            INSERT INTO perguntas
                (id, texto_pergunta, tipo_pergunta, ordem, eh_obrigatoria,
                 eh_resposta_espontanea, papel_analitico, metadados_analiticos, ativo, pesquisa_id)
            VALUES
                (11, 'Voto', 'ESCOLHA_SIMPLES', 1, 1, 0, 'INTENCAO_VOTO', '{"schema_version": 1}', 1, 1),
                (12, 'Sexo', 'MultiplaEscolha_Unica', 2, 1, 0, 'PERFIL', '{}', 1, 1),
                (13, 'Temas', 'MultiplaEscolha_Multipla', 3, 0, 0, NULL, '{}', 1, 1),
                (14, 'Texto', 'TEXTO', 4, 0, 0, NULL, '{}', 1, 1),
                (15, 'Inativa', 'ESCOLHA_SIMPLES', 5, 0, 0, NULL, '{}', 0, 1),
                (16, 'Regiao', 'ESCOLHA_SIMPLES', 6, 0, 0, 'TERRITORIO', '{}', 1, 1),
                (17, 'Perfil 2', 'ESCOLHA_SIMPLES', 7, 0, 0, 'PERFIL', '{}', 1, 1),
                (18, 'Opiniao', 'TEXTO', 8, 0, 1, NULL, '{}', 1, 1),
                (21, 'Outra empresa', 'ESCOLHA_SIMPLES', 1, 1, 0, NULL, '{}', 1, 2)
        """))
        connection.execute(text("INSERT INTO opcoes VALUES (1, 'B', 2, 11, NULL), (2, 'A', 1, 11, NULL), (3, 'F', 1, 12, NULL), (4, 'M', 2, 12, NULL)"))
        connection.execute(text("INSERT INTO categorias_resposta_espontanea (id, pesquisa_id, nome, nome_normalizado, ativo, criado_por_id, atualizado_por_id) VALUES (1, 1, 'Positiva', 'positiva', 1, 1, 1), (2, 1, 'Negativa', 'negativa', 1, 1, 1)"))
        connection.execute(text("INSERT INTO mapeamentos_resposta_espontanea (id, pesquisa_id, categoria_id, chave_normalizada, texto_referencia, ativo, criado_por_id, atualizado_por_id) VALUES (1, 1, 1, 'gostei', 'Gostei', 1, 1, 1)"))
        connection.execute(text("""
            INSERT INTO coletas
                (id, pesquisa_id, agente_id, company_id, client_uuid, status_sincronizacao,
                 inconformidade_localizacao)
            VALUES (101, 1, 1, 10, 'a', 'sincronizado', 0),
                   (102, 1, 1, 10, 'b', 'sincronizado', 0),
                   (103, 1, 1, 10, 'c', 'sincronizado', 0),
                   (201, 2, 2, 20, 'd', 'sincronizado', 0)
        """))
        answers = [
            (1, 11, 101, "A"), (2, 12, 101, "F"), (3, 13, 101, json.dumps(["Saude", "Educacao", "Saude"])),
            (4, 11, 102, "A"), (5, 12, 102, "M"), (6, 13, 102, json.dumps(["Saude"])),
            (7, 11, 103, "B"), (8, 13, 103, json.dumps(["Educacao"])),
            (9, 13, 101, json.dumps(["Saude", "Educacao"])),
            (10, 21, 201, "X"),
            (11, 18, 101, "Gostei"), (12, 18, 102, "Sem classificação"),
        ]
        connection.execute(
            text("INSERT INTO respostas (id, pergunta_id, coleta_id, valor_resposta) VALUES (:id, :qid, :cid, :value)"),
            [{"id": i, "qid": qid, "cid": cid, "value": value} for i, qid, cid, value in answers],
        )

    current_user = SimpleNamespace(id=1, company_id=10, ativo=True, perfil_id=1)
    app = FastAPI()
    app.include_router(relatorios.router)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: current_user
    yield SimpleNamespace(
        client=TestClient(app), Session=Session, user=current_user, app=app
    )
    engine.dispose()


def post(context, payload, pesquisa_id=1):
    return context.client.post(
        f"/relatorios/pesquisas/{pesquisa_id}/cruzamentos-multidimensionais/",
        json=payload,
    )


def test_question_metadata_schemas_are_backward_compatible_and_strict():
    legacy = schemas.PerguntaCreate(
        texto_pergunta="Legada", tipo_pergunta="escolha_simples", ordem=1
    )
    assert legacy.papel_analitico is None
    assert legacy.metadados_analiticos == {}
    enriched = schemas.PerguntaCreate(
        texto_pergunta="Voto",
        tipo_pergunta="escolha_simples",
        ordem=1,
        papel_analitico="intencao_voto",
        metadados_analiticos={"schema_version": 1, "futuro": {"livre": True}},
    )
    assert enriched.papel_analitico.value == "INTENCAO_VOTO"
    assert enriched.metadados_analiticos["futuro"] == {"livre": True}
    for invalid in ([], "x", 1, True, None):
        with pytest.raises(ValidationError):
            schemas.PerguntaCreate(
                texto_pergunta="X",
                tipo_pergunta="ESCOLHA_SIMPLES",
                ordem=1,
                metadados_analiticos=invalid,
            )


def test_question_metadata_create_update_and_clear(context):
    with context.Session() as db:
        created = crud.create_pergunta(
            db,
            schemas.PerguntaCreate(
                texto_pergunta="Nova",
                tipo_pergunta="ESCOLHA_SIMPLES",
                papel_analitico="PERFIL",
                metadados_analiticos={"dimensao": "PERFIL"},
            ),
            pesquisa_id=1,
        )
        assert created.papel_analitico == "PERFIL"
        assert created.metadados_analiticos == {"dimensao": "PERFIL"}
        updated = crud.update_pergunta(
            db, 1, created.id, schemas.PerguntaUpdate(metadados_analiticos={})
        )
        assert updated.papel_analitico == "PERFIL"
        assert updated.metadados_analiticos == {}


def test_two_dimensions_count_distinct_interviews_and_parent_bases(context):
    response = post(context, {"pergunta_ids": [11, 12]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_entrevistas"] == 3
    assert body["base_valida"] == 2
    assert [item["caminho"][0]["valor_chave"] for item in body["nodos"] if item["nivel"] == 1] == ["A"]
    leaves = [item for item in body["nodos"] if item["nivel"] == 2]
    assert {(item["caminho"][-1]["valor_chave"], item["contagem_entrevistas"]) for item in leaves} == {("F", 1), ("M", 1)}
    assert all(item["base_pai"] == 2 and item["percentual_pai"] == 50.0 for item in leaves)


def test_three_dimensions_multiple_choice_and_no_cartesian_zeroes(context):
    response = post(context, {"pergunta_ids": [11, 12, 13]})
    assert response.status_code == 200
    body = response.json()
    leaves = [item for item in body["nodos"] if item["nivel"] == 3]
    paths = {tuple(value["valor_chave"] for value in item["caminho"]) for item in leaves}
    assert paths == {("A", "F", "Saude"), ("A", "F", "Educacao"), ("A", "M", "Saude")}
    assert body["metadados_execucao"]["possui_multipla_resposta"] is True
    assert body["metadados_execucao"]["somatorio_percentuais_pode_exceder_100"] is True
    assert any("deduplicados" in warning for warning in body["avisos"])


def test_depth_and_question_order_define_hierarchy(context):
    body = post(
        context,
        {"pergunta_ids": [12, 11, 13], "profundidade_maxima": 2},
    ).json()
    assert len(body["dimensoes"]) == 2
    assert [item["pergunta_id"] for item in body["dimensoes"]] == [12, 11]
    assert max(item["nivel"] for item in body["nodos"]) == 2
    assert {item["caminho"][0]["valor_chave"] for item in body["nodos"] if item["nivel"] == 1} == {"F", "M"}


def test_five_dimensions_are_processed_without_materializing_zero_paths(context):
    response = post(
        context,
        {
            "pergunta_ids": [11, 12, 13, 16, 17],
            "incluir_sem_resposta": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["dimensoes"]) == 5
    assert max(node["nivel"] for node in body["nodos"]) == 5
    assert all(node["contagem_entrevistas"] > 0 for node in body["nodos"])


def test_include_missing_answer_creates_technical_category(context):
    body = post(
        context, {"pergunta_ids": [11, 12], "incluir_sem_resposta": True}
    ).json()
    assert body["base_valida"] == 3
    missing = [
        item for item in body["nodos"]
        if item["caminho"][-1]["valor_chave"] == "__SEM_RESPOSTA__"
    ]
    assert len(missing) == 1
    assert missing[0]["caminho"][-1]["rotulo"] == "Sem resposta"


def test_options_endpoint_returns_ordered_values_counts_and_missing(context):
    response = context.client.get(
        "/relatorios/pesquisas/1/cruzamentos-multidimensionais/opcoes/"
    )
    assert response.status_code == 200, response.text
    dimensions = {item["pergunta_id"]: item for item in response.json()["dimensoes"]}
    assert [item["valor_chave"] for item in dimensions[11]["valores"]] == ["A", "B", "__SEM_RESPOSTA__"]
    missing = dimensions[12]["valores"][-1]
    assert missing == {
        "valor_chave": "__SEM_RESPOSTA__",
        "rotulo": "Sem resposta",
        "ordem": None,
        "contagem_entrevistas": 1,
        "origem": "SEM_RESPOSTA",
    }
    assert [item["valor_chave"] for item in dimensions[18]["valores"]] == [
        "Negativa", "Positiva", crud.NAO_CATEGORIZADA, "__SEM_RESPOSTA__"
    ]


def test_response_filter_keeps_original_parent_and_total_bases(context):
    body = post(context, {
        "pergunta_ids": [11, 12],
        "incluir_sem_resposta": True,
        "filtros_respostas": [{"pergunta_id": 12, "valores": ["F"]}],
    }).json()
    leaves = [node for node in body["nodos"] if node["nivel"] == 2]
    assert len(leaves) == 1
    assert leaves[0]["caminho"][-1]["valor_chave"] == "F"
    assert leaves[0]["base_pai"] == 2
    assert leaves[0]["percentual_pai"] == 50.0
    assert leaves[0]["percentual_total"] == 33.33


def test_response_filter_does_not_renormalize_100_interview_denominator(context):
    with context.Session() as db:
        db.execute(text("DELETE FROM respostas WHERE coleta_id IN (101, 102, 103)"))
        db.execute(text("DELETE FROM coletas WHERE pesquisa_id = 1"))
        for index in range(100):
            collection_id = 1000 + index
            value = "A" if index < 40 else "B" if index < 70 else "C"
            db.execute(text("INSERT INTO coletas (id, pesquisa_id, agente_id, company_id, client_uuid, status_sincronizacao, inconformidade_localizacao) VALUES (:id, 1, 1, 10, :uuid, 'sincronizado', 0)"), {"id": collection_id, "uuid": str(collection_id)})
            db.execute(text("INSERT INTO respostas (id, pergunta_id, coleta_id, valor_resposta) VALUES (:id1, 11, :cid, :value), (:id2, 12, :cid, 'Todos')"), {"id1": 2000 + index * 2, "id2": 2001 + index * 2, "cid": collection_id, "value": value})
        db.commit()

    body = post(context, {
        "pergunta_ids": [11, 12],
        "incluir_sem_resposta": True,
        "filtros_respostas": [{"pergunta_id": 11, "valores": ["A", "B"]}],
    }).json()
    roots = {node["caminho"][0]["valor_chave"]: node for node in body["nodos"] if node["nivel"] == 1}
    assert body["base_valida"] == 100
    assert roots["A"]["percentual_total"] == 40.0
    assert roots["B"]["percentual_total"] == 30.0


def test_spontaneous_cross_uses_active_report_categorization(context):
    body = post(context, {
        "pergunta_ids": [11, 18],
        "incluir_sem_resposta": True,
    }).json()
    values = {node["caminho"][-1]["valor_chave"] for node in body["nodos"] if node["nivel"] == 2}
    assert values == {"Positiva", crud.NAO_CATEGORIZADA, "__SEM_RESPOSTA__"}


@pytest.mark.parametrize("filters", [
    [{"pergunta_id": 12, "valores": []}],
    [{"pergunta_id": 12, "valores": ["F"]}, {"pergunta_id": 12, "valores": ["M"]}],
    [{"pergunta_id": 999, "valores": ["F"]}],
])
def test_invalid_response_filters_return_422(context, filters):
    assert post(context, {"pergunta_ids": [11, 12], "filtros_respostas": filters}).status_code == 422


def test_unknown_filter_value_returns_422(context):
    response = post(context, {
        "pergunta_ids": [11, 12],
        "filtros_respostas": [{"pergunta_id": 12, "valores": ["inexistente"]}],
    })
    assert response.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"pergunta_ids": []},
        {"pergunta_ids": [11]},
        {"pergunta_ids": [11, 11]},
        {"pergunta_ids": [11, 12], "profundidade_maxima": 0},
        {"pergunta_ids": [11, 12], "profundidade_maxima": 3},
        {"pergunta_ids": [11, 12], "company_id": 10},
    ],
)
def test_invalid_request_contract_returns_422(context, payload):
    assert post(context, payload).status_code == 422


@pytest.mark.parametrize("question_ids", [[11, 14], [11, 15], [11, 21], [11, 999]])
def test_ineligible_or_foreign_questions_return_422(context, question_ids):
    response = post(context, {"pergunta_ids": question_ids})
    assert response.status_code == 422


def test_other_tenant_receives_404_without_question_disclosure(context):
    context.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=2, company_id=20, ativo=True, perfil_id=1
    )
    response = post(context, {"pergunta_ids": [11, 12]}, pesquisa_id=1)
    assert response.status_code == 404


def test_empty_valid_research_returns_200(context):
    with context.Session() as db:
        db.execute(text("DELETE FROM respostas"))
        db.execute(text("DELETE FROM coletas WHERE pesquisa_id = 1"))
        db.commit()
    body = post(context, {"pergunta_ids": [11, 12]}).json()
    assert body["total_entrevistas"] == 0
    assert body["base_valida"] == 0
    assert body["nodos"] == []
    assert body["avisos"]


def test_legacy_crosstab_contract_stays_available(context):
    response = context.client.post(
        "/relatorios/pesquisas/1/crosstab/",
        json={"pergunta_linha_id": 11, "pergunta_coluna_id": 12},
    )
    assert response.status_code == 200
    assert set(response.json()) == {"pergunta_linha", "pergunta_coluna", "dados"}

    legacy_counts = {
        (row["valor_linha"], cell["valor_coluna"]): cell["contagem"]
        for row in response.json()["dados"]
        for cell in row["celulas"]
    }
    multidimensional = post(context, {"pergunta_ids": [11, 12]}).json()
    new_counts = {
        tuple(value["valor_chave"] for value in node["caminho"]): node["contagem_entrevistas"]
        for node in multidimensional["nodos"]
        if node["nivel"] == 2
    }
    assert new_counts == legacy_counts


def test_node_limit_returns_controlled_422(context, monkeypatch):
    monkeypatch.setattr(
        "pesquisa360.services.multidimensional_cross.MAX_CROSS_NODES", 1
    )
    response = post(context, {"pergunta_ids": [11, 12]})
    assert response.status_code == 422
    assert response.json()["detail"]["limite"] == 1


# --- Pergunta-alvo: X versus Nao-X -------------------------------------------

def seed_target_base(context, distribuicao, sexo_por_indice=None):
    """Recria a pesquisa 1 com uma base controlada: pergunta 12 = Sexo, pergunta 11 = Voto."""
    with context.Session() as db:
        db.execute(text("DELETE FROM respostas WHERE coleta_id IN (SELECT id FROM coletas WHERE pesquisa_id = 1)"))
        db.execute(text("DELETE FROM coletas WHERE pesquisa_id = 1"))
        index = 0
        answer_id = 5000
        for valor, quantidade in distribuicao:
            for _ in range(quantidade):
                collection_id = 3000 + index
                sexo = sexo_por_indice(index) if sexo_por_indice else "Todos"
                db.execute(
                    text("INSERT INTO coletas (id, pesquisa_id, agente_id, company_id, client_uuid, status_sincronizacao, inconformidade_localizacao) VALUES (:id, 1, 1, 10, :uuid, 'sincronizado', 0)"),
                    {"id": collection_id, "uuid": str(collection_id)},
                )
                db.execute(
                    text("INSERT INTO respostas (id, pergunta_id, coleta_id, valor_resposta) VALUES (:id1, 11, :cid, :voto), (:id2, 12, :cid, :sexo)"),
                    {"id1": answer_id, "id2": answer_id + 1, "cid": collection_id, "voto": valor, "sexo": sexo},
                )
                answer_id += 2
                index += 1
        db.commit()


BASE_SIMPLES = [("A", 40), ("B", 30), ("C", 20), ("NS/NR", 10)]


def test_target_absent_or_full_distribution_keeps_current_behavior(context):
    # Sem alvo: ordem legada de pergunta_ids (11 na raiz).
    sem_alvo = post(context, {"pergunta_ids": [11, 12]}).json()
    # Com alvo 11 na raiz e 12 como aprofundamento: mesma arvore.
    completo = post(context, {
        "pergunta_ids": [11, 12],
        "alvo": {"pergunta_id": 11, "modo": "FULL_DISTRIBUTION"},
        "dimensoes": [{"tipo": "PERGUNTA", "pergunta_id": 12}],
    }).json()

    assert sem_alvo["nodos"] == completo["nodos"]
    assert sem_alvo["base_valida"] == completo["base_valida"]
    assert sem_alvo["dimensoes"] == completo["dimensoes"]
    assert sem_alvo["metadados_execucao"]["alvo"] is None
    assert completo["metadados_execucao"]["alvo"] == {
        "pergunta_id": 11,
        "modo": "FULL_DISTRIBUTION",
        "valor": None,
        "valores_excluidos": [],
        "valores_preservados": [],
    }


def test_one_vs_rest_reduces_target_to_x_and_not_x(context):
    seed_target_base(context, BASE_SIMPLES)
    body = post(context, {
        "pergunta_ids": [12, 11],
        "incluir_sem_resposta": True,
        "alvo": {"pergunta_id": 11, "modo": "ONE_VS_REST", "valor": "A"},
        "dimensoes": [{"tipo": "PERGUNTA", "pergunta_id": 12}],
    }).json()

    assert body["base_valida"] == 100
    # O alvo e a raiz: X e Nao-X ficam no nivel 1.
    raizes = [node for node in body["nodos"] if node["nivel"] == 1]
    assert [(node["caminho"][-1]["valor_chave"], node["contagem_entrevistas"], node["percentual_pai"]) for node in raizes] == [
        ("A", 40, 40.0),
        ("__NAO_X__", 60, 60.0),
    ]
    assert [node["caminho"][-1]["rotulo"] for node in raizes] == ["A", "Nao-X"]
    # A dimensao-alvo passa a ter cardinalidade 2.
    assert body["dimensoes"][0]["cardinalidade_observada"] == 2


def test_one_vs_rest_excluded_values_leave_the_denominator(context):
    seed_target_base(context, BASE_SIMPLES)
    body = post(context, {
        "pergunta_ids": [12, 11],
        "incluir_sem_resposta": True,
        "alvo": {
            "pergunta_id": 11,
            "modo": "ONE_VS_REST",
            "valor": "A",
            "valores_excluidos": ["NS/NR"],
        },
        "dimensoes": [{"tipo": "PERGUNTA", "pergunta_id": 12}],
    }).json()

    # 100 - 10 excluidos: as respostas excluidas saem do universo da comparacao.
    assert body["base_valida"] == 90
    folhas = {node["caminho"][-1]["valor_chave"]: node for node in body["nodos"] if node["nivel"] == 1}
    assert folhas["A"]["contagem_entrevistas"] == 40
    assert folhas["A"]["percentual_pai"] == 44.44
    assert folhas["__NAO_X__"]["contagem_entrevistas"] == 50
    assert folhas["__NAO_X__"]["percentual_pai"] == 55.56


def test_one_vs_rest_applies_after_dimension_filter(context):
    seed_target_base(
        context,
        [("A", 40), ("B", 50), ("NS/NR", 10)],
        sexo_por_indice=lambda index: "M" if index < 60 else "F",
    )
    body = post(context, {
        "pergunta_ids": [12, 11],
        "incluir_sem_resposta": True,
        "filtros_respostas": [{"pergunta_id": 12, "valores": ["M"]}],
        "alvo": {"pergunta_id": 11, "modo": "ONE_VS_REST", "valor": "A"},
        "dimensoes": [{"tipo": "PERGUNTA", "pergunta_id": 12}],
    }).json()

    # Alvo na raiz, Sexo (filtrado em M) no nivel 2.
    raizes = {node["caminho"][0]["valor_chave"]: node for node in body["nodos"] if node["nivel"] == 1}
    assert set(raizes) == {"A", "__NAO_X__"}
    assert raizes["A"]["contagem_entrevistas"] == 40

    folhas = [node for node in body["nodos"] if node["nivel"] == 2]
    assert {node["caminho"][-1]["valor_chave"] for node in folhas} == {"M"}
    # O recorte por Sexo=M restringe os ramos exibidos sob cada classe do alvo.
    assert sum(node["contagem_entrevistas"] for node in folhas) == 60


def test_one_vs_rest_uses_reportable_spontaneous_categories(context):
    body = post(context, {
        "pergunta_ids": [11, 18],
        "incluir_sem_resposta": True,
        "alvo": {"pergunta_id": 18, "modo": "ONE_VS_REST", "valor": "Positiva"},
        "dimensoes": [{"tipo": "PERGUNTA", "pergunta_id": 11}],
    }).json()

    raizes = [node for node in body["nodos"] if node["nivel"] == 1]
    chaves = {node["caminho"][0]["valor_chave"] for node in raizes}
    assert chaves == {"Positiva", "__NAO_X__"}
    # 'Nao categorizada' e 'Sem resposta' continuam obedecendo as regras atuais e caem em Nao-X.
    nao_x = [node for node in raizes if node["caminho"][0]["valor_chave"] == "__NAO_X__"]
    assert sum(node["contagem_entrevistas"] for node in nao_x) == 2


def test_alvo_repetido_em_dimensoes_e_rejeitado(context):
    # O alvo ocupa a raiz: nao pode reaparecer entre as dimensoes de aprofundamento.
    response = post(context, {
        "pergunta_ids": [11, 12],
        "alvo": {"pergunta_id": 11, "modo": "ONE_VS_REST", "valor": "A"},
        "dimensoes": [{"tipo": "PERGUNTA", "pergunta_id": 12},
                      {"tipo": "PERGUNTA", "pergunta_id": 11}],
    })
    assert response.status_code == 422


def test_filtro_sobre_a_propria_dimensao_alvo_e_rejeitado(context):
    response = post(context, {
        "pergunta_ids": [11, 12],
        "filtros_respostas": [{"pergunta_id": 12, "valores": ["F"]}],
        "alvo": {"pergunta_id": 12, "modo": "ONE_VS_REST", "valor": "F"},
        "dimensoes": [{"tipo": "PERGUNTA", "pergunta_id": 11}],
    })
    assert response.status_code == 422
    assert "filtros_respostas" in response.json()["detail"]


@pytest.mark.parametrize("alvo", [
    {"pergunta_id": 12, "modo": "ONE_VS_REST"},
    {"pergunta_id": 12, "modo": "ONE_VS_REST", "valor": "F", "valores_excluidos": ["F"]},
    {"pergunta_id": 12, "modo": "FULL_DISTRIBUTION", "valor": "F"},
    {"pergunta_id": 99, "modo": "FULL_DISTRIBUTION"},
])
def test_invalid_target_contract_returns_422(context, alvo):
    assert post(context, {"pergunta_ids": [11, 12], "alvo": alvo}).status_code == 422
# --- Tratamentos das demais respostas: Nao-X / preservado / excluido ---------

BASE_ELEITORAL = [("X", 80), ("Y", 70), ("Z", 50), ("Branco/Nulo", 20), ("NS/NR", 30), ("Indeciso", 25)]


def folhas_por_chave(body):
    return {node["caminho"][-1]["valor_chave"]: node for node in body["nodos"] if node["nivel"] == 2}


def test_valor_preservado_mantem_categoria_propria_sem_mudar_base(context):
    seed_target_base(context, BASE_SIMPLES)
    body = post(context, {
        "pergunta_ids": [12, 11],
        "incluir_sem_resposta": True,
        "alvo": {
            "pergunta_id": 11,
            "modo": "ONE_VS_REST",
            "valor": "A",
            "valores_preservados": ["NS/NR"],
        },
        "dimensoes": [{"tipo": "PERGUNTA", "pergunta_id": 12}],
    }).json()

    assert body["base_valida"] == 100
    folhas = {node["caminho"][0]["valor_chave"]: node for node in body["nodos"] if node["nivel"] == 1}
    assert set(folhas) == {"A", "__NAO_X__", "NS/NR"}
    assert (folhas["A"]["contagem_entrevistas"], folhas["A"]["percentual_pai"]) == (40, 40.0)
    assert (folhas["__NAO_X__"]["contagem_entrevistas"], folhas["__NAO_X__"]["percentual_pai"]) == (50, 50.0)
    # A categoria preservada conserva o valor original, inclusive no rotulo.
    assert (folhas["NS/NR"]["contagem_entrevistas"], folhas["NS/NR"]["percentual_pai"]) == (10, 10.0)
    assert folhas["NS/NR"]["caminho"][-1]["rotulo"] == "NS/NR"
    # Ordem da leitura: X, Nao-X e depois as preservadas.
    assert [node["caminho"][0]["valor_chave"] for node in body["nodos"] if node["nivel"] == 1] == [
        "A", "__NAO_X__", "NS/NR",
    ]


def test_preservado_e_excluido_combinados_seguem_a_regra_da_leitura(context):
    seed_target_base(context, BASE_ELEITORAL)
    body = post(context, {
        "pergunta_ids": [12, 11],
        "incluir_sem_resposta": True,
        "alvo": {
            "pergunta_id": 11,
            "modo": "ONE_VS_REST",
            "valor": "X",
            "valores_excluidos": ["NS/NR"],
            "valores_preservados": ["Branco/Nulo", "Indeciso"],
        },
        "dimensoes": [{"tipo": "PERGUNTA", "pergunta_id": 12}],
    }).json()

    # 275 entrevistas, 30 excluidas: preservadas continuam na base, excluidas saem.
    assert body["total_entrevistas"] == 275
    assert body["base_valida"] == 245
    folhas = {node["caminho"][0]["valor_chave"]: node for node in body["nodos"] if node["nivel"] == 1}
    assert set(folhas) == {"X", "__NAO_X__", "Branco/Nulo", "Indeciso"}
    assert folhas["X"]["contagem_entrevistas"] == 80
    assert folhas["__NAO_X__"]["contagem_entrevistas"] == 120
    assert folhas["Branco/Nulo"]["contagem_entrevistas"] == 20
    assert folhas["Indeciso"]["contagem_entrevistas"] == 25
    assert sum(node["contagem_entrevistas"] for node in folhas.values()) == 245


def test_configuracao_antiga_sem_valores_preservados_permanece_identica(context):
    seed_target_base(context, BASE_SIMPLES)
    antiga = post(context, {
        "pergunta_ids": [12, 11],
        "incluir_sem_resposta": True,
        "alvo": {"pergunta_id": 11, "modo": "ONE_VS_REST", "valor": "A", "valores_excluidos": ["NS/NR"]},
    }).json()
    explicita = post(context, {
        "pergunta_ids": [12, 11],
        "incluir_sem_resposta": True,
        "alvo": {
            "pergunta_id": 11,
            "modo": "ONE_VS_REST",
            "valor": "A",
            "valores_excluidos": ["NS/NR"],
            "valores_preservados": [],
        },
    }).json()

    assert antiga["nodos"] == explicita["nodos"]
    assert antiga["base_valida"] == explicita["base_valida"] == 90
    assert antiga["metadados_execucao"]["alvo"]["valores_preservados"] == []


def test_espontanea_aceita_categoria_preservada(context):
    body = post(context, {
        "pergunta_ids": [11, 18],
        "incluir_sem_resposta": True,
        "alvo": {
            "pergunta_id": 18,
            "modo": "ONE_VS_REST",
            "valor": "Positiva",
            "valores_preservados": [crud.NAO_CATEGORIZADA],
        },
        "dimensoes": [{"tipo": "PERGUNTA", "pergunta_id": 11}],
    }).json()

    chaves = {node["caminho"][0]["valor_chave"] for node in body["nodos"] if node["nivel"] == 1}
    # A categoria tecnica de nao mapeados deixa de cair em Nao-X e vira categoria propria.
    assert crud.NAO_CATEGORIZADA in chaves
    assert chaves == {"Positiva", crud.NAO_CATEGORIZADA, "__NAO_X__"}


def test_multipla_resposta_mantem_deduplicacao_com_preservados(context):
    body = post(context, {
        "pergunta_ids": [11, 13],
        "incluir_sem_resposta": True,
        "alvo": {
            "pergunta_id": 13,
            "modo": "ONE_VS_REST",
            "valor": "Saude",
            "valores_preservados": ["Educacao"],
        },
        "dimensoes": [{"tipo": "PERGUNTA", "pergunta_id": 11}],
    }).json()

    folhas = [node for node in body["nodos"] if node["nivel"] == 2]
    caminhos = {tuple(item["valor_chave"] for item in node["caminho"]): node["contagem_entrevistas"] for node in folhas}
    # A coleta 101 marcou Saude e Educacao: conta uma vez como X, nunca tambem como preservada.
    assert caminhos == {("Saude", "A"): 2, ("Educacao", "B"): 1}


@pytest.mark.parametrize("alvo", [
    {"pergunta_id": 12, "modo": "ONE_VS_REST", "valor": "F", "valores_preservados": ["F"]},
    {"pergunta_id": 12, "modo": "ONE_VS_REST", "valor": "F", "valores_excluidos": ["M"], "valores_preservados": ["M"]},
    {"pergunta_id": 12, "modo": "ONE_VS_REST", "valor": "F", "valores_preservados": ["M", "M"]},
    {"pergunta_id": 12, "modo": "ONE_VS_REST", "valor": "F", "valores_preservados": [" "]},
])
def test_tratamentos_conflitantes_retornam_422(context, alvo):
    assert post(context, {"pergunta_ids": [11, 12], "alvo": alvo}).status_code == 422
# --- Territorio: filtro e dimensao ------------------------------------------

def ponto(lon, lat):
    return f"SRID=4326;POINT({lon} {lat})"


def quadrado(nome, x0, x1):
    return f"SRID=4326;POLYGON(({x0} 0, {x1} 0, {x1} 10, {x0} 10, {x0} 0))"


def seed_territorio(context, sobreposto=False):
    """Centro em x=[0,10], Norte em x=[10,20]; opcionalmente um setor sobreposto."""
    with context.Session() as db:
        db.execute(text("DELETE FROM respostas"))
        db.execute(text("DELETE FROM coletas WHERE pesquisa_id = 1"))
        db.execute(text("DELETE FROM setores"))
        db.execute(
            text("INSERT INTO setores (id, nome, meta, tolerancia, finalidade, geometria, pesquisa_id, agente_id) VALUES "
                 "(1, 'Centro', 0, 0, 'RELATORIO', :centro, 1, NULL),"
                 "(2, 'Norte', 0, 0, 'AMBOS', :norte, 1, NULL),"
                 "(3, 'Operacional', 0, 0, 'OPERACAO', :oper, 1, NULL),"
                 "(4, 'Outra pesquisa', 0, 0, 'RELATORIO', :outra, 2, NULL)"),
            {
                "centro": quadrado("Centro", 0, 10),
                "norte": quadrado("Norte", 10, 20),
                "oper": quadrado("Operacional", 0, 10),
                "outra": quadrado("Outra", 0, 10),
            },
        )
        if sobreposto:
            db.execute(
                text("INSERT INTO setores (id, nome, meta, tolerancia, finalidade, geometria, pesquisa_id, agente_id) "
                     "VALUES (5, 'Sobreposto', 0, 0, 'RELATORIO', :geo, 1, NULL)"),
                {"geo": quadrado("Sobreposto", 5, 15)},
            )

        # 6 no Centro, 4 no Norte, 2 fora de qualquer setor.
        distribuicao = [(5, "A", 4), (5, "B", 2), (12, "A", 3), (12, "B", 1), (50, "A", 2)]
        indice, resposta_id = 0, 9000
        for x, voto, quantidade in distribuicao:
            for _ in range(quantidade):
                coleta_id = 7000 + indice
                db.execute(
                    text("INSERT INTO coletas (id, pesquisa_id, agente_id, company_id, client_uuid, status_sincronizacao, "
                         "inconformidade_localizacao, localizacao_inicio) VALUES (:id, 1, 1, 10, :u, 'sincronizado', 0, :p)"),
                    {"id": coleta_id, "u": str(coleta_id), "p": ponto(x, 5)},
                )
                db.execute(
                    text("INSERT INTO respostas (id, pergunta_id, coleta_id, valor_resposta) VALUES (:a, 11, :c, :v), (:b, 12, :c, 'Todos')"),
                    {"a": resposta_id, "b": resposta_id + 1, "c": coleta_id, "v": voto},
                )
                resposta_id += 2
                indice += 1
        db.commit()


DIMENSAO_TERRITORIAL = {"tipo": "TERRITORIO", "nivel": "SETOR"}


def por_chave(body, nivel):
    return {node["caminho"][-1]["valor_chave"]: node for node in body["nodos"] if node["nivel"] == nivel}


def test_sem_filtro_territorial_preserva_a_base_atual(context):
    seed_territorio(context)
    body = post(context, {"pergunta_ids": [12, 11], "incluir_sem_resposta": True}).json()
    assert body["total_entrevistas"] == 12
    assert body["base_valida"] == 12
    assert body["metadados_execucao"]["filtro_territorial"] is None
    assert body["metadados_execucao"]["dimensao_territorial"] is None


def test_filtro_territorial_restringe_o_universo_antes_do_cruzamento(context):
    seed_territorio(context)
    centro = post(context, {
        "pergunta_ids": [12, 11],
        "incluir_sem_resposta": True,
        "filtro_territorial": {"nivel": "SETOR", "setor_ids": [1]},
    }).json()
    assert centro["total_entrevistas"] == 6
    assert centro["base_valida"] == 6

    uniao = post(context, {
        "pergunta_ids": [12, 11],
        "incluir_sem_resposta": True,
        "filtro_territorial": {"nivel": "SETOR", "setor_ids": [1, 2]},
    }).json()
    assert uniao["base_valida"] == 10
    assert uniao["metadados_execucao"]["filtro_territorial"]["setor_ids"] == [1, 2]

    com_sem_setor = post(context, {
        "pergunta_ids": [12, 11],
        "incluir_sem_resposta": True,
        "filtro_territorial": {"nivel": "SETOR", "setor_ids": [1], "incluir_sem_setor": True},
    }).json()
    assert com_sem_setor["base_valida"] == 8


@pytest.mark.parametrize("setor_ids", [[3], [4], [999]])
def test_setor_nao_analitico_de_outra_pesquisa_ou_inexistente_e_rejeitado(context, setor_ids):
    seed_territorio(context)
    response = post(context, {
        "pergunta_ids": [12, 11],
        "filtro_territorial": {"nivel": "SETOR", "setor_ids": setor_ids},
    })
    assert response.status_code == 404


def test_setor_de_outro_tenant_nao_e_acessivel(context):
    seed_territorio(context)
    context.user.company_id = 20
    response = post(context, {
        "pergunta_ids": [12, 11],
        "filtro_territorial": {"nivel": "SETOR", "setor_ids": [1]},
    })
    assert response.status_code == 404


def test_dimensao_territorial_como_primeira_dimensao(context):
    seed_territorio(context)
    body = post(context, {
        "pergunta_ids": [11],
        "incluir_sem_resposta": True,
        "dimensoes": [DIMENSAO_TERRITORIAL, {"tipo": "PERGUNTA", "pergunta_id": 11}],
    }).json()

    raizes = por_chave(body, 1)
    # Ordem por nome do setor; categorias tecnicas ao final.
    assert [node["caminho"][0]["valor_chave"] for node in body["nodos"] if node["nivel"] == 1] == [
        "Centro", "Norte", "__SEM_SETOR__",
    ]
    assert raizes["Centro"]["contagem_entrevistas"] == 6
    assert raizes["Norte"]["contagem_entrevistas"] == 4
    # 'Sem setor' nao desaparece nem sai do denominador.
    assert raizes["__SEM_SETOR__"]["contagem_entrevistas"] == 2
    assert raizes["__SEM_SETOR__"]["caminho"][0]["rotulo"] == "Sem setor"
    assert body["base_valida"] == 12

    territorial = body["dimensoes"][0]
    assert territorial["tipo"] == "TERRITORIO"
    assert territorial["pergunta_id"] is None
    assert territorial["nivel_territorial"] == "SETOR"
    assert territorial["texto_pergunta"] == "Setor"
    assert body["nodos"][0]["caminho"][0]["tipo"] == "TERRITORIO"


def test_dimensao_territorial_intermediaria_com_base_do_segmento(context):
    seed_territorio(context)
    body = post(context, {
        "pergunta_ids": [12, 11],
        "incluir_sem_resposta": True,
        "dimensoes": [
            {"tipo": "PERGUNTA", "pergunta_id": 12},
            DIMENSAO_TERRITORIAL,
            {"tipo": "PERGUNTA", "pergunta_id": 11},
        ],
    }).json()

    assert [dimension["posicao"] for dimension in body["dimensoes"]] == [1, 2, 3]
    assert body["dimensoes"][1]["tipo"] == "TERRITORIO"

    nivel2 = por_chave(body, 2)
    assert nivel2["Centro"]["base_pai"] == 12
    assert nivel2["Centro"]["contagem_entrevistas"] == 6
    folhas = [node for node in body["nodos"] if node["nivel"] == 3]
    centro = {node["caminho"][-1]["valor_chave"]: node for node in folhas if node["caminho"][1]["valor_chave"] == "Centro"}
    assert centro["A"]["base_pai"] == 6
    assert centro["A"]["contagem_entrevistas"] == 4
    assert centro["A"]["percentual_pai"] == 66.67


def test_filtro_e_dimensao_territorial_combinados(context):
    seed_territorio(context)
    body = post(context, {
        "pergunta_ids": [11],
        "incluir_sem_resposta": True,
        "dimensoes": [DIMENSAO_TERRITORIAL, {"tipo": "PERGUNTA", "pergunta_id": 11}],
        "filtro_territorial": {"nivel": "SETOR", "setor_ids": [1]},
    }).json()

    # A arvore compara somente os setores do recorte.
    assert [node["caminho"][0]["valor_chave"] for node in body["nodos"] if node["nivel"] == 1] == ["Centro"]
    assert body["base_valida"] == 6


def test_territorio_como_aprofundamento_sob_o_alvo_em_one_vs_rest(context):
    seed_territorio(context)
    body = post(context, {
        "pergunta_ids": [12, 11],
        "incluir_sem_resposta": True,
        "alvo": {"pergunta_id": 11, "modo": "ONE_VS_REST", "valor": "A"},
        "dimensoes": [
            {"tipo": "PERGUNTA", "pergunta_id": 12},
            DIMENSAO_TERRITORIAL,
        ],
    }).json()

    # Raiz binaria; Sexo no nivel 2; Setor no nivel 3.
    assert {node["caminho"][0]["valor_chave"] for node in body["nodos"] if node["nivel"] == 1} == {"A", "__NAO_X__"}
    assert body["dimensoes"][0]["pergunta_id"] == 11
    assert body["dimensoes"][2]["tipo"] == "TERRITORIO"

    por_classe = {}
    for node in [n for n in body["nodos"] if n["nivel"] == 3]:
        classe = node["caminho"][0]["valor_chave"]
        setor = node["caminho"][-1]["valor_chave"]
        por_classe.setdefault(classe, {}).setdefault(setor, 0)
        por_classe[classe][setor] += node["contagem_entrevistas"]
    assert por_classe["A"]["Centro"] == 4
    assert por_classe["A"]["Norte"] == 3
    assert por_classe["__NAO_X__"]["Centro"] == 2


def test_territorio_pode_ocupar_qualquer_posicao_do_aprofundamento(context):
    seed_territorio(context)
    # Territorio na primeira e na ultima posicao do aprofundamento: o alvo segue na raiz.
    for dimensoes in (
        [DIMENSAO_TERRITORIAL, {"tipo": "PERGUNTA", "pergunta_id": 12}],
        [{"tipo": "PERGUNTA", "pergunta_id": 12}, DIMENSAO_TERRITORIAL],
    ):
        body = post(context, {
            "pergunta_ids": [12, 11],
            "incluir_sem_resposta": True,
            "alvo": {"pergunta_id": 11, "modo": "ONE_VS_REST", "valor": "A"},
            "dimensoes": dimensoes,
        }).json()
        assert body["dimensoes"][0]["pergunta_id"] == 11
        assert {node["caminho"][0]["valor_chave"]
                for node in body["nodos"] if node["nivel"] == 1} == {"A", "__NAO_X__"}


def test_profundidade_maxima_e_um_mais_as_dimensoes(context):
    seed_territorio(context)
    base = {
        "pergunta_ids": [12, 11],
        "incluir_sem_resposta": True,
        "alvo": {"pergunta_id": 11, "modo": "ONE_VS_REST", "valor": "A"},
        "dimensoes": [{"tipo": "PERGUNTA", "pergunta_id": 12}, DIMENSAO_TERRITORIAL],
    }
    # 1 alvo + 2 dimensoes => profundidade maxima 3.
    assert post(context, dict(base, profundidade_maxima=4)).status_code == 422

    # Profundidade 1 ja entrega o alvo, porque ele ocupa a raiz.
    rasa = post(context, dict(base, profundidade_maxima=1)).json()
    assert rasa["metadados_execucao"]["quantidade_dimensoes_processadas"] == 1
    assert {node["caminho"][0]["valor_chave"] for node in rasa["nodos"]} == {"A", "__NAO_X__"}

    completa = post(context, dict(base, profundidade_maxima=3)).json()
    assert completa["metadados_execucao"]["quantidade_dimensoes_processadas"] == 3


def test_sobreposicao_vira_categoria_propria_sem_duplicar_entrevista(context):
    seed_territorio(context, sobreposto=True)
    body = post(context, {
        "pergunta_ids": [11],
        "incluir_sem_resposta": True,
        "dimensoes": [DIMENSAO_TERRITORIAL, {"tipo": "PERGUNTA", "pergunta_id": 11}],
    }).json()

    raizes = por_chave(body, 1)
    # O setor Sobreposto cobre x=[5,15], logo as 10 coletas de Centro e Norte passam a
    # ter duas correspondencias: nao sao duplicadas nem atribuidas arbitrariamente.
    assert set(raizes) == {"__SETOR_AMBIGUO__", "__SEM_SETOR__"}
    assert raizes["__SETOR_AMBIGUO__"]["contagem_entrevistas"] == 10
    assert raizes["__SEM_SETOR__"]["contagem_entrevistas"] == 2
    assert raizes["__SETOR_AMBIGUO__"]["caminho"][0]["rotulo"] == "Territorio sobreposto"
    assert sum(node["contagem_entrevistas"] for node in raizes.values()) == body["base_valida"] == 12


def test_opcoes_expoem_territorios_reportaveis_ordenados(context):
    seed_territorio(context)
    response = context.client.get("/relatorios/pesquisas/1/cruzamentos-multidimensionais/opcoes/")
    assert response.status_code == 200
    territorios = response.json()["territorios"]
    assert len(territorios) == 1
    assert territorios[0]["nivel"] == "SETOR"
    valores = territorios[0]["valores"]
    # Somente setores analiticos (RELATORIO/AMBOS), por nome, com Sem setor ao final.
    assert [item["valor_chave"] for item in valores] == ["Centro", "Norte", "__SEM_SETOR__"]
    assert [item["setor_id"] for item in valores] == [1, 2, None]
    assert [item["contagem_entrevistas"] for item in valores] == [6, 4, 2]


@pytest.mark.parametrize("dimensoes", [
    [{"tipo": "PERGUNTA", "pergunta_id": 12}],
    [{"tipo": "PERGUNTA", "pergunta_id": 11}, {"tipo": "PERGUNTA", "pergunta_id": 12}],
    [{"tipo": "PERGUNTA", "pergunta_id": 12}, DIMENSAO_TERRITORIAL, DIMENSAO_TERRITORIAL, {"tipo": "PERGUNTA", "pergunta_id": 11}],
    [{"tipo": "TERRITORIO", "pergunta_id": 12}],
    [{"tipo": "PERGUNTA", "nivel": "SETOR", "pergunta_id": 12}],
])
def test_sequencia_de_dimensoes_invalida_retorna_422(context, dimensoes):
    assert post(context, {"pergunta_ids": [12, 11], "dimensoes": dimensoes}).status_code == 422
def test_setor_analitico_exige_finalidade_geometria_pesquisa_e_tenant(context):
    """RELATORIO e AMBOS entram; OPERACAO, sem geometria, outra pesquisa e outro tenant nao."""
    seed_territorio(context)
    with context.Session() as db:
        db.execute(text(
            "INSERT INTO setores (id, nome, meta, tolerancia, finalidade, geometria, pesquisa_id, agente_id) "
            "VALUES (6, 'Sem geometria', 0, 0, 'RELATORIO', NULL, 1, NULL)"
        ))
        db.commit()

    resposta = context.client.get("/relatorios/pesquisas/1/cruzamentos-multidimensionais/opcoes/")
    assert resposta.status_code == 200
    valores = resposta.json()["territorios"][0]["valores"]
    nomes = {item["valor_chave"] for item in valores if item["origem"] == "SETOR"}
    # Centro = RELATORIO, Norte = AMBOS
    assert nomes == {"Centro", "Norte"}
    assert "Operacional" not in nomes       # OPERACAO nao e territorio analitico
    assert "Sem geometria" not in nomes     # geometria nula nao entra
    assert "Outra pesquisa" not in nomes    # setor de outra pesquisa nao entra

    context.user.company_id = 20
    assert context.client.get(
        "/relatorios/pesquisas/1/cruzamentos-multidimensionais/opcoes/"
    ).status_code == 404


def test_one_vs_rest_reduz_a_cardinalidade_do_alvo_para_duas_classes(context):
    """A dimensao-alvo vira binaria antes da montagem da arvore; FULL mantem a original."""
    seed_target_base(context, BASE_ELEITORAL)
    base = {"pergunta_ids": [12, 11], "incluir_sem_resposta": True, "profundidade_maxima": 2}

    aprofundamento = [{"tipo": "PERGUNTA", "pergunta_id": 12}]
    completo = post(context, dict(
        base, alvo={"pergunta_id": 11, "modo": "FULL_DISTRIBUTION"},
        dimensoes=aprofundamento)).json()
    assert completo["dimensoes"][0]["cardinalidade_observada"] == 6

    binario = post(context, dict(
        base, alvo={"pergunta_id": 11, "modo": "ONE_VS_REST", "valor": "X"},
        dimensoes=aprofundamento)).json()
    assert binario["dimensoes"][0]["cardinalidade_observada"] == 2
    # Menos classes no alvo => menos nodos na raiz.
    raizes_completo = len([n for n in completo["nodos"] if n["nivel"] == 1])
    raizes_binario = len([n for n in binario["nodos"] if n["nivel"] == 1])
    assert raizes_binario == 2 < raizes_completo


def test_limite_de_nodos_so_dispara_com_estouro_real(context, monkeypatch):
    """Cenario tipico nao encosta no teto; o 422 de limite traz detalhe estruturado."""
    seed_target_base(context, BASE_ELEITORAL)
    base = {"pergunta_ids": [12, 11], "incluir_sem_resposta": True, "profundidade_maxima": 2}
    assert post(context, base).status_code == 200

    monkeypatch.setattr(
        "pesquisa360.services.multidimensional_cross.MAX_CROSS_NODES", 1, raising=False)
    estouro = post(context, base)
    assert estouro.status_code == 422
    detalhe = estouro.json()["detail"]
    assert isinstance(detalhe, dict) and "limite" in detalhe
