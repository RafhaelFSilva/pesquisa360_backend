"""Motor estatistico do Potencial de Crescimento (MVP 1 — Prompt 03).

Consome exclusivamente o contrato canonico validado
(`GrowthAnalysisConfiguration`) e produz um `GrowthAnalysis` em memoria.

O motor NAO: persiste resultado, expoe endpoint, gera score/ranking
agregado, projeta votos, produz narrativa, pondera (weighted_base = null) ou
infere semantica por texto.

Fluxo:

    configuracao -> revalidacao/normalizacao -> universo da pesquisa
    -> filtros estruturais (universo analitico congelado)
    -> classificacao de intencao -> universo elegivel
    -> segmentacao (perfil x territorio) -> sinais -> evidencias
    -> snapshot (engine_version, configuration_hash, input_fingerprint)

Unidade estatistica invariavel: 1 Coleta = 1 observacao (contagens sempre
sobre coleta_id unicos, nunca count de linhas de Resposta).

Carregamento em lote: numero de queries nao cresce com a quantidade de
segmentos (nenhuma query por segmento/finding).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy.orm import Session

from pesquisa360 import crud
from pesquisa360.db import models
from pesquisa360.question_types import is_multiple_response_question_type
from pesquisa360.services import acessos, filtros_universo, pergunta_territorio
from pesquisa360.services import base_eleitoral as base_eleitoral_service
from pesquisa360.services.response_values import response_values
from pesquisa360.utils.response_normalization import normalizar_resposta_espontanea

from pesquisa360.inteligencia_eleitoral.config import (
    GrowthAnalysisConfiguration,
    SignalType,
    TerritoryLevel,
)
from pesquisa360.inteligencia_eleitoral.results import (
    AnalysisSnapshot,
    AnalysisUniverseSummary,
    AnalysisWarning,
    AnalysisWarningCode,
    CoverageDiagnostics,
    EvidenceStatus,
    FavorableDirection,
    FindingStatus,
    GrowthAnalysis,
    GrowthEvidence,
    GrowthFinding,
    IntentionClassification,
    ProfileGroupMembership,
    TerritoryDiagnostics,
    TerritorySegmentInfo,
    WarningSeverity,
)
from pesquisa360.inteligencia_eleitoral import statistics as growth_statistics
from pesquisa360.inteligencia_eleitoral.validation import (
    GrowthConfigurationValidationResult,
    validate_growth_configuration,
)

# Versao SEMANTICA do motor: muda quando a semantica estatistica muda.
# Nao e commit hash.
GROWTH_ENGINE_VERSION = "1.0"

# Protecao COMPUTACIONAL contra explosao de segmentos (nao e decisao
# metodologica). Segue o padrao de limites de core/analytics_config.
GROWTH_MAX_SEGMENTS = int(os.getenv("P360_GROWTH_MAX_SEGMENTS", "5000"))

_SPECIAL_CLASS_ORDER = (
    IntentionClassification.INDECISO,
    IntentionClassification.BRANCO_NULO,
    IntentionClassification.NS_NR,
    IntentionClassification.NAO_PRETENDE_VOTAR,
)

_SIGNAL_ORDER = (SignalType.REJECTION, SignalType.SECOND_OPTION, SignalType.VOTE_DECISION)

_SIGNAL_FAVORABLE = {
    SignalType.REJECTION: FavorableDirection.LOWER_IS_FAVORABLE,
    SignalType.SECOND_OPTION: FavorableDirection.HIGHER_IS_FAVORABLE,
    SignalType.VOTE_DECISION: FavorableDirection.HIGHER_IS_FAVORABLE,
}

_MISSING_SIGNAL_CODES = {
    SignalType.REJECTION: "NO_REJECTION",
    SignalType.SECOND_OPTION: "NO_SECOND_OPTION",
    SignalType.VOTE_DECISION: "NO_VOTE_DECISION",
}


class GrowthEngineError(Exception):
    """Erro de dominio do motor (sem HTTPException; a API futura converte)."""


class GrowthAnalysisConfigurationError(GrowthEngineError):
    """Configuracao estrutural/de dominio invalida: o motor nao executa."""

    def __init__(self, validation_result: GrowthConfigurationValidationResult):
        self.validation_result = validation_result
        codes = sorted({issue.code for issue in validation_result.errors})
        super().__init__(f"Configuracao invalida para o motor: {codes}")


class GrowthSegmentLimitExceededError(GrowthEngineError):
    code = "SEGMENT_LIMIT_EXCEEDED"

    def __init__(self, limit: int, found: int):
        self.limit = limit
        self.found = found
        super().__init__(
            f"Quantidade teorica de segmentos ({found}) excede o limite tecnico ({limit})."
        )


def _nset(values: Iterable[str]) -> Set[str]:
    return {normalizar_resposta_espontanea(value) for value in values}


def _warning(
    code: AnalysisWarningCode | str,
    message: str,
    *,
    severity: WarningSeverity = WarningSeverity.WARNING,
    **context,
) -> AnalysisWarning:
    return AnalysisWarning(
        code=str(code), severity=severity, message=message, context=context or None
    )


# ---------------------------------------------------------------------------
# Carregamento em lote
# ---------------------------------------------------------------------------


def _load_reportable_values(
    db: Session,
    *,
    pesquisa_id: int,
    current_user,
    questions: Dict[int, models.Pergunta],
) -> Tuple[Dict[int, Dict[int, List[str]]], bool]:
    """coleta_id -> pergunta_id -> lista ordenada de valores reportaveis.

    Uma unica query de respostas para todas as perguntas necessarias.
    Multipla escolha usa o parser compartilhado (`services/response_values`),
    com canonicalizacao POR ELEMENTO pela mesma regra de normalizacao unica
    do projeto (`crud.mapa_opcoes_canonicas`). Espontanea usa o mapeamento
    ativo existente; sem match vira a categoria "Nao categorizada".
    """
    if not questions:
        return {}, False

    spontaneous_mapping = (
        crud.get_active_spontaneous_mapping_for_report(
            db=db, pesquisa_id=pesquisa_id, current_user=current_user
        )
        if any(q.eh_resposta_espontanea for q in questions.values())
        else {}
    )

    rows = (
        db.query(
            models.Resposta.coleta_id,
            models.Resposta.pergunta_id,
            models.Resposta.valor_resposta,
        )
        .join(models.Coleta, models.Coleta.id == models.Resposta.coleta_id)
        .filter(
            models.Coleta.pesquisa_id == pesquisa_id,
            acessos.filtro_company_acessivel(models.Coleta.company_id, current_user),
            models.Resposta.pergunta_id.in_(list(questions)),
        )
        .order_by(models.Resposta.coleta_id, models.Resposta.pergunta_id, models.Resposta.id)
        .all()
    )

    multiple_by_id = {
        qid: is_multiple_response_question_type(question.tipo_pergunta)
        for qid, question in questions.items()
    }

    values_by_coleta: Dict[int, Dict[int, List[str]]] = defaultdict(dict)
    duplicate_anomaly = False
    for coleta_id, question_id, raw_value in rows:
        question = questions[question_id]
        parsed, duplicated = _response_values_for_question(
            question,
            raw_value,
            multiple=multiple_by_id[question_id],
            spontaneous_mapping=spontaneous_mapping,
        )
        duplicate_anomaly = duplicate_anomaly or duplicated
        existing = values_by_coleta[coleta_id].setdefault(question_id, [])
        for value in parsed:
            if value in existing:
                duplicate_anomaly = True
            else:
                existing.append(value)
    return values_by_coleta, duplicate_anomaly


def _response_values_for_question(
    question: models.Pergunta,
    raw_value,
    *,
    multiple: bool,
    spontaneous_mapping: Dict[str, str],
) -> Tuple[List[str], bool]:
    parsed, duplicated = response_values(raw_value, multiple)
    if question.eh_resposta_espontanea:
        reportable = [
            crud.resolve_reportable_response_value(
                pergunta=question,
                valor_resposta=value,
                spontaneous_mapping=spontaneous_mapping,
            )
            for value in parsed
        ]
    elif multiple:
        # Canonicalizacao por elemento com a MESMA chave de normalizacao unica
        # do projeto (o helper de resposta unica pula multipla de proposito).
        mapa = crud.mapa_opcoes_canonicas(question)
        reportable = [
            mapa.get(crud.normalizar_chave_categoria(value), value) for value in parsed
        ]
    else:
        reportable = [
            crud.resolve_reportable_response_value(
                pergunta=question,
                valor_resposta=value,
                spontaneous_mapping=spontaneous_mapping,
            )
            for value in parsed
        ]
    deduped: List[str] = []
    for value in reportable:
        if value and value not in deduped:
            deduped.append(value)
    return deduped, duplicated


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------


def analyze_growth_potential(
    db: Session,
    current_user,
    configuration: GrowthAnalysisConfiguration,
) -> GrowthAnalysis:
    """Interface publica do motor. Nunca recebe company_id/tenant_id."""

    validation_result = validate_growth_configuration(db, current_user, configuration)
    if not validation_result.valid or validation_result.normalized_configuration is None:
        raise GrowthAnalysisConfigurationError(validation_result)
    cfg = validation_result.normalized_configuration

    pesquisa = crud._validar_pesquisa_relatorio(db, cfg.pesquisa_id, current_user)

    warnings: List[AnalysisWarning] = []

    # --- Universo total da pesquisa (tenant/ACL, antes dos filtros) --------
    survey_rows = (
        db.query(models.Coleta.id, models.Coleta.agente_id)
        .filter(
            models.Coleta.pesquisa_id == cfg.pesquisa_id,
            acessos.filtro_company_acessivel(models.Coleta.company_id, current_user),
        )
        .order_by(models.Coleta.id)
        .all()
    )
    survey_ids = [row.id for row in survey_rows]
    agente_por_coleta = {row.id: row.agente_id for row in survey_rows}
    survey_n = len(survey_ids)

    # --- Classificacoes territoriais (regra oficial, no maximo 2 chamadas) --
    territory_active = cfg.territory.level != TerritoryLevel.NONE
    classification_cache: Dict[Tuple[int, ...], dict] = {}

    def classify(setor_ids: Sequence[int]) -> dict:
        key = tuple(sorted(setor_ids))
        if key not in classification_cache:
            classification_cache[key] = crud.classificar_coletas_por_setor(
                db,
                cfg.pesquisa_id,
                current_user,
                setor_ids=list(key) or None,
            )
        return classification_cache[key]

    filter_classification = classify(cfg.filters.setor_ids) if cfg.filters.setor_ids else None
    territory_classification = None
    if territory_active:
        selected = cfg.territory.setor_ids if cfg.territory.level == TerritoryLevel.SETOR else []
        territory_classification = classify(selected)

    # --- Valores reportaveis das perguntas necessarias (uma query) ---------
    needed_question_ids = sorted(
        {item.question_id for item in cfg.scenario.intention_questions}
        | {signal.question_id for signal in cfg.enabled_signals()}
        | {dimension.question_id for dimension in cfg.profile_dimensions}
        | {item.question_id for item in cfg.filters.response_filters}
    )
    questions = {
        question.id: question
        for question in db.query(models.Pergunta)
        .filter(models.Pergunta.id.in_(needed_question_ids))
        .all()
    }
    values_by_coleta, duplicate_anomaly = _load_reportable_values(
        db,
        pesquisa_id=cfg.pesquisa_id,
        current_user=current_user,
        questions=questions,
    )
    if duplicate_anomaly:
        warnings.append(
            _warning(
                AnalysisWarningCode.DUPLICATE_ANSWER_ANOMALY,
                "Foram encontradas respostas duplicadas na mesma entrevista; valores foram deduplicados.",
                severity=WarningSeverity.INFO,
            )
        )

    values_sets: Dict[int, Dict[int, Set[str]]] = {
        coleta_id: {qid: set(values) for qid, values in per_question.items()}
        for coleta_id, per_question in values_by_coleta.items()
    }

    # --- Filtros estruturais (aplicados UMA vez; universo congelado) --------
    analytical_ids = list(survey_ids)
    if cfg.filters.agent_ids:
        allowed_agents = set(cfg.filters.agent_ids)
        analytical_ids = [
            coleta_id
            for coleta_id in analytical_ids
            if agente_por_coleta.get(coleta_id) in allowed_agents
        ]
    if filter_classification is not None:
        allowed_setores = set(cfg.filters.setor_ids)
        classificados = filter_classification["classificados"]
        analytical_ids = [
            coleta_id
            for coleta_id in analytical_ids
            if classificados.get(coleta_id) in allowed_setores
        ]
    if cfg.filters.response_filters:
        analytical_ids = filtros_universo.aplicar_filtros_respostas(
            analytical_ids,
            values_sets,
            cfg.filters.as_filtros_respostas(),
        )
    analytical_ids = sorted(analytical_ids)
    analytical_set = set(analytical_ids)
    analytical_n = len(analytical_ids)

    # --- Classificacao de intencao / universo elegivel ----------------------
    special_lookup: Dict[str, IntentionClassification] = {}
    for special_class, values in (
        (IntentionClassification.INDECISO, cfg.intention_taxonomy.indeciso_declarado),
        (IntentionClassification.BRANCO_NULO, cfg.intention_taxonomy.branco_nulo),
        (IntentionClassification.NS_NR, cfg.intention_taxonomy.ns_nr),
        (IntentionClassification.NAO_PRETENDE_VOTAR, cfg.intention_taxonomy.nao_pretende_votar),
    ):
        for key in _nset(values):
            special_lookup[key] = special_class

    include_by_class = {
        IntentionClassification.INDECISO: cfg.eligibility.include_indeciso,
        IntentionClassification.BRANCO_NULO: cfg.eligibility.include_branco_nulo,
        IntentionClassification.NS_NR: cfg.eligibility.include_ns_nr,
        IntentionClassification.NAO_PRETENDE_VOTAR: cfg.eligibility.include_nao_pretende_votar,
    }

    intention_qids = [item.question_id for item in cfg.scenario.intention_questions]
    target_by_question = {
        qid: _nset(cfg.target.values_for(qid) or []) for qid in intention_qids
    }

    classification_by_coleta: Dict[int, IntentionClassification] = {}
    for coleta_id in analytical_ids:
        has_target = False
        has_regular = False
        special_classes: Set[IntentionClassification] = set()
        has_any_value = False
        per_question = values_by_coleta.get(coleta_id, {})
        for qid in intention_qids:
            for value in per_question.get(qid, []):
                has_any_value = True
                key = normalizar_resposta_espontanea(value)
                if key in target_by_question[qid]:
                    has_target = True
                elif key in special_lookup:
                    special_classes.add(special_lookup[key])
                else:
                    has_regular = True
        if has_target:
            classification = IntentionClassification.CURRENT_TARGET_SUPPORTER
        elif has_regular:
            classification = IntentionClassification.REGULAR_ELIGIBLE
        elif special_classes:
            policies = {include_by_class[item] for item in special_classes}
            if len(policies) > 1:
                # Politicas divergentes: exclusao CONSERVADORA, sem precedencia
                # silenciosa (diagnostico MIXED_SPECIAL_ELIGIBILITY).
                classification = IntentionClassification.SPECIAL_CONFLICT
            else:
                classification = next(
                    item for item in _SPECIAL_CLASS_ORDER if item in special_classes
                )
        elif has_any_value:
            classification = IntentionClassification.REGULAR_ELIGIBLE
        else:
            # Sem resposta de intencao: impossivel saber se e eleitor atual.
            # Fica no universo analitico, fora do elegivel; nunca vira indeciso.
            classification = IntentionClassification.TECHNICAL_MISSING
        classification_by_coleta[coleta_id] = classification

    def _is_eligible(classification: IntentionClassification) -> bool:
        if classification == IntentionClassification.REGULAR_ELIGIBLE:
            return True
        if classification in include_by_class:
            return include_by_class[classification]
        return False

    eligible_ids = sorted(
        coleta_id
        for coleta_id, classification in classification_by_coleta.items()
        if _is_eligible(classification)
    )
    eligible_set = set(eligible_ids)
    eligible_n = len(eligible_ids)

    counts = defaultdict(int)
    for classification in classification_by_coleta.values():
        counts[classification.value] += 1
    supporters_n = counts[IntentionClassification.CURRENT_TARGET_SUPPORTER.value]
    technical_missing_n = counts[IntentionClassification.TECHNICAL_MISSING.value]
    mixed_conflict_n = counts[IntentionClassification.SPECIAL_CONFLICT.value]
    excluded_special_n = sum(
        1
        for classification in classification_by_coleta.values()
        if classification in include_by_class and not include_by_class[classification]
    ) + mixed_conflict_n
    if mixed_conflict_n:
        warnings.append(
            _warning(
                AnalysisWarningCode.MIXED_SPECIAL_ELIGIBILITY,
                "Entrevistas com apenas categorias especiais de políticas divergentes foram excluídas de forma conservadora do universo elegível.",
                n=mixed_conflict_n,
            )
        )

    universe = AnalysisUniverseSummary(
        survey_n=survey_n,
        analytical_n=analytical_n,
        eligible_n=eligible_n,
        current_target_supporters_n=supporters_n,
        technical_missing_intention_n=technical_missing_n,
        excluded_special_n=excluded_special_n,
        mixed_special_conflict_n=mixed_conflict_n,
        intention_classification_counts=dict(sorted(counts.items())),
    )

    # --- Territorio: unidades, municipio e diagnostics ----------------------
    territory_diag = None
    territory_key_by_coleta: Dict[int, Tuple[str, str]] = {}
    territory_units: Dict[str, str] = {}
    territory_reason_by_coleta: Dict[int, str] = {}
    if territory_active:
        classificados = territory_classification["classificados"]
        conflito = territory_classification["conflito_setor"]
        sem_setor = territory_classification["sem_setor"]
        sem_coordenada = territory_classification["sem_coordenada"]
        setor_labels = {
            setor.id: setor.nome for setor in territory_classification["setores"]
        }
        municipio_por_setor = {}
        if cfg.territory.level == TerritoryLevel.MUNICIPIO:
            municipio_por_setor = pergunta_territorio.resolver_municipios_setores(
                db, sorted(setor_labels)
            )
            for setor_id, resolucao in municipio_por_setor.items():
                if resolucao.resolvido:
                    territory_units[str(resolucao.municipio.id)] = resolucao.municipio.nome
        else:
            territory_units = {str(sid): nome for sid, nome in setor_labels.items()}

        municipio_nao_resolvido = 0
        for coleta_id in eligible_ids:
            if coleta_id in sem_coordenada:
                territory_reason_by_coleta[coleta_id] = "SEM_COORDENADA"
                continue
            if coleta_id in conflito:
                territory_reason_by_coleta[coleta_id] = "TERRITORIO_SOBREPOSTO"
                continue
            setor_id = classificados.get(coleta_id)
            if setor_id is None:
                territory_reason_by_coleta[coleta_id] = "SEM_SETOR"
                continue
            if cfg.territory.level == TerritoryLevel.MUNICIPIO:
                resolucao = municipio_por_setor.get(setor_id)
                if resolucao is None or not resolucao.resolvido:
                    territory_reason_by_coleta[coleta_id] = "MUNICIPIO_NAO_RESOLVIDO"
                    municipio_nao_resolvido += 1
                    continue
                territory_key_by_coleta[coleta_id] = (
                    str(resolucao.municipio.id),
                    resolucao.municipio.nome,
                )
            else:
                territory_key_by_coleta[coleta_id] = (
                    str(setor_id),
                    setor_labels.get(setor_id, str(setor_id)),
                )

        territory_diag = TerritoryDiagnostics(
            sem_setor_n=sum(1 for c in eligible_ids if c in sem_setor),
            conflito_setor_n=sum(1 for c in eligible_ids if c in conflito),
            sem_coordenada_n=sum(1 for c in eligible_ids if c in sem_coordenada),
            municipio_nao_resolvido_n=municipio_nao_resolvido,
        )
        if territory_reason_by_coleta:
            warnings.append(
                _warning(
                    AnalysisWarningCode.TERRITORY_PARTICIPATION_BELOW_100,
                    "Parte das entrevistas elegíveis não pôde ser classificada territorialmente; a soma da participação dos segmentos territoriais pode ficar abaixo de 100%.",
                    severity=WarningSeverity.INFO,
                    n=len(territory_reason_by_coleta),
                )
            )

    # --- Contexto eleitoral (APENAS contexto; nunca projecao) ---------------
    electoral_by_territory: Dict[str, int] = {}
    if territory_active and cfg.territory.include_electoral_context:
        electoral_by_territory = _load_electoral_context(
            db,
            current_user,
            pesquisa=pesquisa,
            level=cfg.territory.level,
            territory_units=territory_units,
            territory_classification=territory_classification,
            warnings=warnings,
        )

    # --- Segmentacao ---------------------------------------------------------
    dimension_memberships: List[Dict[int, List[Tuple[str, str]]]] = []
    dimension_group_counts: List[int] = []
    overlap_detected = False
    coverage_reasons: Dict[str, Set[int]] = defaultdict(set)

    for dimension in cfg.profile_dimensions:
        per_coleta: Dict[int, List[Tuple[str, str]]] = {}
        if dimension.mode.value == "CATEGORICAL":
            group_sets = [
                (group.key, group.label, _nset(group.values))
                for group in dimension.groups or []
            ]
            dimension_group_counts.append(len(group_sets))
            for coleta_id in eligible_ids:
                values = values_sets.get(coleta_id, {}).get(dimension.question_id, set())
                if not values:
                    coverage_reasons[f"MISSING_PROFILE:{dimension.label}"].add(coleta_id)
                    continue
                keys = _nset(values)
                memberships = [
                    (key, label)
                    for key, label, group_keys in group_sets
                    if keys & group_keys
                ]
                if not memberships:
                    coverage_reasons[f"UNMATCHED_PROFILE:{dimension.label}"].add(coleta_id)
                    continue
                if len(memberships) > 1:
                    overlap_detected = True
                per_coleta[coleta_id] = memberships
        else:
            ranges = dimension.ranges or []
            dimension_group_counts.append(len(ranges))
            for coleta_id in eligible_ids:
                values = values_by_coleta.get(coleta_id, {}).get(dimension.question_id, [])
                if not values:
                    coverage_reasons[f"MISSING_PROFILE:{dimension.label}"].add(coleta_id)
                    continue
                try:
                    numeric = Decimal(str(values[0]).strip())
                except (InvalidOperation, ValueError):
                    coverage_reasons[f"INVALID_NUMERIC:{dimension.label}"].add(coleta_id)
                    continue
                membership = None
                for numeric_range in ranges:
                    low_ok = numeric >= Decimal(numeric_range.min)
                    high_ok = (
                        numeric_range.max is None
                        or numeric <= Decimal(numeric_range.max)
                    )
                    if low_ok and high_ok:
                        membership = (numeric_range.key, numeric_range.label)
                        break
                if membership is None:
                    coverage_reasons[f"UNMATCHED_PROFILE:{dimension.label}"].add(coleta_id)
                    continue
                per_coleta[coleta_id] = [membership]
        dimension_memberships.append(per_coleta)

    if overlap_detected:
        warnings.append(
            _warning(
                AnalysisWarningCode.OVERLAPPING_SEGMENT_GROUPS,
                "Há entrevistas pertencentes a mais de um grupo da mesma dimensão; a soma das participações pode exceder 100%.",
                severity=WarningSeverity.INFO,
            )
        )

    theoretical_segments = 1
    for count in dimension_group_counts:
        theoretical_segments *= max(count, 1)
    if territory_active:
        theoretical_segments *= max(len(territory_units), 1)
    if theoretical_segments > GROWTH_MAX_SEGMENTS:
        raise GrowthSegmentLimitExceededError(GROWTH_MAX_SEGMENTS, theoretical_segments)

    segments: Dict[str, dict] = {}
    not_segmented: Set[int] = set()
    for coleta_id in eligible_ids:
        memberships_per_dimension: List[List[Tuple[str, str]]] = []
        blocked = False
        for per_coleta in dimension_memberships:
            memberships = per_coleta.get(coleta_id)
            if not memberships:
                blocked = True
                break
            memberships_per_dimension.append(memberships)
        territory_part: Optional[Tuple[str, str]] = None
        if not blocked and territory_active:
            territory_part = territory_key_by_coleta.get(coleta_id)
            if territory_part is None:
                reason = territory_reason_by_coleta.get(coleta_id, "SEM_SETOR")
                coverage_reasons[f"TERRITORY:{reason}"].add(coleta_id)
                blocked = True
        if blocked:
            not_segmented.add(coleta_id)
            continue

        combos: List[List[Tuple[str, str]]] = [[]]
        for memberships in memberships_per_dimension:
            combos = [combo + [membership] for combo in combos for membership in memberships]
        for combo in combos:
            key_parts = [
                f"profile:{dimension.question_id}={membership[0]}"
                for dimension, membership in zip(cfg.profile_dimensions, combo)
            ]
            if territory_part is not None:
                key_parts.append(
                    f"territory:{cfg.territory.level.value.lower()}={territory_part[0]}"
                )
            segment_key = "|".join(key_parts)
            segment = segments.setdefault(
                segment_key,
                {
                    "coletas": set(),
                    "profile": [
                        ProfileGroupMembership(
                            question_id=dimension.question_id,
                            dimension_label=dimension.label,
                            group_key=membership[0],
                            group_label=membership[1],
                        )
                        for dimension, membership in zip(cfg.profile_dimensions, combo)
                    ],
                    "territory": territory_part,
                },
            )
            segment["coletas"].add(coleta_id)

    coverage = CoverageDiagnostics(
        not_segmented_n=len(not_segmented),
        by_reason={reason: len(ids) for reason, ids in sorted(coverage_reasons.items())},
    )

    # --- Sinais: specs, qualidade de espontanea e referencia ----------------
    signal_specs = []
    for signal in cfg.enabled_signals():
        question = questions[signal.question_id]
        spec = {
            "type": signal.type,
            "question_id": signal.question_id,
            "favorable": _SIGNAL_FAVORABLE[signal.type],
            "multiple": is_multiple_response_question_type(question.tipo_pergunta),
            "spontaneous": bool(question.eh_resposta_espontanea),
            "quality_blocked": False,
            "quality_rate": None,
        }
        if signal.type in (SignalType.REJECTION, SignalType.SECOND_OPTION):
            spec["numerator_set"] = _nset(cfg.target.values_for(signal.question_id) or [])
            spec["valid_set"] = None  # qualquer valor reportavel conta na base
        else:
            groups = signal.decision_groups
            mobile = _nset(groups.mobile)
            crystallized = _nset(groups.crystallized)
            spec["numerator_set"] = mobile
            # Base valida da decisao: somente valores classificados nos grupos.
            spec["valid_set"] = mobile | crystallized
        signal_specs.append(spec)

    uncategorized_key = normalizar_resposta_espontanea(crud.NAO_CATEGORIZADA)
    quality_threshold = (
        cfg.spontaneous_quality.max_uncategorized_rate
        if cfg.spontaneous_quality is not None
        else None
    )
    for spec in signal_specs:
        if not spec["spontaneous"] or quality_threshold is None:
            continue
        base = 0
        uncategorized = 0
        for coleta_id in eligible_ids:
            values = values_sets.get(coleta_id, {}).get(spec["question_id"], set())
            if not values:
                continue
            base += 1
            if uncategorized_key in _nset(values):
                uncategorized += 1
        if base:
            uncategorized_rate = Decimal(uncategorized) / Decimal(base)
            spec["quality_rate"] = uncategorized_rate
            if uncategorized_rate > Decimal(str(quality_threshold)):
                spec["quality_blocked"] = True
                warnings.append(
                    _warning(
                        AnalysisWarningCode.HIGH_UNCATEGORIZED_RATE,
                        f"O sinal {spec['type'].value} usa pergunta espontânea com taxa de 'Não categorizada' acima do limiar configurado; o sinal não será usado como evidência.",
                        signal_type=spec["type"].value,
                        question_id=spec["question_id"],
                        uncategorized_rate=str(uncategorized_rate),
                        threshold=str(quality_threshold),
                    )
                )

    def measure(spec: dict, scope: Iterable[int]) -> Tuple[int, int]:
        """(numerador, base valida) contando coleta_id unicos."""
        numerator = 0
        base = 0
        numerator_set = spec["numerator_set"]
        valid_set = spec["valid_set"]
        qid = spec["question_id"]
        for coleta_id in scope:
            values = values_sets.get(coleta_id, {}).get(qid)
            if not values:
                continue
            keys = _nset(values)
            if valid_set is not None:
                keys = keys & valid_set
                if not keys:
                    continue
            base += 1
            if keys & numerator_set:
                numerator += 1
        return numerator, base

    reference_by_signal: Dict[SignalType, Tuple[int, int]] = {}
    for spec in signal_specs:
        if spec["quality_blocked"]:
            continue
        reference_by_signal[spec["type"]] = measure(spec, eligible_ids)

    # --- Findings ------------------------------------------------------------
    confidence_level = cfg.uncertainty.confidence_level
    suppress = cfg.minimum_base.suppress_below_n
    warn_threshold = cfg.minimum_base.warn_below_n

    findings: List[GrowthFinding] = []
    for segment_key in sorted(segments):
        segment = segments[segment_key]
        segment_ids = segment["coletas"]
        n_bruto = len(segment_ids)
        participation = (
            Decimal(n_bruto) / Decimal(eligible_n) if eligible_n else Decimal(0)
        )
        territory_info = None
        if segment["territory"] is not None:
            territory_key, territory_label = segment["territory"]
            territory_info = TerritorySegmentInfo(
                level=cfg.territory.level,
                key=territory_key,
                label=territory_label,
                eleitorado_apto=electoral_by_territory.get(territory_key),
            )

        finding_warnings: List[AnalysisWarning] = []
        if n_bruto < suppress:
            finding_warnings.append(
                _warning(
                    AnalysisWarningCode.SUPPRESSED_BASE_INSUFFICIENT,
                    "Base bruta do segmento abaixo do limiar de supressão; evidências não são calculadas.",
                    n_bruto=n_bruto,
                    suppress_below_n=suppress,
                )
            )
            findings.append(
                GrowthFinding(
                    segment_key=segment_key,
                    profile_groups=segment["profile"],
                    territory=territory_info,
                    n_bruto=n_bruto,
                    weighted_base=None,
                    participation_rate=participation,
                    status=FindingStatus.SUPPRESSED_BASE_INSUFFICIENT,
                    evidences=[],
                    warnings=finding_warnings,
                )
            )
            continue
        if n_bruto < warn_threshold:
            finding_warnings.append(
                _warning(
                    AnalysisWarningCode.SMALL_BASE,
                    "Base bruta do segmento abaixo do limiar de alerta; leia com cautela.",
                    n_bruto=n_bruto,
                    warn_below_n=warn_threshold,
                )
            )

        evidences: List[GrowthEvidence] = []
        for spec in sorted(signal_specs, key=lambda item: _SIGNAL_ORDER.index(item["type"])):
            evidences.append(
                _build_evidence(
                    spec,
                    segment_ids=segment_ids,
                    reference=reference_by_signal.get(spec["type"]),
                    measure=measure,
                    confidence_level=confidence_level,
                    suppress=suppress,
                    warn_threshold=warn_threshold,
                )
            )

        findings.append(
            GrowthFinding(
                segment_key=segment_key,
                profile_groups=segment["profile"],
                territory=territory_info,
                n_bruto=n_bruto,
                weighted_base=None,
                participation_rate=participation,
                status=FindingStatus.AVAILABLE,
                evidences=evidences,
                warnings=finding_warnings,
            )
        )

    # --- Warnings globais ----------------------------------------------------
    warnings.insert(
        0,
        _warning(
            AnalysisWarningCode.UNWEIGHTED_ANALYSIS,
            "Análise não ponderada: percentuais refletem a amostra coletada, não a população. weighted_base indisponível.",
        ),
    )
    warnings.insert(
        1,
        _warning(
            AnalysisWarningCode.SRS_ASSUMPTION,
            "Intervalos são aproximações sob hipótese de Amostragem Aleatória Simples; o sistema não dispõe de estratos, clusters, PSU ou desenho complexo.",
        ),
    )
    warnings.insert(
        2,
        _warning(
            AnalysisWarningCode.REFERENCE_INCLUDES_SEGMENT,
            "A referência é o universo elegível total, que contém o próprio segmento; as comparações são descritivo-comparativas, não teste de duas amostras independentes.",
        ),
    )
    if len(findings) > 1:
        warnings.append(
            _warning(
                AnalysisWarningCode.MULTIPLE_COMPARISONS_EXPLORATORY,
                "Vários segmentos são comparados na mesma execução; leituras extremas podem ser achados ocasionais (análise exploratória).",
                severity=WarningSeverity.INFO,
                segmentos=len(findings),
            )
        )
    configured_types = {signal.type for signal in cfg.enabled_signals()}
    for signal_type in _SIGNAL_ORDER:
        if signal_type not in configured_types:
            warnings.append(
                _warning(
                    AnalysisWarningCode.SIGNAL_NOT_CONFIGURED,
                    f"Sinal {signal_type.value} não configurado; a análise executou sem ele (ausência não é zero).",
                    severity=WarningSeverity.INFO,
                    signal_type=signal_type.value,
                )
            )
    # Propaga warnings de configuracao nao duplicados pelos globais do motor.
    engine_codes = {warning.code for warning in warnings}
    skip_config_codes = engine_codes | {"NO_REJECTION", "NO_SECOND_OPTION", "NO_VOTE_DECISION", "UNWEIGHTED_ANALYSIS"}
    for issue in validation_result.warnings:
        if issue.code in skip_config_codes:
            continue
        warnings.append(
            AnalysisWarning(
                code=issue.code,
                severity=WarningSeverity.INFO,
                message=issue.message,
                context={"source": "configuration", "path": issue.path, **(issue.context or {})},
            )
        )

    # --- Snapshot ------------------------------------------------------------
    configuration_hash = compute_configuration_hash(cfg)
    input_fingerprint = compute_input_fingerprint(
        analytical_ids=analytical_ids,
        values_by_coleta=values_by_coleta,
        needed_question_ids=needed_question_ids,
        territory_key_by_coleta=territory_key_by_coleta,
        territory_reason_by_coleta=territory_reason_by_coleta,
    )
    snapshot = AnalysisSnapshot(
        engine_version=GROWTH_ENGINE_VERSION,
        schema_version=cfg.schema_version,
        configuration_hash=configuration_hash,
        input_fingerprint=input_fingerprint,
        executed_at=datetime.now(timezone.utc),
        pesquisa_id=cfg.pesquisa_id,
        analytical_universe_n=analytical_n,
        eligible_universe_n=eligible_n,
    )

    return GrowthAnalysis(
        snapshot=snapshot,
        universe=universe,
        territory_diagnostics=territory_diag,
        coverage=coverage,
        findings=findings,
        warnings=warnings,
    )


def _build_evidence(
    spec: dict,
    *,
    segment_ids: Set[int],
    reference: Optional[Tuple[int, int]],
    measure,
    confidence_level,
    suppress: int,
    warn_threshold: int,
) -> GrowthEvidence:
    signal_type: SignalType = spec["type"]
    favorable: FavorableDirection = spec["favorable"]
    evidence_warnings: List[AnalysisWarning] = []

    if spec["quality_blocked"]:
        return GrowthEvidence(
            signal_type=signal_type,
            status=EvidenceStatus.SIGNAL_UNAVAILABLE_QUALITY,
            favorable_direction=favorable,
            warnings=[
                _warning(
                    AnalysisWarningCode.HIGH_UNCATEGORIZED_RATE,
                    "Taxa de 'Não categorizada' acima do limiar de qualidade configurado.",
                    uncategorized_rate=str(spec["quality_rate"]),
                )
            ],
        )

    reference_numerator, reference_base = reference if reference else (0, 0)
    if reference_base == 0:
        return GrowthEvidence(
            signal_type=signal_type,
            status=EvidenceStatus.SIGNAL_UNAVAILABLE,
            favorable_direction=favorable,
            segment_base_n=0,
            reference_base_n=0,
            warnings=[
                _warning(
                    AnalysisWarningCode.SIGNAL_UNAVAILABLE,
                    "Nenhuma resposta válida para este sinal no universo elegível.",
                    signal_type=signal_type.value,
                )
            ],
        )

    segment_numerator, segment_base = measure(spec, segment_ids)
    if segment_base < suppress:
        evidence_warnings.append(
            _warning(
                AnalysisWarningCode.SUPPRESSED_BASE_INSUFFICIENT,
                "Base válida do sinal no segmento abaixo do limiar de supressão; taxa/delta/lift não são calculados.",
                signal_base_n=segment_base,
                suppress_below_n=suppress,
            )
        )
        return GrowthEvidence(
            signal_type=signal_type,
            status=EvidenceStatus.SUPPRESSED_BASE_INSUFFICIENT,
            favorable_direction=favorable,
            segment_base_n=segment_base,
            reference_numerator=reference_numerator,
            reference_base_n=reference_base,
            warnings=evidence_warnings,
        )
    if segment_base < warn_threshold:
        evidence_warnings.append(
            _warning(
                AnalysisWarningCode.SMALL_BASE,
                "Base válida do sinal no segmento abaixo do limiar de alerta.",
                signal_base_n=segment_base,
                warn_below_n=warn_threshold,
            )
        )
    if spec["multiple"]:
        evidence_warnings.append(
            _warning(
                AnalysisWarningCode.MULTIPLE_CHOICE_SUM_MAY_EXCEED_100,
                "Pergunta de múltipla escolha: uma entrevista pode pertencer a várias categorias; somas podem exceder 100%.",
                severity=WarningSeverity.INFO,
            )
        )

    segment_rate = growth_statistics.rate(segment_numerator, segment_base)
    reference_rate = growth_statistics.rate(reference_numerator, reference_base)
    delta = growth_statistics.delta_pp(segment_rate, reference_rate)
    lift_value = growth_statistics.lift(segment_rate, reference_rate)
    if lift_value is None:
        evidence_warnings.append(
            _warning(
                AnalysisWarningCode.LIFT_UNDEFINED_REFERENCE_ZERO,
                "Lift indefinido: taxa de referência igual a zero.",
                severity=WarningSeverity.INFO,
            )
        )

    return GrowthEvidence(
        signal_type=signal_type,
        status=EvidenceStatus.AVAILABLE,
        favorable_direction=favorable,
        observed_direction=growth_statistics.observed_direction(delta, favorable),
        segment_numerator=segment_numerator,
        segment_base_n=segment_base,
        segment_rate=segment_rate,
        segment_interval=growth_statistics.wilson_interval(
            segment_numerator, segment_base, confidence_level
        ),
        reference_numerator=reference_numerator,
        reference_base_n=reference_base,
        reference_rate=reference_rate,
        reference_interval=growth_statistics.wilson_interval(
            reference_numerator, reference_base, confidence_level
        ),
        delta_pp=delta,
        lift=lift_value,
        warnings=evidence_warnings,
    )


def _load_electoral_context(
    db: Session,
    current_user,
    *,
    pesquisa,
    level: TerritoryLevel,
    territory_units: Dict[str, str],
    territory_classification: dict,
    warnings: List[AnalysisWarning],
) -> Dict[str, int]:
    """eleitorado_apto por unidade territorial, APENAS como contexto.

    Exige Base Eleitoral principal VALIDADA do Projeto e vínculo oficial;
    qualquer indisponibilidade é declarada (nunca número inventado, nunca
    falha da análise inteira). INVARIÁVEL: nenhum cálculo taxa × eleitorado."""
    try:
        base = base_eleitoral_service.obter_base_principal_projeto_para_calculo(
            db, pesquisa.projeto_id, current_user
        )
    except Exception:
        warnings.append(
            _warning(
                AnalysisWarningCode.ELECTORAL_CONTEXT_UNAVAILABLE,
                "Contexto eleitoral solicitado, mas o Projeto não possui Base Eleitoral principal VALIDADA.",
                severity=WarningSeverity.INFO,
            )
        )
        return {}

    result: Dict[str, int] = {}
    if level == TerritoryLevel.MUNICIPIO:
        municipio_ids = [int(key) for key in territory_units]
        if municipio_ids:
            rows = (
                db.query(models.TerritorioEleitoral.id, models.TerritorioEleitoral.eleitorado_apto)
                .filter(
                    models.TerritorioEleitoral.id.in_(municipio_ids),
                    models.TerritorioEleitoral.base_eleitoral_id == base.id,
                    models.TerritorioEleitoral.eleitorado_apto.isnot(None),
                )
                .all()
            )
            result = {str(row.id): int(row.eleitorado_apto) for row in rows}
    else:
        setor_ids = [setor.id for setor in territory_classification["setores"]]
        if setor_ids:
            rows = (
                db.query(
                    models.SetorTerritorioEleitoral.setor_id,
                    models.TerritorioEleitoral.eleitorado_apto,
                )
                .join(
                    models.TerritorioEleitoral,
                    models.TerritorioEleitoral.id
                    == models.SetorTerritorioEleitoral.territorio_eleitoral_id,
                )
                .filter(
                    models.SetorTerritorioEleitoral.setor_id.in_(setor_ids),
                    models.TerritorioEleitoral.base_eleitoral_id == base.id,
                    models.TerritorioEleitoral.eleitorado_apto.isnot(None),
                )
                .all()
            )
            totals: Dict[int, int] = defaultdict(int)
            for setor_id, eleitorado in rows:
                totals[setor_id] += int(eleitorado)
            result = {str(setor_id): total for setor_id, total in totals.items()}

    if not result:
        warnings.append(
            _warning(
                AnalysisWarningCode.ELECTORAL_CONTEXT_UNAVAILABLE,
                "Contexto eleitoral solicitado, mas nenhum território da execução possui eleitorado vinculado à base principal validada.",
                severity=WarningSeverity.INFO,
            )
        )
    return result


# ---------------------------------------------------------------------------
# Hashes de reprodutibilidade
# ---------------------------------------------------------------------------


def _canonical_sha256(payload) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_configuration_hash(configuration: GrowthAnalysisConfiguration) -> str:
    """SHA-256 do JSON canonico da configuracao NORMALIZADA (sem runtime)."""
    return _canonical_sha256(configuration.model_dump(mode="json"))


def compute_input_fingerprint(
    *,
    analytical_ids: Sequence[int],
    values_by_coleta: Dict[int, Dict[int, List[str]]],
    needed_question_ids: Sequence[int],
    territory_key_by_coleta: Dict[int, Tuple[str, str]],
    territory_reason_by_coleta: Dict[int, str],
) -> str:
    """SHA-256 deterministico dos dados efetivamente usados.

    Inclui, de forma ordenada: coleta_ids do universo analitico, valores
    reportaveis das perguntas analiticas e classificacao territorial usada.
    NAO inclui executed_at."""
    payload = {
        "coletas": sorted(analytical_ids),
        "valores": {
            str(coleta_id): {
                str(question_id): sorted(
                    values_by_coleta.get(coleta_id, {}).get(question_id, [])
                )
                for question_id in needed_question_ids
                if values_by_coleta.get(coleta_id, {}).get(question_id)
            }
            for coleta_id in sorted(analytical_ids)
        },
        "territorio": {
            str(coleta_id): territory_key_by_coleta.get(coleta_id, (None,))[0]
            or territory_reason_by_coleta.get(coleta_id)
            for coleta_id in sorted(
                set(territory_key_by_coleta) | set(territory_reason_by_coleta)
            )
        },
    }
    return _canonical_sha256(payload)
