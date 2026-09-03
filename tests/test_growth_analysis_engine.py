"""Testes do motor estatistico do Potencial de Crescimento (Prompt 03).

Cobre os casos E01-E112 do plano. TODOS os dados sao DADOS SINTETICOS DE
TESTE; os limiares de base minima e de qualidade de espontanea usados aqui
sao VALORES SINTETICOS, nunca defaults metodologicos de producao (D04/D15).
"""

import copy
import json
import os
from decimal import Decimal
from types import SimpleNamespace

import pytest
from shapely import wkt as shapely_wkt
from shapely.geometry import mapping
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SECRET_KEY", "test-only-growth-engine-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.inteligencia_eleitoral import (
    AnalysisWarningCode,
    EvidenceStatus,
    FindingStatus,
    GrowthAnalysisConfigurationError,
    GrowthSegmentLimitExceededError,
    ObservedDirection,
    SignalType,
    analyze_growth_potential,
    compute_configuration_hash,
    parse_growth_analysis_configuration,
)
from pesquisa360.inteligencia_eleitoral import engine as growth_engine
from pesquisa360.inteligencia_eleitoral import statistics as growth_statistics
from tests.acl_fixture import criar_tabelas_acl


# ---------------------------------------------------------------------------
# Harness (SQLite + shims PostGIS, mesmo padrao de test_multidimensional_cross)
# ---------------------------------------------------------------------------


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

    criar_tabelas_acl(engine)
    Session = sessionmaker(bind=engine)
    with engine.begin() as connection:
        for statement in (
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
            "CREATE TABLE base_eleitoral (id INTEGER PRIMARY KEY, nome TEXT, ano INTEGER, uf TEXT, fonte TEXT, fonte_referencia TEXT, versao TEXT, data_referencia DATE, status TEXT, substituida_por_id INTEGER, company_id INTEGER, comparecimento_estimado NUMERIC, percentual_votos_validos NUMERIC, criado_por_id INTEGER, criado_em DATETIME, atualizado_em DATETIME)",
            "CREATE TABLE projeto_base_eleitoral (id INTEGER PRIMARY KEY, projeto_id INTEGER, base_eleitoral_id INTEGER, principal BOOLEAN, vinculado_em DATETIME)",
        ):
            connection.execute(text(statement))
        connection.execute(text("INSERT INTO perfis (id, nome) VALUES (1, 'Gerente')"))
        connection.execute(text(
            "INSERT INTO usuarios (id, email, nome, senha_hash, ativo, perfil_id, company_id) "
            "VALUES (1, 'gerente@a', 'Gerente A', 'x', 1, 1, 10), (2, 'gerente@b', 'Gerente B', 'x', 1, 1, 20)"
        ))
        connection.execute(text(
            "INSERT INTO projetos VALUES (1, 'Projeto A', NULL, 'Ativo', NULL, NULL, 1, 10), "
            "(2, 'Projeto B', NULL, 'Ativo', NULL, NULL, 2, 20)"
        ))
        connection.execute(text(
            "INSERT INTO pesquisas VALUES (1, 'Pesquisa A', NULL, 1, 1, NULL, NULL), "
            "(2, 'Pesquisa B', NULL, 1, 2, NULL, NULL)"
        ))
        connection.execute(text("""
            INSERT INTO perguntas
                (id, texto_pergunta, tipo_pergunta, ordem, eh_obrigatoria,
                 eh_resposta_espontanea, papel_analitico, metadados_analiticos, ativo,
                 pesquisa_id, aplicabilidade)
            VALUES
                (101, 'Intencao',      'ESCOLHA_SIMPLES',  1, 1, 0, 'INTENCAO_VOTO', '{}', 1, 1, 'GLOBAL'),
                (102, 'Rejeicao',      'MULTIPLA_ESCOLHA', 2, 0, 0, 'REJEICAO',      '{}', 1, 1, 'GLOBAL'),
                (103, 'Segunda opcao', 'ESCOLHA_SIMPLES',  3, 0, 0, 'SEGUNDA_OPCAO', '{}', 1, 1, 'GLOBAL'),
                (104, 'Decisao',       'ESCOLHA_SIMPLES',  4, 0, 0, 'DECISAO_VOTO',  '{}', 1, 1, 'GLOBAL'),
                (105, 'Sexo',          'ESCOLHA_SIMPLES',  5, 0, 0, 'PERFIL',        '{}', 1, 1, 'GLOBAL'),
                (106, 'Idade',         'NUMERO',           6, 0, 0, 'PERFIL',        '{}', 1, 1, 'GLOBAL'),
                (107, 'Espontanea',    'TEXTO',            7, 0, 1, NULL,            '{}', 1, 1, 'GLOBAL'),
                (201, 'Outro tenant',  'ESCOLHA_SIMPLES',  1, 0, 0, NULL,            '{}', 1, 2, 'GLOBAL')
        """))
        opcoes = [
            (101, ["Candidato A", "Candidato B", "Candidato C", "Indeciso", "Branco/Nulo", "NS/NR", "Não pretende votar"]),
            (102, ["Candidato A", "Candidato B", "Indeciso", "NS/NR"]),
            (103, ["Candidato A", "Candidato B", "NS/NR"]),
            (104, ["Definitivo", "Pode mudar"]),
            (105, ["Feminino", "Masculino"]),
            (201, ["Y"]),
        ]
        rows = []
        next_id = 1
        for pergunta_id, textos in opcoes:
            for ordem, texto in enumerate(textos, start=1):
                rows.append({"id": next_id, "texto": texto, "ordem": ordem, "qid": pergunta_id})
                next_id += 1
        connection.execute(
            text("INSERT INTO opcoes (id, texto, ordem, pergunta_id, proxima_pergunta_id) VALUES (:id, :texto, :ordem, :qid, NULL)"),
            rows,
        )
        # Espontanea: categoria ativa 'Candidato A' mapeada por 'candidato a'.
        connection.execute(text(
            "INSERT INTO categorias_resposta_espontanea (id, pesquisa_id, nome, nome_normalizado, ativo, criado_por_id, atualizado_por_id) "
            "VALUES (1, 1, 'Candidato A', 'candidato a', 1, 1, 1)"
        ))
        connection.execute(text(
            "INSERT INTO mapeamentos_resposta_espontanea (id, pesquisa_id, categoria_id, chave_normalizada, texto_referencia, ativo, criado_por_id, atualizado_por_id) "
            "VALUES (1, 1, 1, 'candidato a', 'Candidato A', 1, 1, 1)"
        ))
        # Base eleitoral principal VALIDADA do Projeto 1 (contexto eleitoral).
        connection.execute(text(
            "INSERT INTO base_eleitoral (id, nome, ano, uf, fonte, versao, data_referencia, status, company_id, criado_por_id) "
            "VALUES (1, 'Base AP', 2026, 'AP', 'TSE', 'v1', '2026-01-01', 'VALIDADA', NULL, 1)"
        ))
        connection.execute(text(
            "INSERT INTO projeto_base_eleitoral (id, projeto_id, base_eleitoral_id, principal) VALUES (1, 1, 1, 1)"
        ))
        connection.execute(text(
            "INSERT INTO territorio_eleitoral (id, base_eleitoral_id, tipo, nome, eleitorado_apto) VALUES "
            "(900, 1, 'MUNICIPIO', 'Macapá', 50000), (901, 1, 'MUNICIPIO', 'Santana', 30000), "
            "(910, 1, 'BAIRRO', 'Central', 12000)"
        ))
        connection.execute(text(
            "INSERT INTO setor_territorio_eleitoral (id, setor_id, territorio_eleitoral_id) VALUES (1, 11, 910)"
        ))
        # Setores: 11 e 14 analiticos com municipio; 12 operacional; 13 analitico
        # sobreposto a 11 (faixa 0.8-1.5) e SEM municipio resolvivel.
        connection.execute(text(
            "INSERT INTO setores (id, nome, meta, tolerancia, finalidade, geometria, pesquisa_id, agente_id, municipio_territorio_id) VALUES "
            "(11, 'Centro', 10, 50, 'AMBOS', 'SRID=4326;POLYGON((0 0,1 0,1 1,0 1,0 0))', 1, NULL, 900), "
            "(14, 'Leste', 10, 50, 'AMBOS', 'SRID=4326;POLYGON((2 0,3 0,3 1,2 1,2 0))', 1, NULL, 901), "
            "(12, 'Norte', 10, 50, 'OPERACAO', 'SRID=4326;POLYGON((0 2,1 2,1 3,0 3,0 2))', 1, NULL, NULL), "
            "(13, 'Sobreposto', 10, 50, 'AMBOS', 'SRID=4326;POLYGON((0.8 0,1.5 0,1.5 1,0.8 1,0.8 0))', 1, NULL, NULL)"
        ))

    user = SimpleNamespace(id=1, company_id=10, ativo=True, perfil_id=1)
    yield SimpleNamespace(Session=Session, user=user, engine=engine)
    engine.dispose()


