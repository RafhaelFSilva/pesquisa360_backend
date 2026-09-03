"""Testes da API do Potencial de Crescimento (Prompt 04) — casos A01-A87.

DADOS SINTETICOS DE TESTE. A feature `potencial_crescimento` nasce INATIVA
no catalogo desta fixture (espelhando o catalogo real) e so e ativada por
helper transacional local — nunca via seed/migration produtivos.
"""

import copy
import json
import os
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SECRET_KEY", "test-only-growth-api-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.api.endpoints import potencial_crescimento as api_module
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.db import models
from tests.acl_fixture import criar_tabelas_acl

BASE = "/projetos/{projeto}/pesquisas/{pesquisa}/inteligencia-eleitoral/potencial-crescimento"


@pytest.fixture
def context():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def sqlite_functions(connection, _):
        connection.create_function("AsEWKB", 1, lambda value: value)
        connection.create_function("ST_GeomFromText", 2, lambda value, srid: value)

    criar_tabelas_acl(engine)
    Session = sessionmaker(bind=engine)
    with engine.begin() as connection:
        for statement in (
            "CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT, cnpj TEXT, logo_url TEXT, is_active BOOLEAN)",
            "CREATE TABLE perfis (id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT)",
            "CREATE TABLE usuarios (id INTEGER PRIMARY KEY, email TEXT, nome TEXT, senha_hash TEXT, ativo BOOLEAN, perfil_id INTEGER, company_id INTEGER)",
            "CREATE TABLE projetos (id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT, status TEXT, data_inicio DATE, data_fim DATE, coordenador_id INTEGER, company_id INTEGER)",
            "CREATE TABLE pesquisas (id INTEGER PRIMARY KEY, titulo TEXT, tipo_pesquisa TEXT, ativo BOOLEAN, projeto_id INTEGER, cerca_eletronica TEXT, tolerancia_metros INTEGER)",
            "CREATE TABLE perguntas (id INTEGER PRIMARY KEY, texto_pergunta TEXT, tipo_pergunta TEXT, ordem INTEGER, eh_obrigatoria BOOLEAN, eh_resposta_espontanea BOOLEAN, papel_analitico VARCHAR(50), metadados_analiticos JSON NOT NULL DEFAULT '{}', ativo BOOLEAN, pesquisa_id INTEGER, aplicabilidade VARCHAR(20) NOT NULL DEFAULT 'GLOBAL')",
            "CREATE TABLE opcoes (id INTEGER PRIMARY KEY, texto TEXT, ordem INTEGER, pergunta_id INTEGER, proxima_pergunta_id INTEGER)",
            "CREATE TABLE coletas (id INTEGER PRIMARY KEY, pesquisa_id INTEGER, agente_id INTEGER, company_id INTEGER, client_uuid TEXT, setor_id INTEGER, foi_offline BOOLEAN, endereco_estimado TEXT, status_sincronizacao TEXT, data_inicio_coleta DATETIME, data_fim_coleta DATETIME, localizacao_inicio TEXT, localizacao_fim TEXT, inconformidade_localizacao BOOLEAN)",
            "CREATE TABLE respostas (id INTEGER PRIMARY KEY, pergunta_id INTEGER, coleta_id INTEGER, valor_resposta TEXT)",
            "CREATE TABLE setores (id INTEGER PRIMARY KEY, nome TEXT, meta INTEGER, tolerancia INTEGER, finalidade TEXT, geometria TEXT, pesquisa_id INTEGER, agente_id INTEGER, municipio_territorio_id INTEGER)",
            "CREATE TABLE categorias_resposta_espontanea (id INTEGER PRIMARY KEY, pesquisa_id INTEGER, nome TEXT, nome_normalizado TEXT, ativo BOOLEAN, criado_por_id INTEGER, atualizado_por_id INTEGER, criado_em DATETIME, atualizado_em DATETIME)",
            "CREATE TABLE mapeamentos_resposta_espontanea (id INTEGER PRIMARY KEY, pesquisa_id INTEGER, categoria_id INTEGER, chave_normalizada TEXT, texto_referencia TEXT, ativo BOOLEAN, criado_por_id INTEGER, atualizado_por_id INTEGER, criado_em DATETIME, atualizado_em DATETIME)",
            "CREATE TABLE territorio_eleitoral (id INTEGER PRIMARY KEY, base_eleitoral_id INTEGER, parent_id INTEGER, tipo TEXT, codigo TEXT, nome TEXT, nome_normalizado TEXT, municipio_id INTEGER, zona_eleitoral INTEGER, numero_secao INTEGER, eleitorado_apto INTEGER, eleitorado_apto_origem TEXT, eleitorado_apto_divergente BOOLEAN, status_validacao TEXT, geometria TEXT, metadados JSON)",
            "CREATE TABLE setor_territorio_eleitoral (id INTEGER PRIMARY KEY, setor_id INTEGER, territorio_eleitoral_id INTEGER, criado_em DATETIME)",
        ):
            connection.execute(text(statement))
    # Tabelas de licenciamento pelo ORM (mesmo padrao de test_module_gates).
    models.Base.metadata.create_all(
        engine,
        tables=[
            models.Modulo.__table__,
            models.ModuloFuncionalidade.__table__,
            models.ModuloEntitlement.__table__,
            models.ModuloEntitlementFuncionalidade.__table__,
        ],
    )
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO companies (id, name, is_active) VALUES (10, 'Empresa A', 1), (20, 'Empresa B', 1)"
        ))
        connection.execute(text(
            "INSERT INTO perfis (id, nome) VALUES (1, 'Gerente'), (2, 'Agente')"
        ))
        connection.execute(text(
            "INSERT INTO usuarios (id, email, nome, senha_hash, ativo, perfil_id, company_id) VALUES "
            "(1, 'gerente@a', 'Gerente A', 'x', 1, 1, 10), "
            "(2, 'gerente@b', 'Gerente B', 'x', 1, 1, 20), "
            "(3, 'agente@a', 'Agente A', 'x', 1, 2, 10)"
        ))
        connection.execute(text(
            "INSERT INTO projetos VALUES (1, 'Projeto A1', NULL, 'Ativo', NULL, NULL, 1, 10), "
            "(2, 'Projeto B1', NULL, 'Ativo', NULL, NULL, 2, 20), "
            "(3, 'Projeto A2', NULL, 'Ativo', NULL, NULL, 1, 10)"
        ))
        connection.execute(text(
            "INSERT INTO pesquisas VALUES (1, 'Pesquisa A1', NULL, 1, 1, NULL, NULL), "
            "(2, 'Pesquisa B1', NULL, 1, 2, NULL, NULL), "
            "(3, 'Pesquisa A2', NULL, 1, 3, NULL, NULL)"
        ))
        connection.execute(text("""
            INSERT INTO perguntas
                (id, texto_pergunta, tipo_pergunta, ordem, eh_obrigatoria,
                 eh_resposta_espontanea, papel_analitico, metadados_analiticos, ativo,
                 pesquisa_id, aplicabilidade)
            VALUES
                (101, 'Intencao',      'ESCOLHA_SIMPLES',  1, 1, 0, 'INTENCAO_VOTO', '{"dimensao": "ELEITORAL"}', 1, 1, 'GLOBAL'),
                (102, 'Rejeicao',      'MULTIPLA_ESCOLHA', 2, 0, 0, 'REJEICAO',      '{}', 1, 1, 'GLOBAL'),
                (103, 'Segunda opcao', 'ESCOLHA_SIMPLES',  3, 0, 0, 'SEGUNDA_OPCAO', '{}', 1, 1, 'GLOBAL'),
                (105, 'Sexo',          'ESCOLHA_SIMPLES',  5, 0, 0, 'PERFIL',        '{"subtipo": "SEXO"}', 1, 1, 'GLOBAL'),
                (106, 'Idade',         'NUMERO',           6, 0, 0, 'PERFIL',        '{}', 1, 1, 'GLOBAL'),
                (107, 'Espontanea',    'TEXTO',            7, 0, 1, NULL,            '{}', 1, 1, 'GLOBAL'),
                (108, 'Territorial',   'ESCOLHA_SIMPLES',  8, 0, 0, NULL,            '{}', 1, 1, 'TERRITORIAL'),
                (109, 'Inativa',       'ESCOLHA_SIMPLES',  9, 0, 0, NULL,            '{}', 0, 1, 'GLOBAL'),
                (110, 'Texto livre',   'TEXTO',           10, 0, 0, 'PERFIL',        '{}', 1, 1, 'GLOBAL'),
                (201, 'Outro tenant',  'ESCOLHA_SIMPLES',  1, 0, 0, NULL,            '{}', 1, 2, 'GLOBAL')
        """))
        opcoes = [
            (101, ["Candidato A", "Candidato B", "Indeciso"]),
            (102, ["Candidato A", "Candidato B"]),
            (103, ["Candidato A", "Candidato B"]),
            (105, ["Feminino", "Masculino"]),
            (108, ["Sim", "Não"]),
            (201, ["Y"]),
        ]
        rows, next_id = [], 1
        for pergunta_id, textos in opcoes:
            for ordem, texto in enumerate(textos, start=1):
                rows.append({"id": next_id, "texto": texto, "ordem": ordem, "qid": pergunta_id})
                next_id += 1
        connection.execute(
            text("INSERT INTO opcoes (id, texto, ordem, pergunta_id, proxima_pergunta_id) VALUES (:id, :texto, :ordem, :qid, NULL)"),
            rows,
        )
        connection.execute(text(
            "INSERT INTO categorias_resposta_espontanea (id, pesquisa_id, nome, nome_normalizado, ativo, criado_por_id, atualizado_por_id) "
            "VALUES (1, 1, 'Candidato A', 'candidato a', 1, 1, 1), (2, 1, 'Inativa', 'inativa', 0, 1, 1)"
        ))
        connection.execute(text(
            "INSERT INTO territorio_eleitoral (id, base_eleitoral_id, tipo, nome) VALUES (900, 1, 'MUNICIPIO', 'Macapá')"
        ))
        connection.execute(text(
            "INSERT INTO setores (id, nome, meta, tolerancia, finalidade, geometria, pesquisa_id, agente_id, municipio_territorio_id) VALUES "
            "(11, 'Centro', 10, 50, 'AMBOS', 'SRID=4326;POLYGON((0 0,1 0,1 1,0 1,0 0))', 1, NULL, 900), "
            "(12, 'Norte', 10, 50, 'OPERACAO', NULL, 1, NULL, NULL), "
            "(21, 'Alheio', 10, 50, 'AMBOS', NULL, 2, NULL, NULL)"
        ))
        # Catalogo ESPELHANDO o real: modulo ativo, feature INATIVA.
        connection.execute(text(
            "INSERT INTO modulos (chave, nome, descricao, ativo) VALUES ('inteligencia_eleitoral', 'Inteligência Eleitoral', NULL, 1)"
        ))
        connection.execute(text(
            "INSERT INTO modulo_funcionalidades (modulo_id, chave, nome, descricao, ativo) "
            "SELECT id, 'potencial_crescimento', 'Potencial de Crescimento', NULL, 0 FROM modulos WHERE chave='inteligencia_eleitoral'"
        ))

    users = {
        1: SimpleNamespace(id=1, company_id=10, ativo=True, perfil_id=1),
        2: SimpleNamespace(id=2, company_id=20, ativo=True, perfil_id=1),
        3: SimpleNamespace(id=3, company_id=10, ativo=True, perfil_id=2),
    }
    state = {"user": users[1]}

    app = FastAPI()
    app.include_router(api_module.router)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: state["user"]

    yield SimpleNamespace(
        client=TestClient(app), Session=Session, engine=engine, app=app,
        users=users, state=state,
    )
    engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def url(leaf, projeto=1, pesquisa=1):
    return BASE.format(projeto=projeto, pesquisa=pesquisa) + leaf


