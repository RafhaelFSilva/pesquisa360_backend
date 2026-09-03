"""Contrato HTTP do Potencial de Crescimento (Prompt 04).

Camada DTO/mapper SEPARADA do dominio (ADR-069):

- DOMINIO (`results.py`): Decimal, precisao integral, nenhuma preocupacao
  com transporte;
- HTTP (este modulo): JSON numbers (float), sem strings numericas, SEM
  arredondamento de apresentacao (a Web decide "18,3%", "+8,2 pp", "1,8x").

O mapper apenas CONVERTE valores finais Decimal -> float na borda; nenhuma
metrica e recalculada em float. `null` e preservado quando o conceito esta
indisponivel (weighted_base, lift com referencia zero, intervalo sem base,
contexto eleitoral ausente) — nunca substituido por 0.

Nenhum DTO contem company_id/tenant_id, score, ranking ou projecao: a camada
HTTP nao adiciona o que o motor deliberadamente nao possui.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from pesquisa360.inteligencia_eleitoral.config import (
    BallotSelectionMode,
    GrowthAnalysisConfiguration,
    ProfileDimensionMode,
    ReferencePopulationType,
    SCHEMA_VERSION,
    SignalType,
    TerritoryLevel,
    UncertaintyMethod,
    WeightingMode,
)
from pesquisa360.inteligencia_eleitoral.results import (
    AnalysisWarning,
    EvidenceStatus,
    FavorableDirection,
    FindingStatus,
    GrowthAnalysis,
    GrowthEvidence,
    GrowthFinding,
    ObservedDirection,
    StatisticalInterval,
    TerritorySegmentInfo,
)
from pesquisa360.inteligencia_eleitoral.validation import ConfigurationIssue


def _num(value: Optional[Decimal]) -> Optional[float]:
    """Decimal -> JSON number na borda HTTP. Nunca string; null preservado."""
    return None if value is None else float(value)


# ---------------------------------------------------------------------------
# DTOs de resultado
# ---------------------------------------------------------------------------


class AnalysisWarningDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: str
    message: str
    context: Optional[dict] = None


class StatisticalIntervalDTO(BaseModel):
    """Intervalo em fracao 0-1 (unidade canonica; a Web formata)."""

    model_config = ConfigDict(extra="forbid")

    method: str
    confidence_level: float
    low: float
    high: float


class GrowthEvidenceDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_type: SignalType
    status: EvidenceStatus
    favorable_direction: FavorableDirection
    observed_direction: Optional[ObservedDirection] = None

    segment_numerator: Optional[int] = None
    segment_base_n: Optional[int] = None
    segment_rate: Optional[float] = None
    segment_interval: Optional[StatisticalIntervalDTO] = None

    reference_numerator: Optional[int] = None
    reference_base_n: Optional[int] = None
    reference_rate: Optional[float] = None
    reference_interval: Optional[StatisticalIntervalDTO] = None

    delta_pp: Optional[float] = None
    lift: Optional[float] = None

    warnings: List[AnalysisWarningDTO] = Field(default_factory=list)


class ProfileGroupMembershipDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: int
    dimension_label: str
    group_key: str
    group_label: str


class TerritorySegmentInfoDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: TerritoryLevel
    key: str
    label: str
    # Contexto eleitoral: apenas contexto; nunca insumo de projecao.
    eleitorado_apto: Optional[int] = None


class GrowthFindingDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_key: str
    profile_groups: List[ProfileGroupMembershipDTO]
    territory: Optional[TerritorySegmentInfoDTO] = None
    n_bruto: int
    weighted_base: Optional[int] = None  # sempre null no MVP (D11)
    participation_rate: float
    status: FindingStatus
    evidences: List[GrowthEvidenceDTO] = Field(default_factory=list)
    warnings: List[AnalysisWarningDTO] = Field(default_factory=list)


class AnalysisUniverseSummaryDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    survey_n: int
    analytical_n: int
    eligible_n: int
    current_target_supporters_n: int
    technical_missing_intention_n: int
    excluded_special_n: int
    mixed_special_conflict_n: int
    intention_classification_counts: Dict[str, int] = Field(default_factory=dict)


class TerritoryDiagnosticsDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sem_setor_n: int = 0
    conflito_setor_n: int = 0
    sem_coordenada_n: int = 0
    municipio_nao_resolvido_n: int = 0


class CoverageDiagnosticsDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    not_segmented_n: int = 0
    by_reason: Dict[str, int] = Field(default_factory=dict)


class AnalysisSnapshotDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine_version: str
    schema_version: int
    configuration_hash: str
    input_fingerprint: str
    executed_at: datetime
    pesquisa_id: int
    analytical_universe_n: int
    eligible_universe_n: int


class GrowthAnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: AnalysisSnapshotDTO
    universe: AnalysisUniverseSummaryDTO
    territory_diagnostics: Optional[TerritoryDiagnosticsDTO] = None
    coverage: CoverageDiagnosticsDTO
    findings: List[GrowthFindingDTO] = Field(default_factory=list)
    warnings: List[AnalysisWarningDTO] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# DTOs de validacao e erro
# ---------------------------------------------------------------------------


class GrowthValidationResponse(BaseModel):
    """Resposta da rota de validacao (sempre HTTP 200 para resultado de
    dominio): a UI trata como formulario. `normalized_configuration` reusa o
    contrato canonico (sem Decimal — nao precisa de DTO proprio)."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    normalized_configuration: Optional[GrowthAnalysisConfiguration] = None
    errors: List[ConfigurationIssue] = Field(default_factory=list)
    warnings: List[ConfigurationIssue] = Field(default_factory=list)