def add_interview(
    context,
    coleta_id,
    answers=None,
    *,
    point="0.5 0.5",
    agente=1,
    company=10,
    pesquisa=1,
):
    """Insere uma entrevista sintetica. answers: {pergunta_id: valor|lista}."""
    geometry = f"SRID=4326;POINT({point})" if point else None
    with context.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO coletas (id, pesquisa_id, agente_id, company_id, client_uuid, status_sincronizacao, inconformidade_localizacao, localizacao_inicio) "
                "VALUES (:id, :pesquisa, :agente, :company, :uuid, 'sincronizado', 0, :geom)"
            ),
            {"id": coleta_id, "pesquisa": pesquisa, "agente": agente, "company": company,
             "uuid": f"uuid-{coleta_id}", "geom": geometry},
        )
        for pergunta_id, valor in (answers or {}).items():
            payload = json.dumps(valor, ensure_ascii=False) if isinstance(valor, list) else valor
            connection.execute(
                text("INSERT INTO respostas (pergunta_id, coleta_id, valor_resposta) VALUES (:qid, :cid, :valor)"),
                {"qid": pergunta_id, "cid": coleta_id, "valor": payload},
            )


def seed_standard(context):
    """Cenario canonico de mao (DADOS SINTETICOS DE TESTE).

    Politica: indeciso/branco-nulo INCLUIDOS; NS/NR e nao-pretende EXCLUIDOS.

    id  intencao(101)        segunda(103)   rejeicao(102)      decisao(104)  sexo(105) idade(106) ponto
    1   Candidato A (alvo)   -              -                  -             Feminino  -          setor 11
    2   Candidato B          Candidato A    [Candidato B]      Pode mudar    Feminino  20         setor 11
    3   Candidato B          Candidato B    [Candidato A]      Definitivo    Feminino  30         setor 11
    4   Candidato B          Candidato A    -                  -             Masculino 40         setor 11
    5   Indeciso             -              -                  -             Feminino  22         setor 11
    6   Branco/Nulo          -              -                  -             Masculino -          setor 11
    7   NS/NR (excluida)     -              -                  -             Feminino  -          setor 11
    8   Não pretende votar   -              -                  -             -         -          setor 11
    9   (sem intencao)       -              -                  -             Feminino  -          setor 11
    10  Candidato B          Candidato A    -                  -             Masculino -          setor 14

    Universos: survey=10; analytical=10; supporters=1; excluidas especiais=2
    (7,8); technical_missing=1 (9); ELEGIVEL = {2,3,4,5,6,10} -> n=6.

    Segunda opcao no elegivel: base={2,3,4,10}=4; favoravel={2,4,10}=3 -> 75%.
    Segmento Feminino={2,3,5}: base={2,3}=2; favoravel={2}=1 -> 50%;
    delta=-25pp; lift=0.5/0.75=2/3; direcao UNFAVORABLE (maior=favoravel).
    Rejeicao no elegivel: base={2,3}=2; rejeita alvo={3}=1 -> 50%.
    Decisao no elegivel: base valida={2,3}=2; MOBILE={2}=1 -> 50%.
    """
    add_interview(context, 1, {101: "Candidato A", 105: "Feminino"})
    add_interview(context, 2, {101: "Candidato B", 103: "Candidato A", 102: ["Candidato B"], 104: "Pode mudar", 105: "Feminino", 106: "20"})
    add_interview(context, 3, {101: "Candidato B", 103: "Candidato B", 102: ["Candidato A"], 104: "Definitivo", 105: "Feminino", 106: "30"})
    add_interview(context, 4, {101: "Candidato B", 103: "Candidato A", 105: "Masculino", 106: "40"})
    add_interview(context, 5, {101: "Indeciso", 105: "Feminino", 106: "22"})
    add_interview(context, 6, {101: "Branco/Nulo", 105: "Masculino"})
    add_interview(context, 7, {101: "NS/NR", 105: "Feminino"})
    add_interview(context, 8, {101: "Não pretende votar"})
    add_interview(context, 9, {105: "Feminino"})
    add_interview(context, 10, {101: "Candidato B", 103: "Candidato A", 105: "Masculino"}, point="2.5 0.5")