def activate_feature(context):
    with context.engine.begin() as connection:
        connection.execute(text(
            "UPDATE modulo_funcionalidades SET ativo=1 WHERE chave='potencial_crescimento'"
        ))


def grant_entitlement(context, *, company=10, projeto=None, pesquisa=None, status="ATIVO"):
    with context.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO modulo_entitlements (company_id, modulo_id, projeto_id, pesquisa_id, status, inicia_em, expira_em, criado_em, atualizado_em) "
                "SELECT :company, id, :projeto, :pesquisa, :status, NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
                "FROM modulos WHERE chave='inteligencia_eleitoral'"
            ),
            {"company": company, "projeto": projeto, "pesquisa": pesquisa, "status": status},
        )
        connection.execute(text(
            "INSERT INTO modulo_entitlement_funcionalidades (entitlement_id, funcionalidade_id, criado_em) "
            "SELECT e.id, f.id, CURRENT_TIMESTAMP FROM modulo_entitlements e, modulo_funcionalidades f "
            "WHERE f.chave='potencial_crescimento' AND e.id = (SELECT MAX(id) FROM modulo_entitlements)"
        ))


def enable(context, **kwargs):
    activate_feature(context)
    grant_entitlement(context, **kwargs)


def seed_interviews(context):
    """DADOS SINTETICOS: 1 apoiador + 6 elegiveis (mesma logica do motor).

    Segunda opcao no elegivel: base=4, favoravel=3 (75%);
    FEM: base=2, favoravel=1 (50%); delta=-25pp; lift=2/3.
    """
    rows = [
        (1, {101: "Candidato A", 105: "Feminino"}),
        (2, {101: "Candidato B", 103: "Candidato A", 102: ["Candidato B"], 105: "Feminino", 106: "20"}),
        (3, {101: "Candidato B", 103: "Candidato B", 102: ["Candidato A"], 105: "Feminino", 106: "30"}),
        (4, {101: "Candidato B", 103: "Candidato A", 105: "Masculino", 106: "40"}),
        (5, {101: "Indeciso", 105: "Feminino", 106: "22"}),
        (6, {101: "Candidato B", 105: "Masculino"}),
        (7, {101: "Candidato B", 103: "Candidato A", 105: "Masculino"}),
    ]
    with context.engine.begin() as connection:
        for coleta_id, answers in rows:
            connection.execute(
                text(
                    "INSERT INTO coletas (id, pesquisa_id, agente_id, company_id, client_uuid, status_sincronizacao, inconformidade_localizacao) "
                    "VALUES (:id, 1, 1, 10, :uuid, 'sincronizado', 0)"
                ),
                {"id": coleta_id, "uuid": f"uuid-{coleta_id}"},
            )
            for qid, valor in answers.items():
                payload = json.dumps(valor, ensure_ascii=False) if isinstance(valor, list) else valor
                connection.execute(
                    text("INSERT INTO respostas (pergunta_id, coleta_id, valor_resposta) VALUES (:qid, :cid, :valor)"),
                    {"qid": qid, "cid": coleta_id, "valor": payload},
                )


