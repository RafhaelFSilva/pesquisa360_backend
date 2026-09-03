"""Modelos de resultado do motor de Potencial de Crescimento (Prompt 03).

Modelos Pydantic independentes de HTTP e de ORM. Nenhum resultado e
persistido nesta fase.

Invariantes (doc/14 D01-D15):

- unidade estatistica: 1 Coleta = 1 observacao (todo `*_n` conta coleta_id
  unicos, nunca linhas de Resposta);
- `weighted_base` e SEMPRE null no MVP nao ponderado (nunca n_bruto
  disfarcado);
- taxas internas sao fracoes 0-1 em Decimal; arredondamento e apresentacao;
- nenhum score, ranking agregado, projecao de votos ou narrativa;
- `observed_direction` e direcao OBSERVADA descritiva, nunca significancia;
- warnings sao tipados (code e autoridade para a Web futura; message e
  apresentacao humana).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from pesquisa360.inteligencia_eleitoral.config import (
    SignalType,
    TerritoryLevel,
)


class FavorableDirection(StrEnum):
    HIGHER_IS_FAVORABLE = "HIGHER_IS_FAVORABLE"
    LOWER_IS_FAVORABLE = "LOWER_IS_FAVORABLE"


class ObservedDirection(StrEnum):
    FAVORABLE = "FAVORABLE"
    UNFAVORABLE = "UNFAVORABLE"
    NEUTRAL = "NEUTRAL"


class IntentionClassification(StrEnum):
    CURRENT_TARGET_SUPPORTER = "CURRENT_TARGET_SUPPORTER"
    REGULAR_ELIGIBLE = "REGULAR_ELIGIBLE"
    INDECISO = "INDECISO"
    BRANCO_NULO = "BRANCO_NULO"
    NS_NR = "NS_NR"
    NAO_PRETENDE_VOTAR = "NAO_PRETENDE_VOTAR"
    TECHNICAL_MISSING = "TECHNICAL_MISSING"
    SPECIAL_CONFLICT = "SPECIAL_CONFLICT"


class EvidenceStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    SUPPRESSED_BASE_INSUFFICIENT = "SUPPRESSED_BASE_INSUFFICIENT"
    SIGNAL_UNAVAILABLE = "SIGNAL_UNAVAILABLE"
    SIGNAL_UNAVAILABLE_QUALITY = "SIGNAL_UNAVAILABLE_QUALITY"


class FindingStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    SUPPRESSED_BASE_INSUFFICIENT = "SUPPRESSED_BASE_INSUFFICIENT"


class WarningSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"


class AnalysisWarningCode(StrEnum):
    # Globais metodologicos
    UNWEIGHTED_ANALYSIS = "UNWEIGHTED_ANALYSIS"
    SRS_ASSUMPTION = "SRS_ASSUMPTION"
    REFERENCE_INCLUDES_SEGMENT = "REFERENCE_INCLUDES_SEGMENT"
    MULTIPLE_COMPARISONS_EXPLORATORY = "MULTIPLE_COMPARISONS_EXPLORATORY"
    # Sinais
    SIGNAL_NOT_CONFIGURED = "SIGNAL_NOT_CONFIGURED"
    SIGNAL_UNAVAILABLE = "SIGNAL_UNAVAILABLE"
    SIGNAL_UNAVAILABLE_QUALITY = "SIGNAL_UNAVAILABLE_QUALITY"
    HIGH_UNCATEGORIZED_RATE = "HIGH_UNCATEGORIZED_RATE"
    LIFT_UNDEFINED_REFERENCE_ZERO = "LIFT_UNDEFINED_REFERENCE_ZERO"
    MULTIPLE_CHOICE_SUM_MAY_EXCEED_100 = "MULTIPLE_CHOICE_SUM_MAY_EXCEED_100"
    # Base
    SMALL_BASE = "SMALL_BASE"
    SUPPRESSED_BASE_INSUFFICIENT = "SUPPRESSED_BASE_INSUFFICIENT"
    # Elegibilidade / segmentacao
    MIXED_SPECIAL_ELIGIBILITY = "MIXED_SPECIAL_ELIGIBILITY"
    OVERLAPPING_SEGMENT_GROUPS = "OVERLAPPING_SEGMENT_GROUPS"
    DUPLICATE_ANSWER_ANOMALY = "DUPLICATE_ANSWER_ANOMALY"
    # Territorio / contexto
    TERRITORY_PARTICIPATION_BELOW_100 = "TERRITORY_PARTICIPATION_BELOW_100"
    ELECTORAL_CONTEXT_UNAVAILABLE = "ELECTORAL_CONTEXT_UNAVAILABLE"
    # Propagados da configuracao
    CONFIGURATION_WARNING = "CONFIGURATION_WARNING"


class AnalysisWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: WarningSeverity = WarningSeverity.WARNING
    message: str
    context: Optional[dict] = None


class StatisticalInterval(BaseModel):
    """Intervalo de proporcao em fracao 0-1 (nunca em pp)."""

    model_config = ConfigDict(extra="forbid")

    method: str
    confidence_level: Decimal
    low: Decimal
    high: Decimal


class GrowthEvidence(BaseModel):
    """Uma evidencia de sinal em um segmento. Nenhum denominador oculto."""

    model_config = ConfigDict(extra="forbid")

    signal_type: SignalType
    status: EvidenceStatus
    favorable_direction: FavorableDirection
    observed_direction: Optional[ObservedDirection] = None

    segment_numerator: Optional[int] = None
    segment_base_n: Optional[int] = None
    segment_rate: Optional[Decimal] = None
    segment_interval: Optional[StatisticalInterval] = None

    reference_numerator: Optional[int] = None
    reference_base_n: Optional[int] = None
    reference_rate: Optional[Decimal] = None
    reference_interval: Optional[StatisticalInterval] = None

    delta_pp: Optional[Decimal] = None
    lift: Optional[Decimal] = None

    warnings: List[AnalysisWarning] = Field(default_factory=list)


class ProfileGroupMembership(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: int
    dimension_label: str
    group_key: str
    group_label: str


class TerritorySegmentInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: TerritoryLevel
    key: str
    label: str
    # Contexto eleitoral: APENAS contexto, nunca insumo de projecao.
    eleitorado_apto: Optional[int] = None


class GrowthFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_key: str
    profile_groups: List[ProfileGroupMembership]
    territory: Optional[TerritorySegmentInfo] = None
    n_bruto: int
    # D11: sempre null no MVP nao ponderado; nunca n_bruto disfarcado.
    weighted_base: Optional[int] = None
    participation_rate: Decimal
    status: FindingStatus
    evidences: List[GrowthEvidence] = Field(default_factory=list)
    warnings: List[AnalysisWarning] = Field(default_factory=list)


class AnalysisUniverseSummary(BaseModel):
    """Explica 'de N entrevistas, por que so M entraram no crescimento?'."""

    model_config = ConfigDict(extra="forbid")

    survey_n: int
    analytical_n: int
    eligible_n: int
    current_target_supporters_n: int
    technical_missing_intention_n: int
    excluded_special_n: int
    mixed_special_conflict_n: int
    # Contagem por classificacao final de intencao (coleta_id unicos).
    intention_classification_counts: Dict[str, int] = Field(default_factory=dict)


class TerritoryDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sem_setor_n: int = 0
    conflito_setor_n: int = 0
    sem_coordenada_n: int = 0
    municipio_nao_resolvido_n: int = 0


class CoverageDiagnostics(BaseModel):
    """Entrevistas elegiveis que nao entraram em nenhum segmento, por motivo.

    Nada e descartado em silencio: a soma da participacao dos segmentos pode
    ficar abaixo de 100% e estes numeros explicam a diferenca."""

    model_config = ConfigDict(extra="forbid")

    not_segmented_n: int = 0
    by_reason: Dict[str, int] = Field(default_factory=dict)


class AnalysisSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine_version: str
    schema_version: int
    configuration_hash: str
    input_fingerprint: str
    executed_at: datetime
    pesquisa_id: int
    analytical_universe_n: int
    eligible_universe_n: int


class GrowthAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: AnalysisSnapshot
    universe: AnalysisUniverseSummary
    territory_diagnostics: Optional[TerritoryDiagnostics] = None
    coverage: CoverageDiagnostics
    findings: List[GrowthFinding] = Field(default_factory=list)
    warnings: List[AnalysisWarning] = Field(default_factory=list)