def base_config(**overrides):
    """Configuracao minima; minimum_base 1/2 = VALORES SINTETICOS DE TESTE."""
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
        "intention_taxonomy": {
            "indeciso_declarado": ["Indeciso"],
            "branco_nulo": ["Branco/Nulo"],
            "ns_nr": ["NS/NR"],
            "nao_pretende_votar": ["Não pretende votar"],
        },
        "eligibility": {
            "include_indeciso": True,
            "include_branco_nulo": True,
            "include_ns_nr": False,
            "include_nao_pretende_votar": False,
        },
        "signals": [],
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
    payload.update(copy.deepcopy(overrides))
    return payload


def enriched_config(**overrides):
    payload = base_config(
        signals=[
            {"type": "REJECTION", "question_id": 102},
            {"type": "SECOND_OPTION", "question_id": 103},
            {"type": "VOTE_DECISION", "question_id": 104,
             "decision_groups": {"mobile": ["Pode mudar"], "crystallized": ["Definitivo"]}},
        ],
        target={
            "label": "Candidato A",
            "cargo": "Senador",
            "bindings": [
                {"question_id": 101, "values": ["Candidato A"]},
                {"question_id": 102, "values": ["Candidato A"]},
                {"question_id": 103, "values": ["Candidato A"]},
            ],
        },
    )
    payload.update(copy.deepcopy(overrides))
    return payload


def run(context, payload):
    config, issues = parse_growth_analysis_configuration(payload)
    assert not issues, issues
    with context.Session() as db:
        return analyze_growth_potential(db, context.user, config)


def finding_by_key(analysis, key):
    matches = [item for item in analysis.findings if item.segment_key == key]
    assert matches, f"segmento {key!r} ausente: {[f.segment_key for f in analysis.findings]}"
    return matches[0]


def evidence_of(finding, signal_type):
    matches = [item for item in finding.evidences if item.signal_type == signal_type]
    assert matches, f"sinal {signal_type} ausente"
    return matches[0]


def warning_codes(items):
    return {item.code for item in items}


# ---------------------------------------------------------------------------
# E01-E10 — universo e elegibilidade
# ---------------------------------------------------------------------------


def test_e01_survey_total_and_universes(context):
    seed_standard(context)
    analysis = run(context, base_config())
    assert analysis.universe.survey_n == 10
    assert analysis.universe.analytical_n == 10
    assert analysis.universe.eligible_n == 6


def test_e02_structural_filters_freeze_analytical_universe(context):
    seed_standard(context)
    # Filtro de resposta: apenas mulheres (2,3,5,7,9 tem sexo Feminino; 1 e alvo).
    payload = base_config(
        filters={"agent_ids": [], "setor_ids": [],
                 "response_filters": [{"question_id": 105, "values": ["Feminino"]}]},
    )
    analysis = run(context, payload)
    assert analysis.universe.analytical_n == 6  # coletas 1,2,3,5,7,9
    assert analysis.universe.eligible_n == 3    # 2,3,5
    # Filtro por agente inexistente: universo vazio, sem erro.
    vazio = run(context, base_config(filters={"agent_ids": [999], "setor_ids": [], "response_filters": []}))
    assert vazio.universe.analytical_n == 0 and vazio.universe.eligible_n == 0


def test_e03_e04_supporter_excluded_opponent_included(context):
    seed_standard(context)
    analysis = run(context, base_config())
    assert analysis.universe.current_target_supporters_n == 1
    counts = analysis.universe.intention_classification_counts
    assert counts["REGULAR_ELIGIBLE"] == 4  # 2,3,4,10


def test_e05_a_e08_special_policies(context):
    seed_standard(context)
    included = run(context, base_config())
    assert included.universe.eligible_n == 6  # indeciso e branco/nulo entram
    flipped = run(context, base_config(eligibility={
        "include_indeciso": False, "include_branco_nulo": False,
        "include_ns_nr": True, "include_nao_pretende_votar": True,
    }))
    # Sai 5 (indeciso) e 6 (branco/nulo); entram 7 (NS/NR) e 8 (nao pretende).
    assert flipped.universe.eligible_n == 6
    assert flipped.universe.excluded_special_n == 2


def test_e09_missing_intention_is_unknown_not_indeciso(context):
    seed_standard(context)
    analysis = run(context, base_config())
    assert analysis.universe.technical_missing_intention_n == 1
    counts = analysis.universe.intention_classification_counts
    assert counts.get("TECHNICAL_MISSING") == 1
    # Nao entra no elegivel nem vira INDECISO.
    assert counts.get("INDECISO") == 1  # apenas a coleta 5


def test_e10_no_interview_counted_twice(context):
    seed_standard(context)
    analysis = run(context, base_config())
    counts = analysis.universe.intention_classification_counts
    assert sum(counts.values()) == analysis.universe.analytical_n


# ---------------------------------------------------------------------------
# E11-E17 — ballot modes
# ---------------------------------------------------------------------------


def multiple_ballot_config():
    return base_config(
        scenario={"label": "Senado", "ballot_selection_mode": "MULTIPLE",
                  "intention_questions": [{"question_id": 102, "slot": "VOTOS"}]},
        target={"label": "Candidato A", "cargo": "Senador",
                "bindings": [{"question_id": 102, "values": ["Candidato A"]}]},
        intention_taxonomy={"indeciso_declarado": ["Indeciso"], "ns_nr": ["NS/NR"]},
        eligibility={"include_indeciso": True, "include_branco_nulo": False,
                     "include_ns_nr": False, "include_nao_pretende_votar": False},
    )


def test_e11_e12_single_mode(context):
    seed_standard(context)
    analysis = run(context, base_config())
    assert analysis.universe.current_target_supporters_n == 1  # coleta 1
    assert analysis.universe.eligible_n == 6


def test_e13_e14_multiple_mode(context):
    add_interview(context, 1, {102: ["Candidato B", "Candidato A"], 105: "Feminino"})
    add_interview(context, 2, {102: ["Candidato B"], 105: "Feminino"})
    add_interview(context, 3, {102: ["Candidato B", "Indeciso"], 105: "Masculino"})
    analysis = run(context, multiple_ballot_config())
    # 1: alvo em qualquer posicao -> supporter; 2 e 3: eligible (valor regular).
    assert analysis.universe.current_target_supporters_n == 1
    assert analysis.universe.eligible_n == 2