def base_config(**overrides):
    """minimum_base 1/2 = VALORES SINTETICOS DE TESTE, nao default."""
    payload = {
        "schema_version": 1,
        "pesquisa_id": 1,
        "target": {
            "label": "Candidato A",
            "cargo": "Senador",
            "bindings": [{"question_id": 101, "values": ["Candidato A"]}],
        },
        "scenario": {
            "label": "Cenário principal",
            "ballot_selection_mode": "SINGLE",
            "intention_questions": [{"question_id": 101, "slot": "VOTO"}],
        },
        "intention_taxonomy": {"indeciso_declarado": ["Indeciso"]},
        "eligibility": {
            "include_indeciso": True,
            "include_branco_nulo": False,
            "include_ns_nr": False,
            "include_nao_pretende_votar": False,
        },
        "signals": [{"type": "SECOND_OPTION", "question_id": 103}],
        "profile_dimensions": [
            {
                "question_id": 105,
                "label": "Sexo",
                "mode": "CATEGORICAL",
                "groups": [
                    {"key": "FEM", "label": "Mulheres", "values": ["Feminino"]},
                    {"key": "MASC", "label": "Homens", "values": ["Masculino"]},
                ],
            }
        ],
        "territory": {"level": "NONE"},
        "weighting": {"mode": "NAO_PONDERADO"},
        "minimum_base": {"suppress_below_n": 1, "warn_below_n": 2},
        "reference": {"type": "ELIGIBLE_UNIVERSE"},
        "uncertainty": {"method": "WILSON_AAS_APPROX", "confidence_level": 0.95},
    }
    payload["target"]["bindings"].append({"question_id": 103, "values": ["Candidato A"]})
    payload.update(copy.deepcopy(overrides))
    return payload


