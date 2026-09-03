"""Testes do contrato de configuracao do Potencial de Crescimento (Prompt 02).

Cobre os casos C01-C65 do plano: estrutura (Pydantic puro) e dominio (SQLite
com o mesmo harness de test_multidimensional_cross).

IMPORTANTE: os limiares de base minima usados aqui (10/30) sao VALORES
SINTETICOS DE TESTE, nunca defaults metodologicos de producao (D04).
"""

import copy
import json
import os
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SECRET_KEY", "test-only-growth-config-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.inteligencia_eleitoral import (
    GrowthAnalysisConfiguration,
    GrowthConfigErrorCode,
    GrowthConfigWarningCode,
    MinimumBasePolicy,
    SignalType,
    WeightingMode,
    parse_growth_analysis_configuration,
    validate_growth_configuration_payload,
)
from tests.acl_fixture import criar_tabelas_acl


# ---------------------------------------------------------------------------
# Harness de dominio (SQLite, mesmo padrao de test_multidimensional_cross)
# ---------------------------------------------------------------------------


@pytest.fixture
def context():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def sqlite_functions(connection, _):
        # Shim minimo de PostGIS: as entidades carregam colunas Geometry via
        # AsEWKB mesmo quando a validacao nao usa geometria.
        connection.create_function("AsEWKB", 1, lambda value: value)
        connection.create_function("ST_GeomFromText", 2, lambda value, srid: value)

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
            "CREATE TABLE setores (id INTEGER PRIMARY KEY, nome TEXT, meta INTEGER, tolerancia INTEGER, finalidade TEXT, geometria TEXT, pesquisa_id INTEGER, agente_id INTEGER, municipio_territorio_id INTEGER)",
            "CREATE TABLE categorias_resposta_espontanea (id INTEGER PRIMARY KEY, pesquisa_id INTEGER, nome TEXT, nome_normalizado TEXT, ativo BOOLEAN, criado_por_id INTEGER, atualizado_por_id INTEGER, criado_em DATETIME, atualizado_em DATETIME)",
            "CREATE TABLE mapeamentos_resposta_espontanea (id INTEGER PRIMARY KEY, pesquisa_id INTEGER, categoria_id INTEGER, chave_normalizada TEXT, texto_referencia TEXT, ativo BOOLEAN, criado_por_id INTEGER, atualizado_por_id INTEGER, criado_em DATETIME, atualizado_em DATETIME)",
            "CREATE TABLE territorio_eleitoral (id INTEGER PRIMARY KEY, base_eleitoral_id INTEGER, parent_id INTEGER, tipo TEXT, codigo TEXT, nome TEXT, nome_normalizado TEXT, municipio_id INTEGER, zona_eleitoral INTEGER, numero_secao INTEGER, eleitorado_apto INTEGER, eleitorado_apto_origem TEXT, eleitorado_apto_divergente BOOLEAN, status_validacao TEXT, geometria TEXT, metadados JSON)",
            "CREATE TABLE setor_territorio_eleitoral (id INTEGER PRIMARY KEY, setor_id INTEGER, territorio_eleitoral_id INTEGER, criado_em DATETIME)",
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
        # Pesquisa 1 e 3 pertencem ao tenant 10; pesquisa 2 ao tenant 20.
        connection.execute(text(
            "INSERT INTO pesquisas VALUES (1, 'Pesquisa A', NULL, 1, 1, NULL, NULL), "
            "(2, 'Pesquisa B', NULL, 1, 2, NULL, NULL), (3, 'Pesquisa A2', NULL, 1, 1, NULL, NULL)"
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
                (108, 'Territorial',   'ESCOLHA_SIMPLES',  8, 0, 0, NULL,            '{}', 1, 1, 'TERRITORIAL'),
                (109, 'Inativa',       'ESCOLHA_SIMPLES',  9, 0, 0, NULL,            '{}', 0, 1, 'GLOBAL'),
                (110, 'Texto livre',   'TEXTO',           10, 0, 0, NULL,            '{}', 1, 1, 'GLOBAL'),
                (201, 'Outro tenant',  'ESCOLHA_SIMPLES',  1, 0, 0, NULL,            '{}', 1, 2, 'GLOBAL'),
                (301, 'Outra pesquisa','ESCOLHA_SIMPLES',  1, 0, 0, NULL,            '{}', 1, 3, 'GLOBAL')
        """))
        opcoes = [
            (101, ["Candidato A", "Candidato B", "Indeciso", "Branco/Nulo", "NS/NR", "Não pretende votar"]),
            (102, ["Candidato A", "Candidato B"]),
            (103, ["Candidato A", "Candidato B", "NS/NR"]),
            (104, ["Definitivo", "Pode mudar"]),
            (105, ["Feminino", "Masculino"]),
            (108, ["Sim", "Não"]),
            (201, ["Y"]),
            (301, ["X"]),
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
        connection.execute(text(
            "INSERT INTO categorias_resposta_espontanea (id, pesquisa_id, nome, nome_normalizado, ativo, criado_por_id, atualizado_por_id) "
            "VALUES (1, 1, 'Candidato A', 'candidato a', 1, 1, 1), (2, 1, 'Inativa', 'inativa', 0, 1, 1)"
        ))
        connection.execute(text(
            "INSERT INTO territorio_eleitoral (id, base_eleitoral_id, tipo, nome) VALUES (900, 1, 'MUNICIPIO', 'Macapá')"
        ))
        connection.execute(text(
            "INSERT INTO setores (id, nome, meta, tolerancia, finalidade, geometria, pesquisa_id, agente_id, municipio_territorio_id) VALUES "
            "(11, 'Centro', 10, 50, 'AMBOS', NULL, 1, NULL, 900), "
            "(12, 'Norte', 10, 50, 'OPERACAO', NULL, 1, NULL, NULL), "
            "(21, 'Alheio', 10, 50, 'AMBOS', NULL, 2, NULL, NULL)"
        ))

    user = SimpleNamespace(id=1, company_id=10, ativo=True, perfil_id=1)
    yield SimpleNamespace(Session=Session, user=user, engine=engine)
    engine.dispose()


def base_config(**overrides):
    """Configuracao minima valida (SINGLE + 1 dimensao de perfil).

    minimum_base 10/30: VALOR SINTETICO DE TESTE, nao default de producao.
    """
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
        "minimum_base": {"suppress_below_n": 10, "warn_below_n": 30},
        "reference": {"type": "ELIGIBLE_UNIVERSE"},
        "uncertainty": {"method": "WILSON_AAS_APPROX", "confidence_level": 0.95},
    }
    payload.update(copy.deepcopy(overrides))
    return payload


def enriched_signals():
    return [
        {"type": "REJECTION", "question_id": 102},
        {"type": "SECOND_OPTION", "question_id": 103},
        {
            "type": "VOTE_DECISION",
            "question_id": 104,
            "decision_groups": {"mobile": ["Pode mudar"], "crystallized": ["Definitivo"]},
        },
    ]


def enriched_bindings():
    return [
        {"question_id": 101, "values": ["Candidato A"]},
        {"question_id": 102, "values": ["Candidato A"]},
        {"question_id": 103, "values": ["Candidato A"]},
    ]


def validate(context, payload):
    with context.Session() as db:
        return validate_growth_configuration_payload(db, context.user, payload)


def error_codes(result):
    return {issue.code for issue in result.errors}


def warning_codes(result):
    return {issue.code for issue in result.warnings}


def structural_errors(payload):
    config, issues = parse_growth_analysis_configuration(payload)
    assert config is None
    return {issue.code for issue in issues}


# ---------------------------------------------------------------------------
# C01-C10 — estrutura basica
# ---------------------------------------------------------------------------


def test_c01_minimal_configuration_is_valid(context):
    result = validate(context, base_config())
    assert result.valid, result.errors
    assert result.normalized_configuration is not None
    # C24-C26: sinais opcionais ausentes geram warnings, nunca erro.
    assert {
        GrowthConfigWarningCode.NO_REJECTION,
        GrowthConfigWarningCode.NO_SECOND_OPTION,
        GrowthConfigWarningCode.NO_VOTE_DECISION,
        GrowthConfigWarningCode.UNWEIGHTED_ANALYSIS,  # C55
    } <= warning_codes(result)


def test_c02_unsupported_schema_version():
    codes = structural_errors(base_config(schema_version=2))
    assert GrowthConfigErrorCode.UNSUPPORTED_SCHEMA_VERSION in codes


def test_c03_pesquisa_id_required():
    payload = base_config()
    del payload["pesquisa_id"]
    config, issues = parse_growth_analysis_configuration(payload)
    assert config is None and issues


def test_c04_company_id_is_rejected():
    codes = structural_errors(base_config(company_id=10))
    assert GrowthConfigErrorCode.UNKNOWN_FIELD in codes


def test_c05_intention_is_mandatory():
    payload = base_config()
    del payload["scenario"]
    config, issues = parse_growth_analysis_configuration(payload)
    assert config is None and issues
    payload = base_config()
    payload["scenario"]["intention_questions"] = []
    config, issues = parse_growth_analysis_configuration(payload)
    assert config is None and issues


def test_c06_zero_profile_dimensions_is_error():
    codes = structural_errors(base_config(profile_dimensions=[]))
    assert GrowthConfigErrorCode.PROFILE_DIMENSION_REQUIRED in codes


def test_c07_c08_one_and_two_profile_dimensions_are_valid(context):
    assert validate(context, base_config()).valid
    payload = base_config()
    payload["profile_dimensions"].append({
        "question_id": 106,
        "label": "Idade",
        "mode": "NUMERIC_RANGES",
        "ranges": [
            {"key": "18_24", "label": "18–24", "min": 18, "max": 24},
            {"key": "25_59", "label": "25–59", "min": 25, "max": 59},
            {"key": "60_MAIS", "label": "60+", "min": 60, "max": None},
        ],
    })
    result = validate(context, payload)
    assert result.valid, result.errors  # tambem cobre C43


def test_c09_three_profile_dimensions_is_error():
    payload = base_config()
    payload["profile_dimensions"] = [
        {"question_id": qid, "label": f"D{qid}", "mode": "CATEGORICAL",
         "groups": [{"key": "G", "label": "G", "values": ["X"]}]}
        for qid in (105, 106, 110)
    ]
    codes = structural_errors(payload)
    assert GrowthConfigErrorCode.TOO_MANY_PROFILE_DIMENSIONS in codes


def test_c10_weighting_other_than_nao_ponderado_is_error():
    codes = structural_errors(base_config(weighting={"mode": "PONDERADO"}))
    assert GrowthConfigErrorCode.UNSUPPORTED_WEIGHTING_MODE in codes


# ---------------------------------------------------------------------------
# C11-C14 — base minima
# ---------------------------------------------------------------------------


def test_c11_suppress_must_be_at_least_one():
    payload = base_config(minimum_base={"suppress_below_n": 0, "warn_below_n": 30})
    config, issues = parse_growth_analysis_configuration(payload)
    assert config is None and issues


def test_c12_c13_warn_must_exceed_suppress():
    ok, issues = parse_growth_analysis_configuration(
        base_config(minimum_base={"suppress_below_n": 10, "warn_below_n": 11})
    )
    assert ok is not None and not issues
    codes = structural_errors(
        base_config(minimum_base={"suppress_below_n": 10, "warn_below_n": 10})
    )
    assert GrowthConfigErrorCode.INVALID_BASE_THRESHOLDS in codes


def test_c14_no_hidden_numeric_defaults():
    with pytest.raises(Exception):
        MinimumBasePolicy()
    fields = GrowthAnalysisConfiguration.model_fields
    assert fields["minimum_base"].is_required()
    assert MinimumBasePolicy.model_fields["suppress_below_n"].is_required()
    assert MinimumBasePolicy.model_fields["warn_below_n"].is_required()


# ---------------------------------------------------------------------------
# C15-C18 — taxonomia
# ---------------------------------------------------------------------------


def test_c15_c16_special_classes_must_be_disjoint():
    payload = base_config()
    payload["intention_taxonomy"]["indeciso_declarado"] = ["NS/NR"]
    codes = structural_errors(payload)
    assert GrowthConfigErrorCode.OVERLAPPING_TAXONOMY_VALUES in codes


def test_c17_target_value_cannot_be_in_taxonomy():
    payload = base_config()
    payload["intention_taxonomy"]["indeciso_declarado"] = ["Candidato A", "Indeciso"]
    codes = structural_errors(payload)
    assert GrowthConfigErrorCode.TARGET_OVERLAPS_TAXONOMY in codes


def test_c18_technical_no_answer_cannot_be_configured():
    payload = base_config()
    payload["intention_taxonomy"]["ns_nr"] = ["__SEM_RESPOSTA__"]
    codes = structural_errors(payload)
    assert GrowthConfigErrorCode.RESERVED_VALUE in codes
    payload = base_config()
    payload["target"]["bindings"][0]["values"] = ["__SEM_RESPOSTA__"]
    codes = structural_errors(payload)
    assert GrowthConfigErrorCode.RESERVED_VALUE in codes


# ---------------------------------------------------------------------------
# C19-C23 — cenario
# ---------------------------------------------------------------------------


def test_c19_single_scenario_valid(context):
    assert validate(context, base_config()).valid


def test_c20_multiple_scenario_valid(context):
    payload = base_config(
        scenario={
            "label": "Senado",
            "ballot_selection_mode": "MULTIPLE",
            "intention_questions": [{"question_id": 102, "slot": "VOTOS"}],
        },
        target={
            "label": "Candidato A",
            "cargo": "Senador",
            "bindings": [{"question_id": 102, "values": ["Candidato A"]}],
        },
        intention_taxonomy={},
    )
    result = validate(context, payload)
    assert result.valid, result.errors


def test_c21_ordered_multiple_scenario_valid():
    payload = base_config(
        scenario={
            "label": "Senado — dois votos",
            "ballot_selection_mode": "ORDERED_MULTIPLE",
            "intention_questions": [
                {"question_id": 101, "slot": "PRIMEIRO_VOTO", "order": 1},
                {"question_id": 103, "slot": "SEGUNDO_VOTO", "order": 2},
            ],
        },
    )
    config, issues = parse_growth_analysis_configuration(payload)
    assert config is not None and not issues


def test_c22_ordered_multiple_without_coherent_slots_is_error():
    payload = base_config(
        scenario={
            "label": "Senado",
            "ballot_selection_mode": "ORDERED_MULTIPLE",
            "intention_questions": [
                {"question_id": 101, "slot": "PRIMEIRO_VOTO"},
                {"question_id": 103, "slot": "SEGUNDO_VOTO"},
            ],
        },
    )
    codes = structural_errors(payload)
    assert GrowthConfigErrorCode.SCENARIO_SLOTS_INCOHERENT in codes


def test_c23_single_mode_with_multiple_choice_question_is_domain_error(context):
    payload = base_config(
        scenario={
            "label": "Cenário",
            "ballot_selection_mode": "SINGLE",
            "intention_questions": [{"question_id": 102, "slot": "VOTO"}],
        },
        target={
            "label": "Candidato A",
            "cargo": "Senador",
            "bindings": [{"question_id": 102, "values": ["Candidato A"]}],
        },
        intention_taxonomy={},
    )
    result = validate(context, payload)
    assert not result.valid
    assert GrowthConfigErrorCode.INCOMPATIBLE_QUESTION_TYPE in error_codes(result)


# ---------------------------------------------------------------------------
# C24-C30 — sinais
# ---------------------------------------------------------------------------


def test_c24_c25_c26_absent_optional_signals_warn(context):
    result = validate(context, base_config())
    assert result.valid
    assert GrowthConfigWarningCode.NO_REJECTION in warning_codes(result)
    assert GrowthConfigWarningCode.NO_SECOND_OPTION in warning_codes(result)
    assert GrowthConfigWarningCode.NO_VOTE_DECISION in warning_codes(result)


def test_c27_territorial_question_cannot_be_signal(context):
    payload = base_config(
        signals=[{"type": "REJECTION", "question_id": 108}],
        target={
            "label": "Candidato A",
            "cargo": "Senador",
            "bindings": [
                {"question_id": 101, "values": ["Candidato A"]},
                {"question_id": 108, "values": ["Sim"]},
            ],
        },
    )
    result = validate(context, payload)
    assert GrowthConfigErrorCode.TERRITORIAL_SIGNAL_NOT_ALLOWED in error_codes(result)


def test_c28_question_from_other_survey_is_error(context):
    payload = base_config(
        signals=[{"type": "REJECTION", "question_id": 301}],
        target={
            "label": "Candidato A",
            "cargo": "Senador",
            "bindings": [
                {"question_id": 101, "values": ["Candidato A"]},
                {"question_id": 301, "values": ["X"]},
            ],
        },
    )
    result = validate(context, payload)
    assert GrowthConfigErrorCode.QUESTION_FROM_OTHER_SURVEY in error_codes(result)


def test_c29_inactive_question_is_error(context):
    payload = base_config()
    payload["profile_dimensions"][0]["question_id"] = 109
    result = validate(context, payload)
    assert GrowthConfigErrorCode.QUESTION_INACTIVE in error_codes(result)


def test_c30_incompatible_type_is_error(context):
    payload = base_config(
        signals=[{"type": "REJECTION", "question_id": 110}],
        target={
            "label": "Candidato A",
            "cargo": "Senador",
            "bindings": [
                {"question_id": 101, "values": ["Candidato A"]},
                {"question_id": 110, "values": ["qualquer"]},
            ],
        },
    )
    result = validate(context, payload)
    assert GrowthConfigErrorCode.INCOMPATIBLE_QUESTION_TYPE in error_codes(result)


# ---------------------------------------------------------------------------
# C31-C36 — bindings da candidatura
# ---------------------------------------------------------------------------


def test_c31_enriched_configuration_with_bindings_is_valid(context):
    payload = base_config(signals=enriched_signals())
    payload["target"]["bindings"] = enriched_bindings()
    result = validate(context, payload)
    assert result.valid, result.errors
    codes = warning_codes(result)
    assert GrowthConfigWarningCode.NO_REJECTION not in codes
    assert GrowthConfigWarningCode.NO_SECOND_OPTION not in codes
    assert GrowthConfigWarningCode.NO_VOTE_DECISION not in codes


def test_c32_unknown_target_value_is_error(context):
    payload = base_config()
    payload["target"]["bindings"][0]["values"] = ["Candidato Z"]
    result = validate(context, payload)
    assert GrowthConfigErrorCode.TARGET_VALUE_NOT_FOUND in error_codes(result)


def test_c33_rejection_without_target_binding_is_error(context):
    payload = base_config(signals=[{"type": "REJECTION", "question_id": 102}])
    result = validate(context, payload)
    assert GrowthConfigErrorCode.MISSING_TARGET_BINDING in error_codes(result)


def test_c34_second_option_without_target_binding_is_error(context):
    payload = base_config(signals=[{"type": "SECOND_OPTION", "question_id": 103}])
    result = validate(context, payload)
    assert GrowthConfigErrorCode.MISSING_TARGET_BINDING in error_codes(result)


def test_c35_vote_decision_does_not_require_target_binding(context):
    payload = base_config(signals=[{
        "type": "VOTE_DECISION",
        "question_id": 104,
        "decision_groups": {"mobile": ["Pode mudar"], "crystallized": ["Definitivo"]},
    }])
    result = validate(context, payload)
    assert result.valid, result.errors
    assert GrowthConfigErrorCode.MISSING_TARGET_BINDING not in error_codes(result)


def test_c36_unused_binding_is_warning(context):
    # Regra documentada: binding sem sinal correspondente e WARNING, nao erro.
    payload = base_config()
    payload["target"]["bindings"].append({"question_id": 104, "values": ["Definitivo"]})
    result = validate(context, payload)
    assert result.valid, result.errors
    assert GrowthConfigWarningCode.UNUSED_TARGET_BINDING in warning_codes(result)


# ---------------------------------------------------------------------------
# C37-C41 — espontanea
# ---------------------------------------------------------------------------


def spontaneous_payload(values, quality={"max_uncategorized_rate": 0.2}):
    overrides = {
        "signals": [{"type": "SECOND_OPTION", "question_id": 107}],
        "target": {
            "label": "Candidato A",
            "cargo": "Senador",
            "bindings": [
                {"question_id": 101, "values": ["Candidato A"]},
                {"question_id": 107, "values": values},
            ],
        },
    }
    if quality is not None:
        overrides["spontaneous_quality"] = quality
    return base_config(**overrides)


def test_c37_active_spontaneous_category_is_valid(context):
    result = validate(context, spontaneous_payload(["Candidato A"]))
    assert result.valid, result.errors
    assert GrowthConfigWarningCode.SPONTANEOUS_SIGNAL in warning_codes(result)


def test_c38_unknown_spontaneous_category_is_error(context):
    result = validate(context, spontaneous_payload(["Categoria X"]))
    assert GrowthConfigErrorCode.TARGET_VALUE_NOT_FOUND in error_codes(result)


def test_c39_inactive_spontaneous_category_is_error(context):
    result = validate(context, spontaneous_payload(["Inativa"]))
    assert GrowthConfigErrorCode.TARGET_VALUE_NOT_FOUND in error_codes(result)


def test_c40_spontaneous_signal_without_quality_policy_is_error(context):
    result = validate(context, spontaneous_payload(["Candidato A"], quality=None))
    assert GrowthConfigErrorCode.SPONTANEOUS_POLICY_REQUIRED in error_codes(result)


def test_c41_quality_threshold_out_of_range_is_error():
    codes = structural_errors(
        spontaneous_payload(["Candidato A"], quality={"max_uncategorized_rate": 1.5})
    )
    assert GrowthConfigErrorCode.INVALID_SPONTANEOUS_THRESHOLD in codes


# ---------------------------------------------------------------------------
# C42-C47 — perfil
# ---------------------------------------------------------------------------


def test_c42_categorical_dimension_valid(context):
    assert validate(context, base_config()).valid


def test_c44_inverted_range_is_error():
    payload = base_config(profile_dimensions=[{
        "question_id": 106,
        "label": "Idade",
        "mode": "NUMERIC_RANGES",
        "ranges": [{"key": "X", "label": "X", "min": 30, "max": 20}],
    }])
    codes = structural_errors(payload)
    assert GrowthConfigErrorCode.INVALID_NUMERIC_RANGE in codes


def test_c45_overlapping_ranges_is_error():
    payload = base_config(profile_dimensions=[{
        "question_id": 106,
        "label": "Idade",
        "mode": "NUMERIC_RANGES",
        "ranges": [
            {"key": "A", "label": "A", "min": 18, "max": 24},
            {"key": "B", "label": "B", "min": 24, "max": 34},
        ],
    }])
    codes = structural_errors(payload)
    assert GrowthConfigErrorCode.OVERLAPPING_NUMERIC_RANGES in codes


def test_c46_dimension_from_other_survey_is_error(context):
    payload = base_config()
    payload["profile_dimensions"][0] = {
        "question_id": 301,
        "label": "Alheia",
        "mode": "CATEGORICAL",
        "groups": [{"key": "G", "label": "G", "values": ["X"]}],
    }
    result = validate(context, payload)
    assert GrowthConfigErrorCode.QUESTION_FROM_OTHER_SURVEY in error_codes(result)


def test_c47_role_mismatch_is_warning_without_mutation(context):
    payload = base_config()
    payload["profile_dimensions"][0] = {
        "question_id": 103,  # papel_analitico = SEGUNDA_OPCAO
        "label": "Improvisada",
        "mode": "CATEGORICAL",
        "groups": [{"key": "A", "label": "A", "values": ["Candidato A"]}],
    }
    result = validate(context, payload)
    assert result.valid, result.errors
    assert GrowthConfigWarningCode.ANALYTIC_ROLE_MISMATCH in warning_codes(result)
    with context.Session() as db:
        role = db.execute(
            text("SELECT papel_analitico FROM perguntas WHERE id = 103")
        ).scalar()
    assert role == "SEGUNDA_OPCAO"


# ---------------------------------------------------------------------------
# C48-C54 — territorio
# ---------------------------------------------------------------------------


def test_c48_none_level_valid(context):
    assert validate(context, base_config(territory={"level": "NONE"})).valid


def test_c49_setor_level_valid(context):
    result = validate(context, base_config(territory={"level": "SETOR", "setor_ids": [11]}))
    assert result.valid, result.errors


def test_c50_municipio_level_valid(context):
    result = validate(context, base_config(territory={"level": "MUNICIPIO"}))
    assert result.valid, result.errors


def test_c50b_municipio_level_unavailable_without_resolution(context):
    with context.Session() as db:
        db.execute(text("UPDATE setores SET municipio_territorio_id = NULL"))
        db.commit()
    result = validate(context, base_config(territory={"level": "MUNICIPIO"}))
    assert GrowthConfigErrorCode.MUNICIPIO_LEVEL_UNAVAILABLE in error_codes(result)


def test_c51_unsupported_level_is_error():
    config, issues = parse_growth_analysis_configuration(
        base_config(territory={"level": "BAIRRO"})
    )
    assert config is None and issues


def test_c52_setor_from_other_survey_is_error(context):
    result = validate(context, base_config(territory={"level": "SETOR", "setor_ids": [21]}))
    assert GrowthConfigErrorCode.SETOR_NOT_FOUND in error_codes(result)


def test_c53_operational_only_setor_is_error(context):
    result = validate(context, base_config(territory={"level": "SETOR", "setor_ids": [12]}))
    assert GrowthConfigErrorCode.SETOR_NOT_ANALYTICAL in error_codes(result)


def test_c54_territory_is_not_a_signal():
    assert "TERRITORY" not in {item.value for item in SignalType}
    codes = structural_errors(
        base_config(signals=[{"type": "TERRITORY", "question_id": 108}])
    )
    assert codes  # enum invalido: rejeitado na camada estrutural


# ---------------------------------------------------------------------------
# C55-C57 — ponderacao
# ---------------------------------------------------------------------------


def test_c55_nao_ponderado_is_valid_and_warned(context):
    result = validate(context, base_config())
    assert result.valid
    assert GrowthConfigWarningCode.UNWEIGHTED_ANALYSIS in warning_codes(result)


def test_c56_contract_does_not_expose_fake_weighted_base():
    config, issues = parse_growth_analysis_configuration(base_config())
    assert not issues
    dumped = json.dumps(config.model_dump(mode="json"))
    assert "weighted_base" not in dumped
    assert "base_ponderada" not in dumped


def test_c57_no_silent_ponderado_support():
    assert [item.value for item in WeightingMode] == ["NAO_PONDERADO"]


# ---------------------------------------------------------------------------
# C58-C60 — multitenancy
# ---------------------------------------------------------------------------


def test_c58_authorized_user_validates(context):
    assert validate(context, base_config()).valid


def test_c59_cross_tenant_survey_is_not_found(context):
    payload = base_config(pesquisa_id=2)
    payload["scenario"]["intention_questions"][0]["question_id"] = 201
    payload["target"]["bindings"][0] = {"question_id": 201, "values": ["Y"]}
    result = validate(context, payload)
    assert not result.valid
    assert error_codes(result) == {GrowthConfigErrorCode.PESQUISA_NOT_FOUND}


def test_c60_cross_tenant_question_does_not_leak_existence(context):
    payload = base_config(
        signals=[{"type": "REJECTION", "question_id": 201}],
        target={
            "label": "Candidato A",
            "cargo": "Senador",
            "bindings": [
                {"question_id": 101, "values": ["Candidato A"]},
                {"question_id": 201, "values": ["Y"]},
            ],
        },
    )
    result = validate(context, payload)
    codes = error_codes(result)
    # Pergunta de outro tenant e indistinguivel de inexistente.
    assert GrowthConfigErrorCode.QUESTION_NOT_FOUND in codes
    assert GrowthConfigErrorCode.QUESTION_FROM_OTHER_SURVEY not in codes


# ---------------------------------------------------------------------------
# C61-C65 — serializacao
# ---------------------------------------------------------------------------


def test_c61_c62_json_round_trip_preserves_semantics():
    config, issues = parse_growth_analysis_configuration(base_config())
    assert not issues
    dumped = config.model_dump(mode="json")
    json.dumps(dumped)  # serializavel
    rebuilt = GrowthAnalysisConfiguration.model_validate(dumped)
    assert rebuilt.model_dump(mode="json") == dumped


def test_c63_deterministic_canonical_order():
    first = base_config(
        territory={"level": "SETOR", "setor_ids": [11, 12, 11]},
        filters={"agent_ids": [3, 1, 2], "setor_ids": [12, 11], "response_filters": []},
    )
    second = base_config(
        territory={"level": "SETOR", "setor_ids": [12, 11]},
        filters={"agent_ids": [1, 2, 3, 1], "setor_ids": [11, 12], "response_filters": []},
    )
    a, issues_a = parse_growth_analysis_configuration(first)
    b, issues_b = parse_growth_analysis_configuration(second)
    assert not issues_a and not issues_b
    assert a.model_dump(mode="json") == b.model_dump(mode="json")
    assert a.territory.setor_ids == [11, 12]
    assert a.filters.agent_ids == [1, 2, 3]


def test_c64_c65_serialization_has_no_orm_or_tenant_fields():
    config, _ = parse_growth_analysis_configuration(base_config())
    dumped = json.dumps(config.model_dump(mode="json"))
    assert "_sa_instance_state" not in dumped
    assert "company_id" not in dumped
    assert "tenant_id" not in dumped


# ---------------------------------------------------------------------------
# Normalizacao de valores (secao 48)
# ---------------------------------------------------------------------------


def test_value_normalization_returns_canonical_form_with_warning(context):
    payload = base_config()
    payload["target"]["bindings"][0]["values"] = ["  candidato a "]
    result = validate(context, payload)
    assert result.valid, result.errors
    assert GrowthConfigWarningCode.VALUE_NORMALIZED in warning_codes(result)
    normalized = result.normalized_configuration
    assert normalized.target.bindings[0].values == ["Candidato A"]


def test_validation_result_is_serializable(context):
    result = validate(context, base_config())
    json.dumps(result.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Estrutura adicional: sinais duplicados / intencao em signals
# ---------------------------------------------------------------------------


def test_intention_cannot_appear_in_signals():
    codes = structural_errors(
        base_config(signals=[{"type": "INTENTION", "question_id": 101}])
    )
    assert GrowthConfigErrorCode.INTENTION_SIGNAL_IN_SIGNALS in codes


def test_duplicate_signal_type_is_error():
    codes = structural_errors(base_config(signals=[
        {"type": "REJECTION", "question_id": 102},
        {"type": "REJECTION", "question_id": 103},
    ]))
    assert GrowthConfigErrorCode.DUPLICATE_SIGNAL_TYPE in codes


def test_vote_decision_requires_disjoint_groups():
    codes = structural_errors(base_config(signals=[{
        "type": "VOTE_DECISION",
        "question_id": 104,
        "decision_groups": {"mobile": ["Pode mudar"], "crystallized": ["Pode mudar"]},
    }]))
    assert GrowthConfigErrorCode.OVERLAPPING_GROUP_VALUES in codes