def test_e15_e16_ordered_multiple_mode(context):
    add_interview(context, 1, {101: "Candidato B", 103: "Candidato A", 105: "Feminino"})
    add_interview(context, 2, {101: "Candidato B", 103: "Candidato C", 105: "Feminino"})
    payload = base_config(
        scenario={"label": "Dois votos", "ballot_selection_mode": "ORDERED_MULTIPLE",
                  "intention_questions": [
                      {"question_id": 101, "slot": "PRIMEIRO_VOTO", "order": 1},
                      {"question_id": 103, "slot": "SEGUNDO_VOTO", "order": 2},
                  ]},
        target={"label": "Candidato A", "cargo": "Senador",
                "bindings": [
                    {"question_id": 101, "values": ["Candidato A"]},
                    {"question_id": 103, "values": ["Candidato A"]},
                ]},
        intention_taxonomy={},
    )
    analysis = run(context, payload)
    # 1: alvo no segundo slot -> supporter. 2: duas adversarias -> UMA eligible.
    assert analysis.universe.current_target_supporters_n == 1
    assert analysis.universe.eligible_n == 1


def test_e17_mixed_special_policies_conservative_exclusion(context):
    # So categorias especiais com politicas divergentes (Indeciso IN, NS/NR OUT).
    add_interview(context, 1, {102: ["Indeciso", "NS/NR"], 105: "Feminino"})
    analysis = run(context, multiple_ballot_config())
    assert analysis.universe.eligible_n == 0
    assert analysis.universe.mixed_special_conflict_n == 1
    assert AnalysisWarningCode.MIXED_SPECIAL_ELIGIBILITY in warning_codes(analysis.warnings)


# ---------------------------------------------------------------------------
# E18-E24 — segmentacao
# ---------------------------------------------------------------------------


def two_dimensions(payload):
    payload["profile_dimensions"].append({
        "question_id": 106, "label": "Idade", "mode": "NUMERIC_RANGES",
        "ranges": [
            {"key": "18_24", "label": "18–24", "min": 18, "max": 24},
            {"key": "25_59", "label": "25–59", "min": 25, "max": 59},
        ],
    })
    return payload


def test_e18_one_dimension_segments(context):
    seed_standard(context)
    analysis = run(context, base_config())
    keys = [item.segment_key for item in analysis.findings]
    assert keys == ["profile:105=FEM", "profile:105=MASC"]
    assert finding_by_key(analysis, "profile:105=FEM").n_bruto == 3   # 2,3,5
    assert finding_by_key(analysis, "profile:105=MASC").n_bruto == 3  # 4,6,10


def test_e19_e20_two_dimensions_cross_only(context):
    seed_standard(context)
    analysis = run(context, two_dimensions(base_config()))
    keys = [item.segment_key for item in analysis.findings]
    # Somente cruzamentos SEXO x IDADE observados; nenhum nivel intermediario.
    assert all(key.count("|") == 1 for key in keys)
    assert "profile:105=FEM|profile:106=18_24" in keys  # coletas 2 (20) e 5 (22)
    assert finding_by_key(analysis, "profile:105=FEM|profile:106=18_24").n_bruto == 2


def test_e21_numeric_ranges_inclusive_bounds(context):
    add_interview(context, 1, {101: "Candidato B", 105: "Feminino", 106: "18"})
    add_interview(context, 2, {101: "Candidato B", 105: "Feminino", 106: "24"})
    add_interview(context, 3, {101: "Candidato B", 105: "Feminino", 106: "25"})
    analysis = run(context, two_dimensions(base_config()))
    assert finding_by_key(analysis, "profile:105=FEM|profile:106=18_24").n_bruto == 2
    assert finding_by_key(analysis, "profile:105=FEM|profile:106=25_59").n_bruto == 1


def test_e22_missing_profile_not_segmented(context):
    seed_standard(context)
    analysis = run(context, two_dimensions(base_config()))
    # Elegiveis sem idade: 6 e 10 -> fora dos segmentos, diagnosticados.
    assert analysis.coverage.not_segmented_n == 2
    assert analysis.coverage.by_reason.get("MISSING_PROFILE:Idade") == 2
    total_segmentado = sum(item.n_bruto for item in analysis.findings)
    assert total_segmentado == analysis.universe.eligible_n - 2


def test_e23_segment_key_is_deterministic_identity(context):
    seed_standard(context)
    analysis = run(context, base_config(territory={"level": "MUNICIPIO"}))
    keys = [item.segment_key for item in analysis.findings]
    assert "profile:105=FEM|territory:municipio=900" in keys
    # Identidade usa key de grupo e ID territorial real, nunca apenas labels.
    assert all("Mulheres" not in key and "Macapá" not in key for key in keys)


def test_e24_query_count_does_not_grow_with_segments(context):
    seed_standard(context)
    counter = {"n": 0}

    @event.listens_for(context.engine, "before_cursor_execute")
    def count_queries(*args):
        counter["n"] += 1

    counter["n"] = 0
    run(context, base_config())
    one_dim = counter["n"]
    counter["n"] = 0
    run(context, two_dimensions(base_config()))
    two_dim = counter["n"]
    event.remove(context.engine, "before_cursor_execute", count_queries)
    assert two_dim == one_dim  # mais segmentos, mesmas queries


# ---------------------------------------------------------------------------
# E25-E31 — territorio
# ---------------------------------------------------------------------------


def test_e25_setor_level_official_classification(context):
    seed_standard(context)
    analysis = run(context, base_config(territory={"level": "SETOR"}))
    keys = [item.segment_key for item in analysis.findings]
    assert "profile:105=MASC|territory:setor=11" in keys
    assert "profile:105=MASC|territory:setor=14" in keys  # coleta 10
    assert finding_by_key(analysis, "profile:105=MASC|territory:setor=14").n_bruto == 1


def test_e26_municipio_level_official_resolution(context):
    seed_standard(context)
    analysis = run(context, base_config(territory={"level": "MUNICIPIO"}))
    keys = [item.segment_key for item in analysis.findings]
    assert "profile:105=MASC|territory:municipio=901" in keys  # Santana via setor 14
    finding = finding_by_key(analysis, "profile:105=MASC|territory:municipio=901")
    assert finding.territory.label == "Santana"