# ---------------------------------------------------------------------------
# A01-A10 — seguranca
# ---------------------------------------------------------------------------


def test_a01_unauthenticated_is_401(context):
    override = context.app.dependency_overrides.pop(get_current_user)
    try:
        response = context.client.get(url("/opcoes-configuracao"))
        assert response.status_code == 401
    finally:
        context.app.dependency_overrides[get_current_user] = override


def test_a02_a04_resource_resolution_404(context):
    enable(context)
    assert context.client.get(url("/opcoes-configuracao", projeto=999)).status_code == 404
    assert context.client.get(url("/opcoes-configuracao", pesquisa=999)).status_code == 404
    # Pesquisa 3 existe, mas pertence ao Projeto 3 — path com Projeto 1: 404.
    assert context.client.get(url("/opcoes-configuracao", projeto=1, pesquisa=3)).status_code == 404


def test_a05_a06_cross_tenant_404_before_commercial_403(context):
    # Feature INATIVA e sem entitlement: ainda assim recurso alheio e 404,
    # nunca 403 comercial (A06).
    response = context.client.get(url("/opcoes-configuracao", projeto=2, pesquisa=2))
    assert response.status_code == 404
    assert "Pesquisa" in response.json()["detail"]


def test_a07_permission_required(context):
    enable(context)
    context.state["user"] = context.users[3]  # Agente: sem INTELIGENCIA_VER
    response = context.client.get(url("/opcoes-configuracao"))
    assert response.status_code == 403
    assert "perfil" in response.json()["detail"].lower()


