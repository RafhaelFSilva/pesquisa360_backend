"""Validacao do contrato de configuracao do Potencial de Crescimento.

Duas camadas separadas (secao 54 do Prompt 02):

CAMADA 1 — estrutural (Pydantic, sem banco):
    `parse_growth_analysis_configuration(payload)` monta o modelo e converte
    `ValidationError` em issues tipadas (`ConfigurationIssue`). Os validators
    do contrato embutem o codigo na mensagem com o prefixo "CODIGO::".

CAMADA 2 — dominio/banco:
    `validate_growth_configuration(db, current_user, config)` valida a
    configuracao contra os dados reais da Pesquisa reutilizando os helpers
    existentes (tenant, canonicalizacao de opcoes, categorias espontaneas,
    setores analiticos, resolucao municipal). Nunca duplica regra de tenant:
    a Pesquisa e resolvida por `crud._validar_pesquisa_relatorio` e qualquer
    recurso cross-tenant permanece indistinguivel de inexistente (404 -> issue
    *_NOT_FOUND), como nos padroes ADR-001/ADR-047.

O resultado (`GrowthConfigurationValidationResult`) e um objeto de dominio,
sem dependencia de HTTP: o Prompt 04 podera mapea-lo para uma API de
validacao sem alterar esta camada.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session

from pesquisa360 import crud
from pesquisa360.analytics import PapelAnalitico
from pesquisa360.db import models
from pesquisa360.question_types import normalize_question_type
from pesquisa360.services import acessos
from pesquisa360.services import pergunta_territorio
from pesquisa360.utils.response_normalization import normalizar_resposta_espontanea

from pesquisa360.inteligencia_eleitoral.config import (
    BallotSelectionMode,
    GrowthAnalysisConfiguration,
    ProfileDimensionMode,
    SignalType,
    TerritoryLevel,
)


class GrowthConfigErrorCode(StrEnum):
    # Estruturais (camada 1)
    INVALID_STRUCTURE = "INVALID_STRUCTURE"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    UNSUPPORTED_SCHEMA_VERSION = "UNSUPPORTED_SCHEMA_VERSION"
    UNSUPPORTED_WEIGHTING_MODE = "UNSUPPORTED_WEIGHTING_MODE"
    INVALID_BASE_THRESHOLDS = "INVALID_BASE_THRESHOLDS"
    OVERLAPPING_TAXONOMY_VALUES = "OVERLAPPING_TAXONOMY_VALUES"
    OVERLAPPING_GROUP_VALUES = "OVERLAPPING_GROUP_VALUES"
    OVERLAPPING_NUMERIC_RANGES = "OVERLAPPING_NUMERIC_RANGES"
    INVALID_NUMERIC_RANGE = "INVALID_NUMERIC_RANGE"
    INVALID_PROFILE_DIMENSION = "INVALID_PROFILE_DIMENSION"
    PROFILE_DIMENSION_REQUIRED = "PROFILE_DIMENSION_REQUIRED"
    TOO_MANY_PROFILE_DIMENSIONS = "TOO_MANY_PROFILE_DIMENSIONS"
    DUPLICATE_BINDING_QUESTION = "DUPLICATE_BINDING_QUESTION"
    DUPLICATE_SIGNAL_TYPE = "DUPLICATE_SIGNAL_TYPE"
    DUPLICATE_SIGNAL_QUESTION = "DUPLICATE_SIGNAL_QUESTION"
    DUPLICATE_FILTER_QUESTION = "DUPLICATE_FILTER_QUESTION"
    SCENARIO_SLOTS_INCOHERENT = "SCENARIO_SLOTS_INCOHERENT"
    INTENTION_SIGNAL_IN_SIGNALS = "INTENTION_SIGNAL_IN_SIGNALS"
    MISSING_DECISION_GROUPS = "MISSING_DECISION_GROUPS"
    UNEXPECTED_DECISION_GROUPS = "UNEXPECTED_DECISION_GROUPS"
    TARGET_OVERLAPS_TAXONOMY = "TARGET_OVERLAPS_TAXONOMY"
    INVALID_SPONTANEOUS_THRESHOLD = "INVALID_SPONTANEOUS_THRESHOLD"
    SETORES_ONLY_FOR_SETOR_LEVEL = "SETORES_ONLY_FOR_SETOR_LEVEL"
    ELECTORAL_CONTEXT_REQUIRES_TERRITORY = "ELECTORAL_CONTEXT_REQUIRES_TERRITORY"
    RESERVED_VALUE = "RESERVED_VALUE"
    EMPTY_VALUE = "EMPTY_VALUE"
    # Dominio (camada 2)
    PESQUISA_NOT_FOUND = "PESQUISA_NOT_FOUND"
    QUESTION_NOT_FOUND = "QUESTION_NOT_FOUND"
    QUESTION_FROM_OTHER_SURVEY = "QUESTION_FROM_OTHER_SURVEY"
    QUESTION_INACTIVE = "QUESTION_INACTIVE"
    TERRITORIAL_SIGNAL_NOT_ALLOWED = "TERRITORIAL_SIGNAL_NOT_ALLOWED"
    INCOMPATIBLE_QUESTION_TYPE = "INCOMPATIBLE_QUESTION_TYPE"
    TARGET_VALUE_NOT_FOUND = "TARGET_VALUE_NOT_FOUND"
    VALUE_NOT_FOUND = "VALUE_NOT_FOUND"
    MISSING_TARGET_BINDING = "MISSING_TARGET_BINDING"
    SETOR_NOT_FOUND = "SETOR_NOT_FOUND"
    SETOR_NOT_ANALYTICAL = "SETOR_NOT_ANALYTICAL"
    NO_ANALYTICAL_SECTORS = "NO_ANALYTICAL_SECTORS"
    MUNICIPIO_LEVEL_UNAVAILABLE = "MUNICIPIO_LEVEL_UNAVAILABLE"
    SPONTANEOUS_POLICY_REQUIRED = "SPONTANEOUS_POLICY_REQUIRED"


class GrowthConfigWarningCode(StrEnum):
    NO_REJECTION = "NO_REJECTION"
    NO_SECOND_OPTION = "NO_SECOND_OPTION"
    NO_VOTE_DECISION = "NO_VOTE_DECISION"
    UNWEIGHTED_ANALYSIS = "UNWEIGHTED_ANALYSIS"
    SPONTANEOUS_SIGNAL = "SPONTANEOUS_SIGNAL"
    ANALYTIC_ROLE_MISMATCH = "ANALYTIC_ROLE_MISMATCH"
    UNUSED_TARGET_BINDING = "UNUSED_TARGET_BINDING"
    VALUE_NORMALIZED = "VALUE_NORMALIZED"
    MULTIPLE_CHOICE_SECOND_OPTION = "MULTIPLE_CHOICE_SECOND_OPTION"
    TERRITORIAL_PROFILE_DIMENSION = "TERRITORIAL_PROFILE_DIMENSION"
    NO_ELECTORAL_CONTEXT = "NO_ELECTORAL_CONTEXT"


class ConfigurationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    path: str = ""
    context: Optional[dict] = None


class GrowthConfigurationValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    normalized_configuration: Optional[GrowthAnalysisConfiguration] = None
    errors: List[ConfigurationIssue] = []
    warnings: List[ConfigurationIssue] = []


# ---------------------------------------------------------------------------
# Camada 1 — estrutural
# ---------------------------------------------------------------------------

_EMBEDDED_CODE = re.compile(r"([A-Z][A-Z0-9_]+)::")
_ERROR_CODES = {code.value for code in GrowthConfigErrorCode}


def _loc_to_path(loc: tuple) -> str:
    parts: List[str] = []
    for item in loc:
        if isinstance(item, int):
            parts.append(f"[{item}]")
        else:
            parts.append(f".{item}" if parts else str(item))
    return "".join(parts)


def _structural_issue(error: dict) -> ConfigurationIssue:
    loc = tuple(error.get("loc") or ())
    message = str(error.get("msg") or "estrutura invalida")
    code = GrowthConfigErrorCode.INVALID_STRUCTURE.value
    match = _EMBEDDED_CODE.search(message)
    if match and match.group(1) in _ERROR_CODES:
        code = match.group(1)
        message = message.split("::", 1)[-1]
    elif error.get("type") == "extra_forbidden":
        code = GrowthConfigErrorCode.UNKNOWN_FIELD.value
    elif error.get("type") == "enum" and loc[-2:] == ("weighting", "mode"):
        code = GrowthConfigErrorCode.UNSUPPORTED_WEIGHTING_MODE.value
    return ConfigurationIssue(code=code, message=message, path=_loc_to_path(loc))


def parse_growth_analysis_configuration(
    payload: dict,
) -> Tuple[Optional[GrowthAnalysisConfiguration], List[ConfigurationIssue]]:
    """Camada estrutural: payload -> contrato tipado ou issues tipadas."""
    try:
        return GrowthAnalysisConfiguration.model_validate(payload), []
    except ValidationError as exc:
        return None, [_structural_issue(error) for error in exc.errors()]


# ---------------------------------------------------------------------------
# Camada 2 — dominio/banco
# ---------------------------------------------------------------------------

_EXPECTED_ROLE_BY_SIGNAL = {
    SignalType.REJECTION: PapelAnalitico.REJEICAO,
    SignalType.SECOND_OPTION: PapelAnalitico.SEGUNDA_OPCAO,
    SignalType.VOTE_DECISION: PapelAnalitico.DECISAO_VOTO,
}


class _DomainContext:
    """Estado acumulado de uma validacao de dominio."""

    def __init__(self) -> None:
        self.errors: List[ConfigurationIssue] = []
        self.warnings: List[ConfigurationIssue] = []
        # path -> lista de valores canonicos, para montar a forma normalizada.
        self.normalized_values: Dict[str, List[str]] = {}

    def error(self, code: GrowthConfigErrorCode, message: str, path: str = "", **context) -> None:
        self.errors.append(
            ConfigurationIssue(code=code.value, message=message, path=path, context=context or None)
        )

    def warning(self, code: GrowthConfigWarningCode, message: str, path: str = "", **context) -> None:
        self.warnings.append(
            ConfigurationIssue(code=code.value, message=message, path=path, context=context or None)
        )


def _load_survey(db: Session, config: GrowthAnalysisConfiguration, current_user, ctx: _DomainContext):
    try:
        return crud._validar_pesquisa_relatorio(db, config.pesquisa_id, current_user)
    except HTTPException:
        # Cross-tenant e inexistente sao indistinguiveis (ADR-001).
        ctx.error(
            GrowthConfigErrorCode.PESQUISA_NOT_FOUND,
            "Pesquisa não encontrada.",
            path="pesquisa_id",
        )
        return None


def _question_issue(db: Session, question_id: int, current_user, ctx: _DomainContext, path: str) -> None:
    accessible = (
        db.query(models.Pergunta.id)
        .join(models.Pesquisa, models.Pesquisa.id == models.Pergunta.pesquisa_id)
        .join(models.Projeto, models.Projeto.id == models.Pesquisa.projeto_id)
        .filter(
            models.Pergunta.id == question_id,
            acessos.filtro_projeto_acessivel(current_user),
        )
        .first()
    )
    if accessible:
        ctx.error(
            GrowthConfigErrorCode.QUESTION_FROM_OTHER_SURVEY,
            f"A pergunta {question_id} pertence a outra pesquisa.",
            path=path,
            question_id=question_id,
        )
    else:
        # Pergunta inexistente ou de outro tenant: mesma resposta, sem vazar
        # existencia (padrao 404 do projeto).
        ctx.error(
            GrowthConfigErrorCode.QUESTION_NOT_FOUND,
            f"Pergunta {question_id} não encontrada.",
            path=path,
            question_id=question_id,
        )


def _spontaneous_categories(db: Session, pesquisa_id: int) -> Dict[str, str]:
    rows = (
        db.query(models.CategoriaRespostaEspontanea.nome)
        .filter(
            models.CategoriaRespostaEspontanea.pesquisa_id == pesquisa_id,
            models.CategoriaRespostaEspontanea.ativo.is_(True),
        )
        .all()
    )
    return {normalizar_resposta_espontanea(nome): nome for (nome,) in rows}


def _validate_values(
    ctx: _DomainContext,
    *,
    pergunta,
    values: List[str],
    path: str,
    not_found_code: GrowthConfigErrorCode,
    spontaneous_categories: Optional[Dict[str, str]],
) -> None:
    """Valida cada valor configurado contra os valores reportaveis reais e
    registra a forma canonica para a configuracao normalizada."""
    if pergunta.eh_resposta_espontanea:
        valid_map = spontaneous_categories or {}
        reserved = {normalizar_resposta_espontanea(crud.NAO_CATEGORIZADA)}
    else:
        tipo = normalize_question_type(pergunta.tipo_pergunta)
        if tipo not in ("ESCOLHA_SIMPLES", "MULTIPLA_ESCOLHA"):
            # Sem catalogo de valores (ex.: NUMERO): nada a validar aqui.
            return
        valid_map = crud.mapa_opcoes_canonicas(pergunta)
        reserved = set()

    normalized: List[str] = []
    for index, value in enumerate(values):
        key = normalizar_resposta_espontanea(value)
        if key in reserved:
            ctx.error(
                GrowthConfigErrorCode.RESERVED_VALUE,
                f"{value!r} é uma categoria técnica e não pode ser configurada.",
                path=f"{path}[{index}]",
                question_id=pergunta.id,
            )
            continue
        canonical = valid_map.get(key)
        if canonical is None:
            ctx.error(
                not_found_code,
                f"O valor {value!r} não existe entre os valores reportáveis da pergunta {pergunta.id}.",
                path=f"{path}[{index}]",
                question_id=pergunta.id,
            )
            continue
        if canonical != value:
            ctx.warning(
                GrowthConfigWarningCode.VALUE_NORMALIZED,
                f"O valor {value!r} foi normalizado para {canonical!r}.",
                path=f"{path}[{index}]",
                question_id=pergunta.id,
                original=value,
                canonical=canonical,
            )
        normalized.append(canonical)
    if normalized and len(normalized) == len(values):
        ctx.normalized_values[path] = normalized


def _check_role(ctx: _DomainContext, pergunta, expected: PapelAnalitico, path: str) -> None:
    """papel_analitico e SUGESTAO, nunca autoridade: divergencia gera warning e
    a Pergunta jamais e alterada."""
    role = pergunta.papel_analitico
    if role and role != expected.value:
        ctx.warning(
            GrowthConfigWarningCode.ANALYTIC_ROLE_MISMATCH,
            f"A pergunta {pergunta.id} está marcada como {role}, mas foi configurada como {expected.value}.",
            path=path,
            question_id=pergunta.id,
            papel_analitico=role,
            esperado=expected.value,
        )


def _resolve_question(
    questions: Dict[int, object],
    ctx: _DomainContext,
    question_id: int,
    path: str,
    *,
    db: Session,
    current_user,
    require_global: bool,
):
    pergunta = questions.get(question_id)
    if pergunta is None:
        _question_issue(db, question_id, current_user, ctx, path)
        return None
    if not pergunta.ativo:
        ctx.error(
            GrowthConfigErrorCode.QUESTION_INACTIVE,
            f"A pergunta {question_id} está inativa.",
            path=path,
            question_id=question_id,
        )
        return None
    if require_global and pergunta.aplicabilidade == pergunta_territorio.APLICABILIDADE_TERRITORIAL:
        # D14: pergunta TERRITORIAL nao pode ser sinal eleitoral no MVP.
        ctx.error(
            GrowthConfigErrorCode.TERRITORIAL_SIGNAL_NOT_ALLOWED,
            f"A pergunta {question_id} tem aplicabilidade TERRITORIAL e não pode ser sinal.",
            path=path,
            question_id=question_id,
        )
        return None
    return pergunta


def _validate_setores(
    db: Session,
    ctx: _DomainContext,
    *,
    pesquisa_id: int,
    setor_ids: List[int],
    current_user,
    path: str,
) -> None:
    if not setor_ids:
        return
    rows = (
        db.query(models.Setor.id, models.Setor.finalidade)
        .join(models.Pesquisa, models.Pesquisa.id == models.Setor.pesquisa_id)
        .join(models.Projeto, models.Projeto.id == models.Pesquisa.projeto_id)
        .filter(
            models.Setor.id.in_(setor_ids),
            models.Setor.pesquisa_id == pesquisa_id,
            acessos.filtro_projeto_acessivel(current_user),
        )
        .all()
    )
    found = {row.id: row.finalidade for row in rows}
    for setor_id in setor_ids:
        finalidade = found.get(setor_id)
        if finalidade is None:
            # Setor de outra pesquisa/tenant e inexistente sao indistinguiveis.
            ctx.error(
                GrowthConfigErrorCode.SETOR_NOT_FOUND,
                f"Setor {setor_id} não encontrado para esta pesquisa.",
                path=path,
                setor_id=setor_id,
            )
        elif finalidade not in crud.FINALIDADES_ANALITICAS:
            ctx.error(
                GrowthConfigErrorCode.SETOR_NOT_ANALYTICAL,
                f"O setor {setor_id} tem finalidade {finalidade} e não participa de análise territorial.",
                path=path,
                setor_id=setor_id,
                finalidade=finalidade,
            )


def _analytical_setor_ids(db: Session, pesquisa_id: int) -> List[int]:
    rows = (
        db.query(models.Setor.id)
        .filter(
            models.Setor.pesquisa_id == pesquisa_id,
            models.Setor.finalidade.in_(crud.FINALIDADES_ANALITICAS),
        )
        .all()
    )
    return [row.id for row in rows]


def _apply_normalized_values(
    config: GrowthAnalysisConfiguration, normalized_values: Dict[str, List[str]]
) -> GrowthAnalysisConfiguration:
    """Reconstroi o contrato com os valores canonicos, revalidando tudo."""
    if not normalized_values:
        return config
    dump = config.model_dump(mode="json")
    for path, values in normalized_values.items():
        cursor = dump
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\[\d+\]", path)
        for token in tokens[:-1]:
            cursor = cursor[int(token[1:-1])] if token.startswith("[") else cursor[token]
        last = tokens[-1]
        if last.startswith("["):
            cursor[int(last[1:-1])] = values
        else:
            cursor[last] = values
    return GrowthAnalysisConfiguration.model_validate(dump)


def validate_growth_configuration(
    db: Session,
    current_user,
    config: GrowthAnalysisConfiguration,
) -> GrowthConfigurationValidationResult:
    """Camada de dominio: valida a configuracao contra os dados reais."""
    ctx = _DomainContext()

    pesquisa = _load_survey(db, config, current_user, ctx)
    if pesquisa is None:
        return GrowthConfigurationValidationResult(
            valid=False, errors=ctx.errors, warnings=ctx.warnings
        )

    questions: Dict[int, object] = {
        pergunta.id: pergunta
        for pergunta in db.query(models.Pergunta)
        .filter(models.Pergunta.pesquisa_id == config.pesquisa_id)
        .all()
    }

    spontaneous_categories = _spontaneous_categories(db, config.pesquisa_id)
    spontaneous_signal_present = False

    def resolve(question_id: int, path: str, *, require_global: bool):
        return _resolve_question(
            questions,
            ctx,
            question_id,
            path,
            db=db,
            current_user=current_user,
            require_global=require_global,
        )

    # --- Intencao (fonte da verdade: scenario) -----------------------------
    intention_questions = {}
    ballot_mode = config.scenario.ballot_selection_mode
    for index, intention in enumerate(config.scenario.intention_questions):
        path = f"scenario.intention_questions[{index}]"
        pergunta = resolve(intention.question_id, path, require_global=True)
        if pergunta is None:
            continue
        intention_questions[intention.question_id] = pergunta
        tipo = normalize_question_type(pergunta.tipo_pergunta)
        if pergunta.eh_resposta_espontanea:
            spontaneous_signal_present = True
        elif tipo == "MULTIPLA_ESCOLHA":
            if ballot_mode != BallotSelectionMode.MULTIPLE:
                ctx.error(
                    GrowthConfigErrorCode.INCOMPATIBLE_QUESTION_TYPE,
                    f"A pergunta {pergunta.id} é MULTIPLA_ESCOLHA e exige ballot_selection_mode MULTIPLE.",
                    path=path,
                    question_id=pergunta.id,
                    tipo=tipo,
                )
        elif tipo != "ESCOLHA_SIMPLES":
            ctx.error(
                GrowthConfigErrorCode.INCOMPATIBLE_QUESTION_TYPE,
                f"A pergunta {pergunta.id} ({tipo}) não pode ser pergunta de intenção.",
                path=path,
                question_id=pergunta.id,
                tipo=tipo,
            )
            continue
        _check_role(ctx, pergunta, PapelAnalitico.INTENCAO_VOTO, path)

    # --- Sinais adicionais --------------------------------------------------
    signal_questions: Dict[int, object] = {}
    configured_types = {signal.type for signal in config.enabled_signals()}
    for absent_type, warning_code in (
        (SignalType.REJECTION, GrowthConfigWarningCode.NO_REJECTION),
        (SignalType.SECOND_OPTION, GrowthConfigWarningCode.NO_SECOND_OPTION),
        (SignalType.VOTE_DECISION, GrowthConfigWarningCode.NO_VOTE_DECISION),
    ):
        if absent_type not in configured_types:
            ctx.warning(
                warning_code,
                f"Sinal {absent_type.value} não configurado; a análise executará sem ele.",
                path="signals",
            )

    for index, signal in enumerate(config.signals):
        if not signal.enabled:
            continue
        path = f"signals[{index}]"
        pergunta = resolve(signal.question_id, path, require_global=True)
        if pergunta is None:
            continue
        signal_questions[signal.question_id] = pergunta
        tipo = normalize_question_type(pergunta.tipo_pergunta)
        spontaneous = bool(pergunta.eh_resposta_espontanea)
        if spontaneous:
            spontaneous_signal_present = True

        if signal.type == SignalType.REJECTION:
            if not spontaneous and tipo not in ("ESCOLHA_SIMPLES", "MULTIPLA_ESCOLHA"):
                ctx.error(
                    GrowthConfigErrorCode.INCOMPATIBLE_QUESTION_TYPE,
                    f"A pergunta {pergunta.id} ({tipo}) não pode ser sinal de rejeição.",
                    path=path,
                    question_id=pergunta.id,
                    tipo=tipo,
                )
                continue
        elif signal.type == SignalType.SECOND_OPTION:
            if not spontaneous and tipo not in ("ESCOLHA_SIMPLES", "MULTIPLA_ESCOLHA"):
                ctx.error(
                    GrowthConfigErrorCode.INCOMPATIBLE_QUESTION_TYPE,
                    f"A pergunta {pergunta.id} ({tipo}) não pode ser sinal de segunda opção.",
                    path=path,
                    question_id=pergunta.id,
                    tipo=tipo,
                )
                continue
            if tipo == "MULTIPLA_ESCOLHA":
                ctx.warning(
                    GrowthConfigWarningCode.MULTIPLE_CHOICE_SECOND_OPTION,
                    f"A pergunta {pergunta.id} de segunda opção é de múltipla escolha; "
                    "a soma dos percentuais poderá exceder 100%.",
                    path=path,
                    question_id=pergunta.id,
                )
        elif signal.type == SignalType.VOTE_DECISION:
            # ESCALA como decisao do voto fica registrada como evolucao futura.
            if spontaneous or tipo != "ESCOLHA_SIMPLES":
                ctx.error(
                    GrowthConfigErrorCode.INCOMPATIBLE_QUESTION_TYPE,
                    f"A pergunta {pergunta.id} ({tipo}) não pode ser sinal de decisão do voto no MVP.",
                    path=path,
                    question_id=pergunta.id,
                    tipo=tipo,
                )
                continue
            groups = signal.decision_groups
            if groups is not None:
                _validate_values(
                    ctx,
                    pergunta=pergunta,
                    values=groups.mobile,
                    path=f"{path}.decision_groups.mobile",
                    not_found_code=GrowthConfigErrorCode.VALUE_NOT_FOUND,
                    spontaneous_categories=spontaneous_categories,
                )
                _validate_values(
                    ctx,
                    pergunta=pergunta,
                    values=groups.crystallized,
                    path=f"{path}.decision_groups.crystallized",
                    not_found_code=GrowthConfigErrorCode.VALUE_NOT_FOUND,
                    spontaneous_categories=spontaneous_categories,
                )
        _check_role(ctx, pergunta, _EXPECTED_ROLE_BY_SIGNAL[signal.type], path)

    # --- Bindings da candidatura -------------------------------------------
    candidate_specific = set(config.candidate_specific_question_ids())
    bound_questions = {binding.question_id for binding in config.target.bindings}
    for question_id in sorted(candidate_specific - bound_questions):
        ctx.error(
            GrowthConfigErrorCode.MISSING_TARGET_BINDING,
            f"A pergunta {question_id} é candidato-específica e exige binding em target.bindings.",
            path="target.bindings",
            question_id=question_id,
        )
    for index, binding in enumerate(config.target.bindings):
        path = f"target.bindings[{index}]"
        if binding.question_id not in candidate_specific:
            ctx.warning(
                GrowthConfigWarningCode.UNUSED_TARGET_BINDING,
                f"O binding da pergunta {binding.question_id} não é usado por nenhum sinal configurado.",
                path=path,
                question_id=binding.question_id,
            )
        pergunta = resolve(binding.question_id, path, require_global=False)
        if pergunta is None:
            continue
        _validate_values(
            ctx,
            pergunta=pergunta,
            values=binding.values,
            path=f"{path}.values",
            not_found_code=GrowthConfigErrorCode.TARGET_VALUE_NOT_FOUND,
            spontaneous_categories=spontaneous_categories,
        )

    # --- Taxonomia de intencao ----------------------------------------------
    # As listas especiais pertencem as perguntas de intencao; validamos contra
    # a uniao dos catalogos delas (um valor precisa existir em ao menos uma).
    taxonomy_fields = (
        "indeciso_declarado",
        "branco_nulo",
        "ns_nr",
        "nao_pretende_votar",
    )
    if intention_questions:
        for field in taxonomy_fields:
            values: List[str] = getattr(config.intention_taxonomy, field)
            if not values:
                continue
            path = f"intention_taxonomy.{field}"
            if len(intention_questions) == 1:
                (pergunta,) = intention_questions.values()
                _validate_values(
                    ctx,
                    pergunta=pergunta,
                    values=values,
                    path=path,
                    not_found_code=GrowthConfigErrorCode.VALUE_NOT_FOUND,
                    spontaneous_categories=spontaneous_categories,
                )
            else:
                for index, value in enumerate(values):
                    key = normalizar_resposta_espontanea(value)
                    found = False
                    for pergunta in intention_questions.values():
                        if pergunta.eh_resposta_espontanea:
                            found = found or key in spontaneous_categories
                        else:
                            found = found or key in crud.mapa_opcoes_canonicas(pergunta)
                    if not found:
                        ctx.error(
                            GrowthConfigErrorCode.VALUE_NOT_FOUND,
                            f"O valor {value!r} não existe em nenhuma pergunta de intenção do cenário.",
                            path=f"{path}[{index}]",
                        )

    # --- Dimensoes de perfil ------------------------------------------------
    for index, dimension in enumerate(config.profile_dimensions):
        path = f"profile_dimensions[{index}]"
        pergunta = resolve(dimension.question_id, path, require_global=False)
        if pergunta is None:
            continue
        if pergunta.aplicabilidade == pergunta_territorio.APLICABILIDADE_TERRITORIAL:
            ctx.warning(
                GrowthConfigWarningCode.TERRITORIAL_PROFILE_DIMENSION,
                f"A pergunta {pergunta.id} é TERRITORIAL: o denominador desta dimensão varia por território.",
                path=path,
                question_id=pergunta.id,
            )
        _check_role(ctx, pergunta, PapelAnalitico.PERFIL, path)
        tipo = normalize_question_type(pergunta.tipo_pergunta)
        if dimension.mode == ProfileDimensionMode.CATEGORICAL:
            if not pergunta.eh_resposta_espontanea and tipo != "ESCOLHA_SIMPLES":
                # MULTIPLA_ESCOLHA como segmentacao faria a mesma entrevista
                # pertencer a varios segmentos: fora do MVP.
                ctx.error(
                    GrowthConfigErrorCode.INCOMPATIBLE_QUESTION_TYPE,
                    f"A pergunta {pergunta.id} ({tipo}) não pode segmentar no modo CATEGORICAL.",
                    path=path,
                    question_id=pergunta.id,
                    tipo=tipo,
                )
                continue
            for group_index, group in enumerate(dimension.groups or []):
                _validate_values(
                    ctx,
                    pergunta=pergunta,
                    values=group.values,
                    path=f"{path}.groups[{group_index}].values",
                    not_found_code=GrowthConfigErrorCode.VALUE_NOT_FOUND,
                    spontaneous_categories=spontaneous_categories,
                )
        else:
            if pergunta.eh_resposta_espontanea or tipo != "NUMERO":
                ctx.error(
                    GrowthConfigErrorCode.INCOMPATIBLE_QUESTION_TYPE,
                    f"A pergunta {pergunta.id} ({tipo}) não pode segmentar no modo NUMERIC_RANGES.",
                    path=path,
                    question_id=pergunta.id,
                    tipo=tipo,
                )

    # --- Filtros estruturais ------------------------------------------------
    for index, response_filter in enumerate(config.filters.response_filters):
        path = f"filters.response_filters[{index}]"
        pergunta = resolve(response_filter.question_id, path, require_global=False)
        if pergunta is None:
            continue
        tipo = normalize_question_type(pergunta.tipo_pergunta)
        if not pergunta.eh_resposta_espontanea and tipo not in (
            "ESCOLHA_SIMPLES",
            "MULTIPLA_ESCOLHA",
        ):
            ctx.error(
                GrowthConfigErrorCode.INCOMPATIBLE_QUESTION_TYPE,
                f"A pergunta {pergunta.id} ({tipo}) não pode ser filtro por resposta.",
                path=path,
                question_id=pergunta.id,
                tipo=tipo,
            )
            continue
        _validate_values(
            ctx,
            pergunta=pergunta,
            values=response_filter.values,
            path=f"{path}.values",
            not_found_code=GrowthConfigErrorCode.VALUE_NOT_FOUND,
            spontaneous_categories=spontaneous_categories,
        )

    _validate_setores(
        db,
        ctx,
        pesquisa_id=config.pesquisa_id,
        setor_ids=config.filters.setor_ids,
        current_user=current_user,
        path="filters.setor_ids",
    )

    # --- Territorio ---------------------------------------------------------
    territory = config.territory
    if territory.level == TerritoryLevel.SETOR:
        if territory.setor_ids:
            _validate_setores(
                db,
                ctx,
                pesquisa_id=config.pesquisa_id,
                setor_ids=territory.setor_ids,
                current_user=current_user,
                path="territory.setor_ids",
            )
        elif not _analytical_setor_ids(db, config.pesquisa_id):
            ctx.error(
                GrowthConfigErrorCode.NO_ANALYTICAL_SECTORS,
                "A pesquisa não possui setores analíticos (finalidade RELATORIO/AMBOS).",
                path="territory",
            )
    elif territory.level == TerritoryLevel.MUNICIPIO:
        analytical_ids = _analytical_setor_ids(db, config.pesquisa_id)
        resolucoes = (
            pergunta_territorio.resolver_municipios_setores(db, analytical_ids)
            if analytical_ids
            else {}
        )
        if not any(resolucao.resolvido for resolucao in resolucoes.values()):
            ctx.error(
                GrowthConfigErrorCode.MUNICIPIO_LEVEL_UNAVAILABLE,
                "Nenhum setor analítico da pesquisa resolve um município; o nível MUNICIPIO não está disponível.",
                path="territory",
            )
    if territory.level != TerritoryLevel.NONE and not territory.include_electoral_context:
        ctx.warning(
            GrowthConfigWarningCode.NO_ELECTORAL_CONTEXT,
            "A análise territorial não incluirá contexto de eleitorado da Base Eleitoral.",
            path="territory",
        )

    # --- Politicas ----------------------------------------------------------
    if spontaneous_signal_present:
        ctx.warning(
            GrowthConfigWarningCode.SPONTANEOUS_SIGNAL,
            "Há pergunta espontânea entre os sinais; a qualidade da categorização limita a leitura.",
            path="signals",
        )
        policy = config.spontaneous_quality
        if policy is None or policy.max_uncategorized_rate is None:
            ctx.error(
                GrowthConfigErrorCode.SPONTANEOUS_POLICY_REQUIRED,
                "Sinal espontâneo exige spontaneous_quality.max_uncategorized_rate explícito.",
                path="spontaneous_quality",
            )

    # D11: sempre declarar a limitacao de ponderacao.
    ctx.warning(
        GrowthConfigWarningCode.UNWEIGHTED_ANALYSIS,
        "Análise não ponderada: percentuais refletem a amostra coletada, não a população.",
        path="weighting",
    )

    valid = not ctx.errors
    normalized = _apply_normalized_values(config, ctx.normalized_values) if valid else None
    return GrowthConfigurationValidationResult(
        valid=valid,
        normalized_configuration=normalized,
        errors=ctx.errors,
        warnings=ctx.warnings,
    )


def validate_growth_configuration_payload(
    db: Session,
    current_user,
    payload: dict,
) -> GrowthConfigurationValidationResult:
    """Camadas 1 e 2 em sequencia, a partir do payload bruto."""
    config, structural_errors = parse_growth_analysis_configuration(payload)
    if config is None:
        return GrowthConfigurationValidationResult(
            valid=False, errors=structural_errors, warnings=[]
        )
    return validate_growth_configuration(db, current_user, config)