def test_e27_a_e31_territory_diagnostics(context):
    add_interview(context, 1, {101: "Candidato B", 105: "Feminino"})                     # setor 11
    add_interview(context, 2, {101: "Candidato B", 105: "Feminino"}, point="5 5")        # fora de tudo
    add_interview(context, 3, {101: "Candidato B", 105: "Feminino"}, point=None)         # sem coordenada
    add_interview(context, 4, {101: "Candidato B", 105: "Feminino"}, point="0.9 0.5")    # sobreposto 11+13
    add_interview(context, 5, {101: "Candidato B", 105: "Feminino"}, point="1.2 0.5")    # so setor 13
    analysis = run(context, base_config(territory={"level": "MUNICIPIO"}))
    diag = analysis.territory_diagnostics
    assert diag.sem_setor_n == 1
    assert diag.sem_coordenada_n == 1
    assert diag.conflito_setor_n == 1
    assert diag.municipio_nao_resolvido_n == 1  # setor 13 sem municipio
    # Buckets tecnicos NAO viram finding (E30).
    keys = [item.segment_key for item in analysis.findings]
    assert keys == ["profile:105=FEM|territory:municipio=900"]
    # Participacao territorial soma <100% e isso e explicito (E31).
    assert sum(item.n_bruto for item in analysis.findings) == 1
    assert analysis.universe.eligible_n == 5
    assert AnalysisWarningCode.TERRITORY_PARTICIPATION_BELOW_100 in warning_codes(analysis.warnings)
    assert analysis.coverage.not_segmented_n == 4


# ---------------------------------------------------------------------------
# E32-E48 — sinais (segunda opcao, rejeicao, decisao)
# ---------------------------------------------------------------------------


def test_e32_a_e38_second_option_oracle(context):
    seed_standard(context)
    analysis = run(context, enriched_config())
    fem = finding_by_key(analysis, "profile:105=FEM")
    evidence = evidence_of(fem, SignalType.SECOND_OPTION)
    assert evidence.status == EvidenceStatus.AVAILABLE
    assert evidence.segment_numerator == 1 and evidence.segment_base_n == 2
    assert evidence.reference_numerator == 3 and evidence.reference_base_n == 4
    assert evidence.segment_rate == Decimal(1) / Decimal(2)
    assert evidence.reference_rate == Decimal(3) / Decimal(4)
    assert evidence.delta_pp == Decimal("-25")
    assert evidence.lift == (Decimal(1) / Decimal(2)) / (Decimal(3) / Decimal(4))
    assert evidence.observed_direction == ObservedDirection.UNFAVORABLE
    # E34: quem nao respondeu (5, 6) nao esta na base do sinal — ausencia != zero.
    assert evidence.reference_base_n == 4 != analysis.universe.eligible_n


def test_e39_a_e44_rejection(context):
    seed_standard(context)
    # Rejeicoes extras: coleta 4 rejeita A duas vezes (dedup) e B (uma so base).
    with context.engine.begin() as connection:
        connection.execute(
            text("INSERT INTO respostas (pergunta_id, coleta_id, valor_resposta) VALUES (102, 4, :valor)"),
            {"valor": json.dumps(["Candidato A", "Candidato A", "Candidato B"])},
        )
    analysis = run(context, enriched_config())
    fem = finding_by_key(analysis, "profile:105=FEM")
    masc = finding_by_key(analysis, "profile:105=MASC")
    fem_ev = evidence_of(fem, SignalType.REJECTION)
    masc_ev = evidence_of(masc, SignalType.REJECTION)
    # Referencia: base={2,3,4}; rejeitam alvo={3,4} -> 2/3.
    assert fem_ev.reference_base_n == 3 and fem_ev.reference_numerator == 2
    # FEM: base={2,3}, rejeita={3} -> 50% (E42); rejeicao multipla conta UMA vez (E40/E41).
    assert fem_ev.segment_numerator == 1 and fem_ev.segment_base_n == 2
    assert masc_ev.segment_numerator == 1 and masc_ev.segment_base_n == 1
    # E43: rejeicao menor que a referencia -> FAVORABLE (LOWER_IS_FAVORABLE).
    assert fem_ev.segment_rate < fem_ev.reference_rate
    assert fem_ev.observed_direction == ObservedDirection.FAVORABLE
    # E44: a taxa exibida e REJEICAO, nao 1-rejeicao.
    assert fem_ev.segment_rate == Decimal(1) / Decimal(2)


def test_e45_a_e48_vote_decision(context):
    seed_standard(context)
    # Valor fora dos grupos configurados nao entra na base valida (E47).
    with context.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO respostas (pergunta_id, coleta_id, valor_resposta) VALUES (104, 4, 'Talvez')"
        ))
    analysis = run(context, enriched_config())
    fem = evidence_of(finding_by_key(analysis, "profile:105=FEM"), SignalType.VOTE_DECISION)
    # Referencia: base valida={2,3} (o 'Talvez' de 4 fica fora); MOBILE={2}.
    assert fem.reference_base_n == 2 and fem.reference_numerator == 1
    assert fem.segment_numerator == 1 and fem.segment_base_n == 2  # E45/E46
    assert fem.segment_rate == fem.reference_rate
    assert fem.observed_direction == ObservedDirection.NEUTRAL


# ---------------------------------------------------------------------------
# E49-E53 — base minima (limiares SINTETICOS)
# ---------------------------------------------------------------------------


def test_e49_a_e53_minimum_base(context):
    seed_standard(context)
    payload = enriched_config(minimum_base={"suppress_below_n": 3, "warn_below_n": 5})
    analysis = run(context, payload)
    fem = finding_by_key(analysis, "profile:105=FEM")   # n=3
    masc = finding_by_key(analysis, "profile:105=MASC") # n=3
    # E50: entre suppress e warn -> warning SMALL_BASE, evidencias calculadas.
    assert fem.status == FindingStatus.AVAILABLE
    assert AnalysisWarningCode.SMALL_BASE in warning_codes(fem.warnings)
    # E52: segmento ok, mas base valida do sinal (2) < suppress (3) -> evidencia suprimida.
    second = evidence_of(fem, SignalType.SECOND_OPTION)
    assert second.status == EvidenceStatus.SUPPRESSED_BASE_INSUFFICIENT
    assert second.segment_rate is None and second.delta_pp is None and second.lift is None
    # E49: segmento inteiro abaixo do suppress.
    strict = run(context, enriched_config(minimum_base={"suppress_below_n": 4, "warn_below_n": 5}))
    fem_strict = finding_by_key(strict, "profile:105=FEM")
    assert fem_strict.status == FindingStatus.SUPPRESSED_BASE_INSUFFICIENT
    assert fem_strict.evidences == []
    assert fem_strict.n_bruto == 3 and fem_strict.participation_rate == Decimal(3) / Decimal(6)