def test_a08_inactive_feature_is_404(context):
    grant_entitlement(context)  # entitlement existe, mas feature inativa
    response = context.client.get(url("/opcoes-configuracao"))
    assert response.status_code == 404
    assert response.json()["detail"] == "Capacidade comercial nao encontrada."


def test_a09_active_feature_without_entitlement_is_403(context):
    activate_feature(context)
    response = context.client.get(url("/opcoes-configuracao"))
    assert response.status_code == 403


def test_a10_full_chain_grants_access(context):
    enable(context)
    assert context.client.get(url("/opcoes-configuracao")).status_code == 200


# ---------------------------------------------------------------------------
# A11-A16 — escopo do entitlement
# ---------------------------------------------------------------------------


def test_a11_a13_entitlement_scopes_grant(context):
    activate_feature(context)
    for kwargs in ({"company": 10}, {"projeto": 1}, {"pesquisa": 1}):
        with context.engine.begin() as connection:
            connection.execute(text("DELETE FROM modulo_entitlement_funcionalidades"))
            connection.execute(text("DELETE FROM modulo_entitlements"))
        grant_entitlement(context, **kwargs)
        assert context.client.get(url("/opcoes-configuracao")).status_code == 200, kwargs


def test_a14_a15_entitlement_of_other_scope_is_403(context):
    activate_feature(context)
    grant_entitlement(context, projeto=3)
    assert context.client.get(url("/opcoes-configuracao")).status_code == 403
    with context.engine.begin() as connection:
        connection.execute(text("DELETE FROM modulo_entitlement_funcionalidades"))
        connection.execute(text("DELETE FROM modulo_entitlements"))
    grant_entitlement(context, pesquisa=3)
    assert context.client.get(url("/opcoes-configuracao")).status_code == 403


def test_a16_entitlements_are_additive(context):
    activate_feature(context)
    grant_entitlement(context, pesquisa=1, status="SUSPENSO")
    grant_entitlement(context, company=10)  # amplo ativo prevalece aditivamente
    assert context.client.get(url("/opcoes-configuracao")).status_code == 200


# ---------------------------------------------------------------------------
# A17-A20 — path/body
# ---------------------------------------------------------------------------


def test_a17_a18_path_body_survey_match(context):
    enable(context)
    seed_interviews(context)
    ok = context.client.post(url("/validar-configuracao"), json=base_config())
    assert ok.status_code == 200
    mismatch = base_config(pesquisa_id=3)
    response = context.client.post(url("/validar-configuracao"), json=mismatch)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "SURVEY_PATH_BODY_MISMATCH"


def test_a19_a20_tenant_fields_rejected(context):
    enable(context)
    for field in ("company_id", "tenant_id"):
        payload = base_config()
        payload[field] = 10
        response = context.client.post(url("/validar-configuracao"), json=payload)
        assert response.status_code == 422, field


# ---------------------------------------------------------------------------
# A21-A47 — GET opcoes
# ---------------------------------------------------------------------------


def get_options(context):
    enable(context)
    response = context.client.get(url("/opcoes-configuracao"))
    assert response.status_code == 200
    return response.json()


def question_by_id(options, question_id):
    matches = [item for item in options["questions"] if item["id"] == question_id]
    assert matches, question_id
    return matches[0]


def test_a21_a25_questions_metadata(context):
    options = get_options(context)
    ids = {item["id"] for item in options["questions"]}
    assert 201 not in ids                       # A21: so perguntas da Pesquisa
    assert 109 not in ids                       # A22: inativa nao aparece
    intent = question_by_id(options, 101)
    assert intent["question_type"] == "ESCOLHA_SIMPLES"   # A23
    assert intent["analytic_role"] == "INTENCAO_VOTO"     # A24 (metadata)
    assert intent["analytic_metadata"] == {"dimensao": "ELEITORAL"}  # A25