class GrowthAnalysisErrorDTO(BaseModel):
    """Corpo tipado do HTTP 422 de POST /analisar."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    errors: List[ConfigurationIssue] = Field(default_factory=list)
    warnings: List[ConfigurationIssue] = Field(default_factory=list)
    context: Optional[dict] = None


# ---------------------------------------------------------------------------
# DTOs de opcoes de configuracao
# ---------------------------------------------------------------------------


class QuestionOptionDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    text: str
    question_type: str
    is_spontaneous: bool
    analytic_role: Optional[str] = None       # sugestao; nunca autoridade
    analytic_metadata: dict = Field(default_factory=dict)
    applicability: str
    compatible_as: List[str] = Field(default_factory=list)
    values: List[str] = Field(default_factory=list)


class SetorOptionDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    nome: str
    finalidade: str
    analytically_eligible: bool


class MunicipioOptionDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    nome: str


class TerritoryOptionsDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supported_levels: List[TerritoryLevel]
    setores: List[SetorOptionDTO] = Field(default_factory=list)
    municipios: List[MunicipioOptionDTO] = Field(default_factory=list)
    municipio_level_available: bool = False


class ConfigurationConstraintsDTO(BaseModel):
    """Regras estaveis do contrato. SEM defaults metodologicos: base minima e
    limiar de espontanea sao sempre parametros explicitos do usuario."""

    model_config = ConfigDict(extra="forbid")

    max_profile_dimensions: int
    supported_ballot_modes: List[BallotSelectionMode]
    supported_signals: List[SignalType]
    supported_profile_modes: List[ProfileDimensionMode]
    supported_territory_levels: List[TerritoryLevel]
    weighting_modes: List[WeightingMode]
    reference_types: List[ReferencePopulationType]
    uncertainty_methods: List[UncertaintyMethod]
    reserved_values: List[str]
    minimum_base_required: bool = True
    spontaneous_quality_required_when_spontaneous_signal: bool = True


class GrowthConfigurationOptionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    engine_version: str
    constraints: ConfigurationConstraintsDTO
    questions: List[QuestionOptionDTO] = Field(default_factory=list)
    territory: TerritoryOptionsDTO


# ---------------------------------------------------------------------------
# Mappers dominio -> HTTP (conversao de valor final, nunca recalculo)
# ---------------------------------------------------------------------------


def _warning_to_http(warning: AnalysisWarning) -> AnalysisWarningDTO:
    return AnalysisWarningDTO(
        code=warning.code,
        severity=warning.severity.value,
        message=warning.message,
        context=warning.context,
    )


def _interval_to_http(interval: Optional[StatisticalInterval]) -> Optional[StatisticalIntervalDTO]:
    if interval is None:
        return None
    return StatisticalIntervalDTO(
        method=interval.method,
        confidence_level=_num(interval.confidence_level),
        low=_num(interval.low),
        high=_num(interval.high),
    )


def _evidence_to_http(evidence: GrowthEvidence) -> GrowthEvidenceDTO:
    return GrowthEvidenceDTO(
        signal_type=evidence.signal_type,
        status=evidence.status,
        favorable_direction=evidence.favorable_direction,
        observed_direction=evidence.observed_direction,
        segment_numerator=evidence.segment_numerator,
        segment_base_n=evidence.segment_base_n,
        segment_rate=_num(evidence.segment_rate),
        segment_interval=_interval_to_http(evidence.segment_interval),
        reference_numerator=evidence.reference_numerator,
        reference_base_n=evidence.reference_base_n,
        reference_rate=_num(evidence.reference_rate),
        reference_interval=_interval_to_http(evidence.reference_interval),
        delta_pp=_num(evidence.delta_pp),
        lift=_num(evidence.lift),
        warnings=[_warning_to_http(item) for item in evidence.warnings],
    )


def _territory_to_http(info: Optional[TerritorySegmentInfo]) -> Optional[TerritorySegmentInfoDTO]:
    if info is None:
        return None
    return TerritorySegmentInfoDTO(
        level=info.level, key=info.key, label=info.label,
        eleitorado_apto=info.eleitorado_apto,
    )


def _finding_to_http(finding: GrowthFinding) -> GrowthFindingDTO:
    return GrowthFindingDTO(
        segment_key=finding.segment_key,
        profile_groups=[
            ProfileGroupMembershipDTO(**item.model_dump())
            for item in finding.profile_groups
        ],
        territory=_territory_to_http(finding.territory),
        n_bruto=finding.n_bruto,
        weighted_base=finding.weighted_base,
        participation_rate=_num(finding.participation_rate),
        status=finding.status,
        evidences=[_evidence_to_http(item) for item in finding.evidences],
        warnings=[_warning_to_http(item) for item in finding.warnings],
    )


def analysis_to_http(analysis: GrowthAnalysis) -> GrowthAnalysisResponse:
    """Mapeia GrowthAnalysis integralmente: nenhum campo metodologicamente
    relevante desaparece; a ordem dos findings e preservada."""
    return GrowthAnalysisResponse(
        snapshot=AnalysisSnapshotDTO(**analysis.snapshot.model_dump()),
        universe=AnalysisUniverseSummaryDTO(**analysis.universe.model_dump()),
        territory_diagnostics=(
            TerritoryDiagnosticsDTO(**analysis.territory_diagnostics.model_dump())
            if analysis.territory_diagnostics is not None
            else None
        ),
        coverage=CoverageDiagnosticsDTO(**analysis.coverage.model_dump()),
        findings=[_finding_to_http(item) for item in analysis.findings],
        warnings=[_warning_to_http(item) for item in analysis.warnings],
    )