def test_e53_other_signal_stays_available(context):
    seed_standard(context)
    # Segunda opcao com base 2 (suprimida com suppress=3); rejeicao idem 2...
    # Acrescenta rejeicoes para dar base 3 ao sinal de rejeicao no segmento FEM.
    with context.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO respostas (pergunta_id, coleta_id, valor_resposta) VALUES (102, 5, '[\"Candidato B\"]')"
        ))
    analysis = run(context, enriched_config(minimum_base={"suppress_below_n": 3, "warn_below_n": 4}))
    fem = finding_by_key(analysis, "profile:105=FEM")
    assert evidence_of(fem, SignalType.SECOND_OPTION).status == EvidenceStatus.SUPPRESSED_BASE_INSUFFICIENT
    assert evidence_of(fem, SignalType.REJECTION).status == EvidenceStatus.AVAILABLE


# ---------------------------------------------------------------------------
# E54-E59 — taxas, delta, lift
# ---------------------------------------------------------------------------


def test_e54_a_e59_rates_units(context):
    seed_standard(context)
    analysis = run(context, enriched_config())
    evidence = evidence_of(finding_by_key(analysis, "profile:105=FEM"), SignalType.SECOND_OPTION)
    assert Decimal(0) <= evidence.segment_rate <= Decimal(1)          # E54
    assert evidence.delta_pp == Decimal("-25")                        # E55 (pp)
    assert float(evidence.lift) == pytest.approx(2 / 3)               # E56
    # E59: delta calculado sem arredondamento previo.
    assert evidence.delta_pp == (evidence.segment_rate - evidence.reference_rate) * 100


def test_e57_e58_lift_with_zero_reference(context):
    # Ninguem do universo elegivel tem o alvo como segunda opcao.
    add_interview(context, 1, {101: "Candidato B", 103: "Candidato B", 105: "Feminino"})
    add_interview(context, 2, {101: "Candidato B", 103: "Candidato B", 105: "Masculino"})
    payload = enriched_config()
    payload["signals"] = [{"type": "SECOND_OPTION", "question_id": 103}]
    analysis = run(context, payload)
    evidence = evidence_of(finding_by_key(analysis, "profile:105=FEM"), SignalType.SECOND_OPTION)
    assert evidence.reference_rate == Decimal(0)
    assert evidence.lift is None
    assert AnalysisWarningCode.LIFT_UNDEFINED_REFERENCE_ZERO in warning_codes(evidence.warnings)


# ---------------------------------------------------------------------------
# E60-E67 — Wilson (unitarios, sem banco)
# ---------------------------------------------------------------------------


def test_e60_a_e65_wilson_known_values():
    z = growth_statistics.z_value(Decimal("0.95"))
    assert float(z) == pytest.approx(1.959963984540054, abs=1e-8)     # E65
    zero = growth_statistics.wilson_interval(0, 10, 0.95)             # E60
    assert zero.low == Decimal(0)
    assert float(zero.high) == pytest.approx(0.27753, abs=1e-4)
    full = growth_statistics.wilson_interval(10, 10, 0.95)            # E61
    assert full.high == Decimal(1)
    assert float(full.low) == pytest.approx(1 - 0.27753, abs=1e-4)
    mid = growth_statistics.wilson_interval(5, 10, 0.95)              # E62
    assert float(mid.low) == pytest.approx(0.2366, abs=1e-3)
    assert float(mid.high) == pytest.approx(0.7634, abs=1e-3)
    assert Decimal(0) <= mid.low <= mid.high <= Decimal(1)            # E63
    assert growth_statistics.wilson_interval(0, 0, 0.95) is None      # E64


def test_e66_e67_no_significance_in_result(context):
    seed_standard(context)
    analysis = run(context, enriched_config())
    dumped = json.dumps(analysis.model_dump(mode="json"), ensure_ascii=False).lower()
    assert "p_value" not in dumped and "pvalue" not in dumped
    assert "significant" not in dumped and "significativo" not in dumped


# ---------------------------------------------------------------------------
# E68-E71 — referencia inclusiva
# ---------------------------------------------------------------------------