def test_a26_compatibility_is_technical_not_textual(context):
    options = get_options(context)
    # 110 e TEXTO nao espontaneo com papel PERFIL: nada de compatibilidade
    # (papel nao transforma tipo incompativel — A33/A34 tambem).
    texto = question_by_id(options, 110)
    assert texto["compatible_as"] == []
    # 102 chama-se 'Rejeicao', mas a compatibilidade vem do TIPO (multipla),
    # nao do texto: tambem serve a intencao/segunda opcao.
    rejeicao = question_by_id(options, 102)
    assert set(rejeicao["compatible_as"]) >= {"INTENTION", "REJECTION", "SECOND_OPTION"}


def test_a27_a29_values_canonical_and_active_categories(context):
    options = get_options(context)
    assert question_by_id(options, 105)["values"] == ["Feminino", "Masculino"]  # A27
    espontanea = question_by_id(options, 107)
    assert espontanea["values"] == ["Candidato A"]  # A28: categorias ativas
    assert "Inativa" not in espontanea["values"]    # A29


def test_a30_territorial_question_has_no_signal_compatibility(context):
    options = get_options(context)
    territorial = question_by_id(options, 108)
    assert not set(territorial["compatible_as"]) & {
        "INTENTION", "REJECTION", "SECOND_OPTION", "VOTE_DECISION"
    }


def test_a31_a34_profile_compatibilities(context):
    options = get_options(context)
    assert "PROFILE_CATEGORICAL" in question_by_id(options, 105)["compatible_as"]  # A31
    assert "PROFILE_NUMERIC" in question_by_id(options, 106)["compatible_as"]      # A32
    assert question_by_id(options, 110)["compatible_as"] == []                     # A33/A34


def test_a35_a40_territory_options(context):
    options = get_options(context)
    territory = options["territory"]
    assert set(territory["supported_levels"]) == {"NONE", "SETOR", "MUNICIPIO"}  # A35-A37
    setor_ids = {item["id"] for item in territory["setores"]}
    assert setor_ids == {11, 12}                     # A38: somente da Pesquisa
    by_id = {item["id"]: item for item in territory["setores"]}
    assert by_id[11]["analytically_eligible"] is True
    assert by_id[12]["analytically_eligible"] is False  # A39: OPERACAO-only
    assert territory["municipios"] == [{"id": 900, "nome": "Macapá"}]  # A40: oficial
    assert territory["municipio_level_available"] is True


def test_a41_a47_constraints(context):
    options = get_options(context)
    constraints = options["constraints"]
    assert constraints["max_profile_dimensions"] == 2                       # A41
    assert set(constraints["supported_ballot_modes"]) == {"SINGLE", "MULTIPLE", "ORDERED_MULTIPLE"}  # A42
    assert constraints["weighting_modes"] == ["NAO_PONDERADO"]              # A43
    assert constraints["reference_types"] == ["ELIGIBLE_UNIVERSE"]          # A44
    assert constraints["uncertainty_methods"] == ["WILSON_AAS_APPROX"]      # A45
    dumped = json.dumps(options)
    assert "suppress_below_n" not in dumped                                 # A46
    assert "warn_below_n" not in dumped
    assert "max_uncategorized_rate" not in dumped                           # A47


# ---------------------------------------------------------------------------
# A48-A54 — POST validar-configuracao
# ---------------------------------------------------------------------------


def test_a48_valid_configuration_200_true(context):
    enable(context)
    response = context.client.post(url("/validar-configuracao"), json=base_config())
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["normalized_configuration"]["pesquisa_id"] == 1  # A53