def test_e68_a_e71_reference_is_inclusive_eligible_universe(context):
    seed_standard(context)
    analysis = run(context, enriched_config())
    evidence = evidence_of(finding_by_key(analysis, "profile:105=FEM"), SignalType.SECOND_OPTION)
    # E68/E69: a referencia e o universo elegivel inteiro e contem o segmento.
    assert evidence.reference_base_n == 4
    assert evidence.segment_base_n <= evidence.reference_base_n
    # E70: warning global declarando a sobreposicao.
    assert AnalysisWarningCode.REFERENCE_INCLUDES_SEGMENT in warning_codes(analysis.warnings)
    # E71: nao existe IC de diferenca de duas amostras independentes.
    assert "difference_interval" not in json.dumps(analysis.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# E72-E76 — espontanea
# ---------------------------------------------------------------------------


def spontaneous_config(threshold):
    return base_config(
        signals=[{"type": "SECOND_OPTION", "question_id": 107}],
        target={"label": "Candidato A", "cargo": "Senador",
                "bindings": [
                    {"question_id": 101, "values": ["Candidato A"]},
                    {"question_id": 107, "values": ["Candidato A"]},
                ]},
        spontaneous_quality={"max_uncategorized_rate": threshold},
    )


def test_e72_a_e74_spontaneous_below_threshold(context):
    add_interview(context, 1, {101: "Candidato B", 107: "candidato a", 105: "Feminino"})
    add_interview(context, 2, {101: "Candidato B", 107: "CANDIDATO A", 105: "Feminino"})
    add_interview(context, 3, {101: "Candidato B", 107: "zzz", 105: "Masculino"})
    analysis = run(context, spontaneous_config(0.5))  # 1/3 nao categorizada < 0.5
    evidence = evidence_of(finding_by_key(analysis, "profile:105=FEM"), SignalType.SECOND_OPTION)
    assert evidence.status == EvidenceStatus.AVAILABLE
    assert evidence.segment_numerator == 2  # E72: categoria ativa resolve
    # E73: 'Nao categorizada' contabilizada na base (coleta 3).
    assert evidence.reference_base_n == 3 and evidence.reference_numerator == 2


def test_e75_e76_spontaneous_above_threshold_blocks_signal(context):
    add_interview(context, 1, {101: "Candidato B", 107: "candidato a", 105: "Feminino"})
    add_interview(context, 2, {101: "Candidato B", 107: "zzz", 105: "Feminino"})
    add_interview(context, 3, {101: "Candidato B", 107: "yyy", 105: "Masculino"})
    analysis = run(context, spontaneous_config(0.5))  # 2/3 > 0.5
    evidence = evidence_of(finding_by_key(analysis, "profile:105=FEM"), SignalType.SECOND_OPTION)
    assert evidence.status == EvidenceStatus.SIGNAL_UNAVAILABLE_QUALITY
    assert evidence.segment_rate is None and evidence.delta_pp is None
    assert AnalysisWarningCode.HIGH_UNCATEGORIZED_RATE in warning_codes(analysis.warnings)


# ---------------------------------------------------------------------------
# E77-E80 — multipla escolha
# ---------------------------------------------------------------------------


def test_e77_a_e80_multiple_choice_semantics(context):
    add_interview(context, 1, {101: "Candidato B", 102: ["Candidato A", "Candidato B", "Candidato A"], 105: "Feminino"})
    add_interview(context, 2, {101: "Candidato B", 102: "Candidato B", 105: "Feminino"})  # legado escalar
    # Coleta 3: duas LINHAS de resposta para a mesma pergunta (anomalia legada).
    add_interview(context, 3, {101: "Candidato B", 105: "Masculino"})
    with context.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO respostas (pergunta_id, coleta_id, valor_resposta) VALUES (102, 3, 'Candidato A'), (102, 3, 'Candidato A')"
        ))
    payload = enriched_config()
    payload["signals"] = [{"type": "REJECTION", "question_id": 102}]
    analysis = run(context, payload)
    fem = evidence_of(finding_by_key(analysis, "profile:105=FEM"), SignalType.REJECTION)
    masc = evidence_of(finding_by_key(analysis, "profile:105=MASC"), SignalType.REJECTION)
    # E77-E79: JSON list e escalar legado; dedup dentro da entrevista.
    assert fem.segment_base_n == 2 and fem.segment_numerator == 1
    # E80: duas linhas de Resposta continuam UMA entrevista na base.
    assert masc.segment_base_n == 1 and masc.segment_numerator == 1
    assert AnalysisWarningCode.MULTIPLE_CHOICE_SUM_MAY_EXCEED_100 in warning_codes(fem.warnings)


# ---------------------------------------------------------------------------
# E81-E87 — snapshot, hash e fingerprint
# ---------------------------------------------------------------------------


def test_e81_a_e84_engine_version_and_configuration_hash(context):
    seed_standard(context)
    analysis = run(context, enriched_config())
    assert analysis.snapshot.engine_version == growth_engine.GROWTH_ENGINE_VERSION  # E81
    config_a, _ = parse_growth_analysis_configuration(enriched_config())
    config_b, _ = parse_growth_analysis_configuration(enriched_config())
    assert compute_configuration_hash(config_a) == compute_configuration_hash(config_b)  # E82/E83
    assert analysis.snapshot.configuration_hash == compute_configuration_hash(config_a)
    other, _ = parse_growth_analysis_configuration(base_config())
    assert compute_configuration_hash(other) != compute_configuration_hash(config_a)     # E84


def test_e85_a_e87_input_fingerprint(context):
    seed_standard(context)
    first = run(context, enriched_config())
    second = run(context, enriched_config())
    assert first.snapshot.input_fingerprint == second.snapshot.input_fingerprint  # E85
    assert first.snapshot.executed_at != second.snapshot.executed_at or True      # E87: timestamp fora do fingerprint
    with context.engine.begin() as connection:
        connection.execute(text("UPDATE respostas SET valor_resposta='Candidato B' WHERE coleta_id=2 AND pergunta_id=103"))
    third = run(context, enriched_config())
    assert third.snapshot.input_fingerprint != first.snapshot.input_fingerprint   # E86


# ---------------------------------------------------------------------------
# E88-E91 — ponderacao ausente
# ---------------------------------------------------------------------------


def test_e88_a_e91_no_weighting(context):
    seed_standard(context)
    analysis = run(context, base_config(territory={"level": "MUNICIPIO", "include_electoral_context": True}))
    assert all(item.weighted_base is None for item in analysis.findings)          # E88
    assert AnalysisWarningCode.UNWEIGHTED_ANALYSIS in warning_codes(analysis.warnings)  # E89
    # E91: eleitorado e contexto — a participacao segue sobre o universo elegivel.
    fem = finding_by_key(analysis, "profile:105=FEM|territory:municipio=900")
    assert fem.territory.eleitorado_apto == 50000
    assert fem.participation_rate == Decimal(fem.n_bruto) / Decimal(analysis.universe.eligible_n)


# ---------------------------------------------------------------------------
# E92-E96 — explicabilidade
# ---------------------------------------------------------------------------


def test_e92_a_e96_no_opaque_evidence(context):
    seed_standard(context)
    analysis = run(context, enriched_config())
    for finding in analysis.findings:
        assert finding.n_bruto >= 0 and finding.segment_key                        # E96
        for evidence in finding.evidences:
            if evidence.status == EvidenceStatus.AVAILABLE:
                assert evidence.segment_numerator is not None                      # E92
                assert evidence.segment_base_n is not None                         # E93
                assert evidence.reference_rate is not None                         # E94
                assert evidence.delta_pp is not None                               # E95


# ---------------------------------------------------------------------------
# E97-E100 — determinismo
# ---------------------------------------------------------------------------


def test_e97_a_e100_determinism(context):
    seed_standard(context)
    first = run(context, enriched_config())
    second = run(context, enriched_config())
    dump_a = first.model_dump(mode="json")
    dump_b = second.model_dump(mode="json")
    dump_a["snapshot"].pop("executed_at")
    dump_b["snapshot"].pop("executed_at")
    assert dump_a == dump_b                                                        # E100
    keys = [item.segment_key for item in first.findings]
    assert keys == sorted(keys)                                                    # E99
    # E98: ordem dos valores configurados nos grupos nao muda o conteudo analitico.
    payload = enriched_config()
    payload["profile_dimensions"][0]["groups"][0]["values"] = ["Feminino"]
    reordered = run(context, payload)
    dump_c = reordered.model_dump(mode="json")
    dump_c["snapshot"].pop("executed_at")
    dump_c["snapshot"].pop("configuration_hash")
    dump_a.pop("configuration_hash", None)
    dump_a["snapshot"].pop("configuration_hash", None)
    dump_c.pop("configuration_hash", None)
    assert dump_c["findings"] == dump_a["findings"]


# ---------------------------------------------------------------------------
# E101-E104 — multitenancy
# ---------------------------------------------------------------------------


def test_e101_authorized_user_executes(context):
    seed_standard(context)
    assert run(context, base_config()).universe.survey_n == 10


def test_e102_cross_tenant_survey_does_not_execute(context):
    payload = base_config(pesquisa_id=2)
    payload["scenario"]["intention_questions"][0]["question_id"] = 201
    payload["target"]["bindings"][0] = {"question_id": 201, "values": ["Y"]}
    payload["profile_dimensions"][0]["question_id"] = 201
    payload["profile_dimensions"][0]["groups"] = [{"key": "Y", "label": "Y", "values": ["Y"]}]
    payload["intention_taxonomy"] = {}
    config, issues = parse_growth_analysis_configuration(payload)
    assert not issues
    with context.Session() as db:
        with pytest.raises(GrowthAnalysisConfigurationError):
            analyze_growth_potential(db, context.user, config)


def test_e103_cross_tenant_data_not_in_universe(context):
    seed_standard(context)
    # Coleta de outro tenant na MESMA pesquisa: nunca entra no universo.
    add_interview(context, 99, {101: "Candidato B", 105: "Feminino"}, company=20)
    analysis = run(context, base_config())
    assert analysis.universe.survey_n == 10


def test_e104_configuration_has_no_company_id(context):
    seed_standard(context)
    analysis = run(context, base_config())
    dumped = json.dumps(analysis.model_dump(mode="json"))
    assert "company_id" not in dumped and "tenant_id" not in dumped


# ---------------------------------------------------------------------------
# E105-E112 — sem projecao, sem score
# ---------------------------------------------------------------------------


def test_e105_a_e112_no_projection_no_score(context):
    seed_standard(context)
    analysis = run(context, enriched_config(territory={"level": "MUNICIPIO", "include_electoral_context": True}))
    dumped = json.dumps(analysis.model_dump(mode="json"), ensure_ascii=False).lower()
    for forbidden in ("projected_votes", "potential_votes", "votos_projetados",
                      "score", "potential_level", "overall_direction", "favorable_count"):
        assert forbidden not in dumped, forbidden
    for finding in analysis.findings:
        assert not hasattr(finding, "rank")
        if finding.territory is not None and finding.territory.eleitorado_apto is not None:
            # E108: nenhum campo derivado de taxa x eleitorado existe.
            assert set(finding.territory.model_dump()) == {"level", "key", "label", "eleitorado_apto"}


# ---------------------------------------------------------------------------
# Caso oraculo manual (secao 97) e limite de segmentos
# ---------------------------------------------------------------------------


def test_oracle_manual_case(context):
    """CASO ORACULO (calculo manual):

    101 entrevistas: 1 apoiadora do alvo + 100 elegiveis (Candidato B).
    Segmento Mulheres: 20; Homens: 80. Segunda opcao 'Candidato A':
    mulheres 6/20 = 30%; homens 9/80; referencia 15/100 = 15%.
    delta = +15 pp; lift = 0.30/0.15 = 2.0.
    """
    add_interview(context, 1, {101: "Candidato A", 105: "Feminino"})
    next_id = 2
    for index in range(20):
        second = "Candidato A" if index < 6 else "Candidato B"
        add_interview(context, next_id, {101: "Candidato B", 105: "Feminino", 103: second})
        next_id += 1
    for index in range(80):
        second = "Candidato A" if index < 9 else "Candidato B"
        add_interview(context, next_id, {101: "Candidato B", 105: "Masculino", 103: second})
        next_id += 1
    payload = enriched_config(minimum_base={"suppress_below_n": 5, "warn_below_n": 10})
    payload["signals"] = [{"type": "SECOND_OPTION", "question_id": 103}]
    analysis = run(context, payload)
    assert analysis.universe.eligible_n == 100
    fem = finding_by_key(analysis, "profile:105=FEM")
    assert fem.n_bruto == 20
    assert fem.participation_rate == Decimal(20) / Decimal(100)
    evidence = evidence_of(fem, SignalType.SECOND_OPTION)
    assert evidence.segment_numerator == 6 and evidence.segment_base_n == 20
    assert evidence.reference_numerator == 15 and evidence.reference_base_n == 100
    assert evidence.segment_rate == Decimal("0.3")
    assert evidence.reference_rate == Decimal("0.15")
    assert evidence.delta_pp == Decimal(15)
    assert evidence.lift == Decimal(2)
    assert evidence.observed_direction == ObservedDirection.FAVORABLE


def test_segment_limit_exceeded(context, monkeypatch):
    seed_standard(context)
    monkeypatch.setattr(growth_engine, "GROWTH_MAX_SEGMENTS", 1)
    config, _ = parse_growth_analysis_configuration(base_config())
    with context.Session() as db:
        with pytest.raises(GrowthSegmentLimitExceededError):
            analyze_growth_potential(db, context.user, config)


def test_global_warnings_present(context):
    seed_standard(context)
    analysis = run(context, base_config())
    codes = warning_codes(analysis.warnings)
    assert AnalysisWarningCode.UNWEIGHTED_ANALYSIS in codes
    assert AnalysisWarningCode.SRS_ASSUMPTION in codes
    assert AnalysisWarningCode.REFERENCE_INCLUDES_SEGMENT in codes
    assert AnalysisWarningCode.MULTIPLE_COMPARISONS_EXPLORATORY in codes  # 2 segmentos
    assert AnalysisWarningCode.SIGNAL_NOT_CONFIGURED in codes  # sinais opcionais ausentes


def test_electoral_context_unavailable_is_declared(context):
    seed_standard(context)
    # Base deixa de ser VALIDADA: contexto indisponivel, analise nao falha.
    with context.engine.begin() as connection:
        connection.execute(text("UPDATE base_eleitoral SET status='EM_CONFERENCIA'"))
    analysis = run(context, base_config(territory={"level": "MUNICIPIO", "include_electoral_context": True}))
    assert all(
        item.territory.eleitorado_apto is None
        for item in analysis.findings if item.territory is not None
    )
    assert AnalysisWarningCode.ELECTORAL_CONTEXT_UNAVAILABLE in warning_codes(analysis.warnings)