def test_a49_a51_semantic_invalid_is_200_false_with_typed_issues(context):
    enable(context)
    payload = base_config()
    payload["target"]["bindings"][0]["values"] = ["Candidato Z"]
    response = context.client.post(url("/validar-configuracao"), json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["normalized_configuration"] is None
    error = next(e for e in body["errors"] if e["code"] == "TARGET_VALUE_NOT_FOUND")
    assert error["path"].startswith("target.bindings[0].values")   # A50
    assert error["context"]["question_id"] == 101
    assert any(w["code"] for w in body["warnings"])                # A51


def test_a52_value_normalized_warning_surfaces(context):
    enable(context)
    payload = base_config()
    payload["target"]["bindings"][0]["values"] = ["  candidato a "]
    response = context.client.post(url("/validar-configuracao"), json=payload)
    body = response.json()
    assert body["valid"] is True
    assert any(w["code"] == "VALUE_NORMALIZED" for w in body["warnings"])
    assert body["normalized_configuration"]["target"]["bindings"][0]["values"] == ["Candidato A"]


def test_a54_validation_does_not_run_engine(context, monkeypatch):
    enable(context)
    called = {"n": 0}

    def spy(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("motor nao deveria rodar na validacao")

    monkeypatch.setattr(api_module, "analyze_growth_potential", spy)
    response = context.client.post(url("/validar-configuracao"), json=base_config())
    assert response.status_code == 200
    assert called["n"] == 0


# ---------------------------------------------------------------------------
# A55-A60 — POST analisar
# ---------------------------------------------------------------------------


def analisar(context, payload=None):
    return context.client.post(url("/analisar"), json=payload or base_config())


def test_a55_a57_valid_config_runs_engine_once(context, monkeypatch):
    enable(context)
    seed_interviews(context)
    calls = {"n": 0}
    original = api_module.analyze_growth_potential

    def spy(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(api_module, "analyze_growth_potential", spy)
    response = analisar(context)
    assert response.status_code == 200
    assert calls["n"] == 1


def test_a56_invalid_config_is_422_typed(context):
    enable(context)
    payload = base_config()
    payload["target"]["bindings"][0]["values"] = ["Candidato Z"]
    response = analisar(context, payload)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "GROWTH_CONFIGURATION_INVALID"
    assert any(e["code"] == "TARGET_VALUE_NOT_FOUND" for e in detail["errors"])


def test_a58_growth_analysis_fully_mapped(context):
    enable(context)
    seed_interviews(context)
    body = analisar(context).json()
    assert set(body) == {
        "snapshot", "universe", "territory_diagnostics", "coverage",
        "findings", "warnings",
    }
    assert body["universe"]["survey_n"] == 7
    assert body["universe"]["eligible_n"] == 6
    finding = body["findings"][0]
    evidence = finding["evidences"][0]
    for field in ("segment_numerator", "segment_base_n", "reference_numerator",
                  "reference_base_n", "delta_pp", "lift"):
        assert field in evidence  # denominadores nunca escondidos


def test_a59_segment_limit_mapped_to_422(context, monkeypatch):
    enable(context)
    seed_interviews(context)
    from pesquisa360.inteligencia_eleitoral import engine as growth_engine
    monkeypatch.setattr(growth_engine, "GROWTH_MAX_SEGMENTS", 1)
    response = analisar(context)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "SEGMENT_LIMIT_EXCEEDED"
    assert detail["context"]["limit"] == 1


def test_a60_unexpected_error_is_not_422(context, monkeypatch):
    enable(context)
    seed_interviews(context)

    def boom(*args, **kwargs):
        raise RuntimeError("falha inesperada")

    monkeypatch.setattr(api_module, "analyze_growth_potential", boom)
    with pytest.raises(RuntimeError):
        analisar(context)  # segue o fluxo 500/log padrao, nunca 422 generico


# ---------------------------------------------------------------------------
# A61-A69 + unidades — serializacao numerica
# ---------------------------------------------------------------------------


def evidence_from(body):
    fem = next(f for f in body["findings"] if f["segment_key"] == "profile:105=FEM")
    return fem, fem["evidences"][0]


def test_a61_a69_json_numbers_and_units(context):
    enable(context)
    seed_interviews(context)
    body = analisar(context).json()
    fem, evidence = evidence_from(body)
    # Unidades canonicas do cenario sintetico: 0.5 vs 0.75, -25pp, lift 2/3.
    assert evidence["segment_rate"] == 0.5                    # A61 (number)
    assert evidence["reference_rate"] == 0.75                 # A62
    assert isinstance(fem["participation_rate"], float)       # A63
    assert evidence["delta_pp"] == -25.0                      # A64 (pp)
    assert isinstance(evidence["lift"], float)                # A65
    interval = evidence["segment_interval"]
    assert isinstance(interval["low"], float) and isinstance(interval["high"], float)  # A66
    assert 0.0 <= interval["low"] <= interval["high"] <= 1.0
    # A67: nenhuma metrica como string.
    for value in (evidence["segment_rate"], evidence["reference_rate"],
                  evidence["delta_pp"], fem["participation_rate"],
                  interval["confidence_level"]):
        assert not isinstance(value, str)
    assert fem["weighted_base"] is None                       # A68


def test_a69_lift_undefined_is_null(context):
    enable(context)
    # Ninguem tem o alvo como segunda opcao: referencia zero.
    with context.engine.begin() as connection:
        for coleta_id, sexo in ((1, "Feminino"), (2, "Masculino")):
            connection.execute(
                text("INSERT INTO coletas (id, pesquisa_id, agente_id, company_id, client_uuid, status_sincronizacao, inconformidade_localizacao) VALUES (:id, 1, 1, 10, :uuid, 'sincronizado', 0)"),
                {"id": coleta_id, "uuid": f"uuid-{coleta_id}"},
            )
            connection.execute(
                text("INSERT INTO respostas (pergunta_id, coleta_id, valor_resposta) VALUES (101, :cid, 'Candidato B'), (103, :cid, 'Candidato B'), (105, :cid, :sexo)"),
                {"cid": coleta_id, "sexo": sexo},
            )
    body = analisar(context).json()
    _, evidence = evidence_from(body)
    assert evidence["reference_rate"] == 0.0
    assert evidence["lift"] is None


def test_units_are_canonical_not_percent(context):
    enable(context)
    seed_interviews(context)
    body = analisar(context).json()
    _, evidence = evidence_from(body)
    assert evidence["segment_rate"] not in (50, "0.5")   # fracao, nao percentual
    assert evidence["reference_rate"] not in (75, "0.75")
    assert evidence["delta_pp"] != -0.25                  # pp, nao fracao


# ---------------------------------------------------------------------------
# A70-A79 — provas negativas e warnings
# ---------------------------------------------------------------------------


def test_a70_a75_negative_proofs(context):
    enable(context)
    seed_interviews(context)
    dumped = json.dumps(analisar(context).json()).lower()
    for forbidden in ("\"score\"", "potential_level", "\"rank\"",
                      "projected_votes", "potential_votes", "company_id"):
        assert forbidden not in dumped, forbidden


def test_a76_a79_methodological_warnings_reach_http(context):
    enable(context)
    seed_interviews(context)
    body = analisar(context).json()
    codes = {w["code"] for w in body["warnings"]}
    assert {"UNWEIGHTED_ANALYSIS", "SRS_ASSUMPTION", "REFERENCE_INCLUDES_SEGMENT"} <= codes
    structured = [w for w in body["warnings"] if w["context"] is not None]
    assert structured and all(isinstance(w["context"], dict) for w in structured)  # A79


# ---------------------------------------------------------------------------
# A80-A83 — determinismo
# ---------------------------------------------------------------------------


def test_a80_a83_determinism_through_http(context):
    enable(context)
    seed_interviews(context)
    first = analisar(context).json()
    second = analisar(context).json()
    first["snapshot"].pop("executed_at")
    second["snapshot"].pop("executed_at")
    assert first == second                                        # A80
    assert first["snapshot"]["configuration_hash"]                # A81
    assert first["snapshot"]["input_fingerprint"]                 # A82
    keys = [f["segment_key"] for f in first["findings"]]
    assert keys == sorted(keys)                                   # A83


# ---------------------------------------------------------------------------
# A84-A87 — OpenAPI
# ---------------------------------------------------------------------------


def test_a84_a87_openapi_contract(context):
    schema = context.app.openapi()
    paths = schema["paths"]
    base = BASE.format(projeto="{projeto_id}", pesquisa="{pesquisa_id}")
    assert f"{base}/opcoes-configuracao" in paths                  # A84
    assert f"{base}/validar-configuracao" in paths
    assert f"{base}/analisar" in paths
    analisar_schema = paths[f"{base}/analisar"]["post"]
    ref = analisar_schema["responses"]["200"]["content"]["application/json"]["schema"]
    assert ref.get("$ref", "").endswith("GrowthAnalysisResponse")  # A85/A86
    evidence = schema["components"]["schemas"]["GrowthEvidenceDTO"]["properties"]
    rate_types = json.dumps(evidence["segment_rate"])
    assert "number" in rate_types and "string" not in rate_types   # A87


# ---------------------------------------------------------------------------
# Cadeia E2E completa (secao 71)
# ---------------------------------------------------------------------------


def test_e2e_options_validate_analyze(context):
    enable(context)
    seed_interviews(context)
    options = context.client.get(url("/opcoes-configuracao")).json()
    intent = question_by_id(options, 101)
    assert "INTENTION" in intent["compatible_as"]
    assert "Candidato A" in intent["values"]

    payload = base_config()
    validated = context.client.post(url("/validar-configuracao"), json=payload)
    assert validated.status_code == 200 and validated.json()["valid"] is True

    normalized = validated.json()["normalized_configuration"]
    analyzed = context.client.post(url("/analisar"), json=normalized)
    assert analyzed.status_code == 200
    body = analyzed.json()
    assert body["snapshot"]["engine_version"]
    assert body["universe"]["eligible_n"] == 6
    assert len(body["findings"]) == 2
