"""Contrato de configuracao do Potencial de Crescimento (MVP 1 — Prompt 02).

Este modulo define o CONFIGURATION CONTRACT: um modelo Pydantic fortemente
tipado, serializavel e deterministico, independente de HTTP, ORM, Web e
persistencia. O motor estatistico (Prompt 03) recebera uma configuracao ja
validada por `validation.validate_growth_configuration`.

Invariantes estruturais (decisoes D01–D15 do doc/14):

- nao existe `company_id`/`tenant_id` no contrato (tenant vem do contexto
  autorizado, nunca do payload analitico);
- nenhuma semantica e inferida por texto de pergunta/opcao: toda equivalencia
  de valores e declarada explicitamente (bindings e taxonomias);
- a fonte da verdade das perguntas de intencao e `ElectoralScenario`;
  `signals` contem apenas os sinais adicionais (rejeicao, segunda opcao,
  decisao do voto);
- os valores da candidatura vivem exclusivamente em
  `TargetCandidacy.bindings` (uma unica fonte da verdade; sinais candidato-
  especificos exigem binding correspondente — validado na camada de dominio);
- `weighting.mode` aceita somente `NAO_PONDERADO` (D11). O contrato NAO
  fabrica `weighted_base = n_bruto`: base ponderada e conceito do resultado
  futuro e permanece indisponivel/null no modo nao ponderado;
- `minimum_base` nao possui default numerico (D04): os limiares sao sempre
  parametros explicitos;
- `__SEM_RESPOSTA__` e categoria tecnica de ausencia e nunca pode ser
  configurada como valor (D09 / secao 26 do Prompt 02).

Erros estruturais levantados pelos validators usam o prefixo "CODIGO::" na
mensagem para que `validation.parse_growth_analysis_configuration` produza
issues tipadas sem depender de parsing de texto livre.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pesquisa360.utils.response_normalization import normalizar_resposta_espontanea

SCHEMA_VERSION = 1

# Categoria tecnica de ausencia de resposta (multidimensional_cross.SEM_RESPOSTA).
RESERVED_TECHNICAL_VALUES = frozenset({"__SEM_RESPOSTA__"})


# ---------------------------------------------------------------------------
# Enums de dominio
# ---------------------------------------------------------------------------


class BallotSelectionMode(StrEnum):
    SINGLE = "SINGLE"
    MULTIPLE = "MULTIPLE"
    ORDERED_MULTIPLE = "ORDERED_MULTIPLE"


class SignalType(StrEnum):
    INTENTION = "INTENTION"
    REJECTION = "REJECTION"
    SECOND_OPTION = "SECOND_OPTION"
    VOTE_DECISION = "VOTE_DECISION"


class IntentionSpecialClass(StrEnum):
    INDECISO_DECLARADO = "INDECISO_DECLARADO"
    BRANCO_NULO = "BRANCO_NULO"
    NS_NR = "NS_NR"
    NAO_PRETENDE_VOTAR = "NAO_PRETENDE_VOTAR"


class ProfileDimensionMode(StrEnum):
    CATEGORICAL = "CATEGORICAL"
    NUMERIC_RANGES = "NUMERIC_RANGES"


class TerritoryLevel(StrEnum):
    NONE = "NONE"
    SETOR = "SETOR"
    MUNICIPIO = "MUNICIPIO"


class WeightingMode(StrEnum):
    # Unico modo do MVP (D11). PONDERADO nao existe ainda de proposito: sua
    # inclusao exigira origem de peso declarada, nunca um default silencioso.
    NAO_PONDERADO = "NAO_PONDERADO"


class ReferencePopulationType(StrEnum):
    ELIGIBLE_UNIVERSE = "ELIGIBLE_UNIVERSE"


class UncertaintyMethod(StrEnum):
    # Aproximacao sob hipotese de Amostragem Aleatoria Simples; nao incorpora
    # estratos, clusters, PSU ou desenho complexo (D07).
    WILSON_AAS_APPROX = "WILSON_AAS_APPROX"


# ---------------------------------------------------------------------------
# Helpers estruturais (puros — sem banco, sem heuristica)
# ---------------------------------------------------------------------------


def _clean_values(values: List[str], *, path: str) -> List[str]:
    """Strip + dedup preservando a ordem; rejeita vazio e valor reservado."""
    cleaned: List[str] = []
    seen: set[str] = set()
    for value in values:
        stripped = value.strip()
        if not stripped:
            raise ValueError(f"EMPTY_VALUE::{path} nao pode conter valor vazio")
        if stripped in RESERVED_TECHNICAL_VALUES:
            raise ValueError(
                f"RESERVED_VALUE::{path} nao pode conter a categoria tecnica {stripped!r}"
            )
        key = normalizar_resposta_espontanea(stripped)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(stripped)
    return cleaned


def _normalized_set(values: List[str]) -> set[str]:
    return {normalizar_resposta_espontanea(value) for value in values}


# ---------------------------------------------------------------------------
# Candidatura e cenario
# ---------------------------------------------------------------------------


class QuestionValueBinding(BaseModel):
    """Equivalencia explicita candidatura -> valores reportaveis de UMA pergunta."""

    model_config = ConfigDict(extra="forbid")

    question_id: int = Field(gt=0)
    values: List[str] = Field(min_length=1)

    @field_validator("values")
    @classmethod
    def clean_values(cls, value: List[str]) -> List[str]:
        return _clean_values(value, path="values")


class TargetCandidacy(BaseModel):
    """Candidatura-alvo declarada. Nao existe entidade Candidato no banco; a
    identidade da candidatura em cada pergunta e um binding explicito."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    cargo: str = Field(min_length=1)
    bindings: List[QuestionValueBinding] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_binding_questions(self) -> "TargetCandidacy":
        ids = [binding.question_id for binding in self.bindings]
        if len(ids) != len(set(ids)):
            raise ValueError(
                "DUPLICATE_BINDING_QUESTION::bindings nao pode repetir question_id"
            )
        return self

    def values_for(self, question_id: int) -> Optional[List[str]]:
        for binding in self.bindings:
            if binding.question_id == question_id:
                return binding.values
        return None


class IntentionQuestionBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: int = Field(gt=0)
    slot: str = Field(min_length=1)
    order: Optional[int] = Field(default=None, ge=1)

    @field_validator("slot")
    @classmethod
    def clean_slot(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("EMPTY_VALUE::slot nao pode ser vazio")
        return stripped


class ElectoralScenario(BaseModel):
    """Conjunto explicitamente configurado de perguntas de intencao que
    descreve uma situacao eleitoral analisavel. Fonte da verdade da intencao
    (o sinal INTENTION nao aparece em `signals`)."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    ballot_selection_mode: BallotSelectionMode
    intention_questions: List[IntentionQuestionBinding] = Field(min_length=1)

    @model_validator(mode="after")
    def coherent_slots(self) -> "ElectoralScenario":
        question_ids = [item.question_id for item in self.intention_questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError(
                "SCENARIO_SLOTS_INCOHERENT::intention_questions nao pode repetir question_id"
            )
        slots = [item.slot for item in self.intention_questions]
        if len(slots) != len(set(slots)):
            raise ValueError(
                "SCENARIO_SLOTS_INCOHERENT::intention_questions nao pode repetir slot"
            )
        if self.ballot_selection_mode == BallotSelectionMode.SINGLE:
            if len(self.intention_questions) != 1:
                raise ValueError(
                    "SCENARIO_SLOTS_INCOHERENT::modo SINGLE exige exatamente uma pergunta de intencao"
                )
        if self.ballot_selection_mode == BallotSelectionMode.ORDERED_MULTIPLE:
            orders = [item.order for item in self.intention_questions]
            if len(self.intention_questions) < 2:
                raise ValueError(
                    "SCENARIO_SLOTS_INCOHERENT::modo ORDERED_MULTIPLE exige duas ou mais perguntas de intencao"
                )
            if any(order is None for order in orders):
                raise ValueError(
                    "SCENARIO_SLOTS_INCOHERENT::modo ORDERED_MULTIPLE exige order em toda pergunta de intencao"
                )
            if sorted(orders) != list(range(1, len(orders) + 1)):
                raise ValueError(
                    "SCENARIO_SLOTS_INCOHERENT::orders devem ser unicos e consecutivos a partir de 1"
                )
        return self


# ---------------------------------------------------------------------------
# Taxonomia de intencao e elegibilidade
# ---------------------------------------------------------------------------


class IntentionTaxonomy(BaseModel):
    """Categorias especiais da(s) pergunta(s) de intencao, por listas
    EXPLICITAS de valores (D09). Lista vazia significa que a pesquisa nao
    possui aquela categoria — nunca que ela sera inferida."""

    model_config = ConfigDict(extra="forbid")

    indeciso_declarado: List[str] = Field(default_factory=list)
    branco_nulo: List[str] = Field(default_factory=list)
    ns_nr: List[str] = Field(default_factory=list)
    nao_pretende_votar: List[str] = Field(default_factory=list)

    @field_validator("indeciso_declarado", "branco_nulo", "ns_nr", "nao_pretende_votar")
    @classmethod
    def clean_values(cls, value: List[str], info) -> List[str]:
        return _clean_values(value, path=info.field_name)

    @model_validator(mode="after")
    def disjoint_classes(self) -> "IntentionTaxonomy":
        classes = {
            IntentionSpecialClass.INDECISO_DECLARADO: self.indeciso_declarado,
            IntentionSpecialClass.BRANCO_NULO: self.branco_nulo,
            IntentionSpecialClass.NS_NR: self.ns_nr,
            IntentionSpecialClass.NAO_PRETENDE_VOTAR: self.nao_pretende_votar,
        }
        seen: Dict[str, IntentionSpecialClass] = {}
        for special_class, values in classes.items():
            for value in values:
                key = normalizar_resposta_espontanea(value)
                if key in seen:
                    raise ValueError(
                        "OVERLAPPING_TAXONOMY_VALUES::"
                        f"o valor {value!r} aparece em {seen[key].value} e em {special_class.value}"
                    )
                seen[key] = special_class
        return self

    def all_values(self) -> List[str]:
        return (
            list(self.indeciso_declarado)
            + list(self.branco_nulo)
            + list(self.ns_nr)
            + list(self.nao_pretende_votar)
        )


class EligibilityPolicy(BaseModel):
    """Quem participa do universo de crescimento.

    Invariavel D01: quem ja declara voto na candidatura-alvo esta EXCLUIDO do
    universo (pertence a futura Consolidacao da Base). As demais inclusoes sao
    decisoes metodologicas explicitas — sem default silencioso."""

    model_config = ConfigDict(extra="forbid")

    current_target_supporter: Literal["EXCLUDE"] = "EXCLUDE"
    include_indeciso: bool
    include_branco_nulo: bool
    include_ns_nr: bool
    include_nao_pretende_votar: bool


# ---------------------------------------------------------------------------
# Sinais
# ---------------------------------------------------------------------------


class VoteDecisionGroups(BaseModel):
    """Grupos explicitos de valores da pergunta de decisao do voto.

    MOBILE = voto declarado como nao definitivo; CRYSTALLIZED = definitivo.
    Valores fora dos dois grupos sao tratados pelo motor como UNKNOWN/OTHER."""

    model_config = ConfigDict(extra="forbid")

    mobile: List[str] = Field(min_length=1)
    crystallized: List[str] = Field(min_length=1)

    @field_validator("mobile", "crystallized")
    @classmethod
    def clean_values(cls, value: List[str], info) -> List[str]:
        return _clean_values(value, path=info.field_name)

    @model_validator(mode="after")
    def disjoint_groups(self) -> "VoteDecisionGroups":
        if _normalized_set(self.mobile) & _normalized_set(self.crystallized):
            raise ValueError(
                "OVERLAPPING_GROUP_VALUES::mobile e crystallized devem ser disjuntos"
            )
        return self


class SignalDefinition(BaseModel):
    """Sinal adicional configurado (REJECTION, SECOND_OPTION, VOTE_DECISION).

    Sinais candidato-especificos (REJECTION, SECOND_OPTION) NAO carregam
    valores da candidatura: os valores vivem em `TargetCandidacy.bindings`
    (fonte unica da verdade; a camada de dominio exige o binding).
    VOTE_DECISION nao e candidato-especifico e usa `decision_groups`."""

    model_config = ConfigDict(extra="forbid")

    type: SignalType
    question_id: int = Field(gt=0)
    enabled: bool = True
    decision_groups: Optional[VoteDecisionGroups] = None

    @model_validator(mode="after")
    def coherent_signal(self) -> "SignalDefinition":
        if self.type == SignalType.INTENTION:
            raise ValueError(
                "INTENTION_SIGNAL_IN_SIGNALS::intencao e configurada em scenario.intention_questions, nao em signals"
            )
        if self.type == SignalType.VOTE_DECISION and self.decision_groups is None:
            raise ValueError(
                "MISSING_DECISION_GROUPS::VOTE_DECISION exige decision_groups explicitos"
            )
        if self.type != SignalType.VOTE_DECISION and self.decision_groups is not None:
            raise ValueError(
                "UNEXPECTED_DECISION_GROUPS::decision_groups so se aplica a VOTE_DECISION"
            )
        return self


# ---------------------------------------------------------------------------
# Segmentacao de perfil
# ---------------------------------------------------------------------------


class ProfileGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    values: List[str] = Field(min_length=1)

    @field_validator("values")
    @classmethod
    def clean_values(cls, value: List[str]) -> List[str]:
        return _clean_values(value, path="values")


class NumericRange(BaseModel):
    """Faixa numerica com limites INCLUSIVOS nas duas pontas.

    `max = null` significa faixa aberta superior (ex.: "60+")."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    min: int = Field(ge=0)
    max: Optional[int] = None

    @model_validator(mode="after")
    def coherent_bounds(self) -> "NumericRange":
        if self.max is not None and self.max < self.min:
            raise ValueError(
                f"INVALID_NUMERIC_RANGE::faixa {self.key!r} possui max menor que min"
            )
        return self


class ProfileDimension(BaseModel):
    """Dimensao de segmentacao por perfil. Generaliza o padrao existente de
    `PlanoCotaPerfil.sexo_valores`: ponteiro para pergunta + valores/faixas
    explicitos."""

    model_config = ConfigDict(extra="forbid")

    question_id: int = Field(gt=0)
    label: str = Field(min_length=1)
    mode: ProfileDimensionMode
    groups: Optional[List[ProfileGroup]] = None
    ranges: Optional[List[NumericRange]] = None

    @model_validator(mode="after")
    def coherent_mode(self) -> "ProfileDimension":
        if self.mode == ProfileDimensionMode.CATEGORICAL:
            if not self.groups:
                raise ValueError(
                    "INVALID_PROFILE_DIMENSION::modo CATEGORICAL exige groups"
                )
            if self.ranges:
                raise ValueError(
                    "INVALID_PROFILE_DIMENSION::modo CATEGORICAL nao aceita ranges"
                )
            keys = [group.key for group in self.groups]
            if len(keys) != len(set(keys)):
                raise ValueError(
                    "INVALID_PROFILE_DIMENSION::groups nao pode repetir key"
                )
            seen: Dict[str, str] = {}
            for group in self.groups:
                for value in group.values:
                    normalized = normalizar_resposta_espontanea(value)
                    if normalized in seen:
                        raise ValueError(
                            "OVERLAPPING_GROUP_VALUES::"
                            f"o valor {value!r} aparece nos grupos {seen[normalized]!r} e {group.key!r}"
                        )
                    seen[normalized] = group.key
        else:
            if not self.ranges:
                raise ValueError(
                    "INVALID_PROFILE_DIMENSION::modo NUMERIC_RANGES exige ranges"
                )
            if self.groups:
                raise ValueError(
                    "INVALID_PROFILE_DIMENSION::modo NUMERIC_RANGES nao aceita groups"
                )
            keys = [numeric_range.key for numeric_range in self.ranges]
            if len(keys) != len(set(keys)):
                raise ValueError(
                    "INVALID_PROFILE_DIMENSION::ranges nao pode repetir key"
                )
            ordered = sorted(
                self.ranges, key=lambda item: (item.min, item.max is None, item.max or 0)
            )
            for previous, current in zip(ordered, ordered[1:]):
                previous_max = previous.max
                # Limites inclusivos: [18, 24] e [24, 34] SE sobrepoem no 24.
                if previous_max is None or current.min <= previous_max:
                    raise ValueError(
                        "OVERLAPPING_NUMERIC_RANGES::"
                        f"as faixas {previous.key!r} e {current.key!r} se sobrepoem"
                    )
        return self


# ---------------------------------------------------------------------------
# Territorio e filtros estruturais
# ---------------------------------------------------------------------------


class TerritoryConfiguration(BaseModel):
    """Territorio como DIMENSAO DE SEGMENTACAO do resultado (nunca sinal).

    - `setor_ids` vazio no nivel SETOR = todos os setores analiticos elegiveis
      da Pesquisa (finalidade RELATORIO/AMBOS);
    - diferenca para `filters.setor_ids`: o filtro RESTRINGE O UNIVERSO; este
      nivel define se o territorio segmenta o RESULTADO. E valido analisar
      apenas os setores 1,2,3 (filtro) e ainda segmentar o resultado por setor
      (dimensao);
    - `include_electoral_context` pede eleitorado_apto da Base Eleitoral como
      CONTEXTO. Invariavel: contexto nunca vira projecao
      (taxa x eleitorado_apto = votos e PROIBIDO neste produto)."""

    model_config = ConfigDict(extra="forbid")

    level: TerritoryLevel = TerritoryLevel.NONE
    setor_ids: List[int] = Field(default_factory=list)
    include_electoral_context: bool = False

    @field_validator("setor_ids")
    @classmethod
    def sorted_unique(cls, value: List[int]) -> List[int]:
        return sorted(set(value))

    @model_validator(mode="after")
    def coherent_territory(self) -> "TerritoryConfiguration":
        if self.setor_ids and self.level != TerritoryLevel.SETOR:
            raise ValueError(
                "SETORES_ONLY_FOR_SETOR_LEVEL::setor_ids so se aplica ao nivel SETOR"
            )
        if self.include_electoral_context and self.level == TerritoryLevel.NONE:
            raise ValueError(
                "ELECTORAL_CONTEXT_REQUIRES_TERRITORY::contexto eleitoral exige nivel territorial"
            )
        return self


class ResponseFilter(BaseModel):
    """Compativel com a semantica de `services/filtros_universo.py`:
    OR dentro dos valores da mesma pergunta, AND entre perguntas."""

    model_config = ConfigDict(extra="forbid")

    question_id: int = Field(gt=0)
    values: List[str] = Field(min_length=1)

    @field_validator("values")
    @classmethod
    def clean_values(cls, value: List[str]) -> List[str]:
        return _clean_values(value, path="values")


class StructuralFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_ids: List[int] = Field(default_factory=list)
    setor_ids: List[int] = Field(default_factory=list)
    response_filters: List[ResponseFilter] = Field(default_factory=list)

    @field_validator("agent_ids", "setor_ids")
    @classmethod
    def sorted_unique(cls, value: List[int]) -> List[int]:
        return sorted(set(value))

    @model_validator(mode="after")
    def unique_filter_questions(self) -> "StructuralFilters":
        ids = [item.question_id for item in self.response_filters]
        if len(ids) != len(set(ids)):
            raise ValueError(
                "DUPLICATE_FILTER_QUESTION::response_filters nao pode repetir question_id"
            )
        return self

    def as_filtros_respostas(self) -> List[tuple[int, set[str]]]:
        """Forma consumida por `filtros_universo.aplicar_filtros_respostas`."""
        return [
            (item.question_id, set(item.values)) for item in self.response_filters
        ]


# ---------------------------------------------------------------------------
# Politicas
# ---------------------------------------------------------------------------


class WeightingDefinition(BaseModel):
    """D11: MVP nao ponderado por declaracao explicita. O resultado futuro
    devera reportar `n_bruto` real e `weighted_base` INDISPONIVEL (null) —
    nunca `weighted_base = n_bruto`."""

    model_config = ConfigDict(extra="forbid")

    mode: WeightingMode


class MinimumBasePolicy(BaseModel):
    """D04: dois niveis (supressao e alerta) SEM default numerico. Os limiares
    operam sobre o N BRUTO de entrevistas."""

    model_config = ConfigDict(extra="forbid")

    suppress_below_n: int = Field(ge=1)
    warn_below_n: int = Field(ge=1)

    @model_validator(mode="after")
    def coherent_thresholds(self) -> "MinimumBasePolicy":
        if self.warn_below_n <= self.suppress_below_n:
            raise ValueError(
                "INVALID_BASE_THRESHOLDS::warn_below_n deve ser maior que suppress_below_n"
            )
        return self


class ReferencePopulation(BaseModel):
    """D10: referencia unica do MVP e o universo elegivel."""

    model_config = ConfigDict(extra="forbid")

    type: ReferencePopulationType


class UncertaintyPolicy(BaseModel):
    """D07: IC de Wilson como APROXIMACAO sob hipotese de Amostragem Aleatoria
    Simples; nao incorpora estratos, clusters, PSU ou desenho complexo. O
    default 0.95 foi aprovado metodologicamente."""

    model_config = ConfigDict(extra="forbid")

    method: UncertaintyMethod = UncertaintyMethod.WILSON_AAS_APPROX
    confidence_level: float = Field(default=0.95, gt=0.0, lt=1.0)


class SpontaneousQualityPolicy(BaseModel):
    """D15: qualidade de categorizacao espontanea. `max_uncategorized_rate` e
    fracao 0–1 (exclusivos), sem default metodologico; obrigatoria quando
    alguma pergunta espontanea participa como sinal/intencao."""

    model_config = ConfigDict(extra="forbid")

    max_uncategorized_rate: Optional[float] = None

    @field_validator("max_uncategorized_rate")
    @classmethod
    def coherent_rate(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and not (0.0 < value < 1.0):
            raise ValueError(
                "INVALID_SPONTANEOUS_THRESHOLD::max_uncategorized_rate deve estar entre 0 e 1 (exclusivos)"
            )
        return value


# ---------------------------------------------------------------------------
# Contrato canonico
# ---------------------------------------------------------------------------

MAX_PROFILE_DIMENSIONS = 2  # D08 — territorio nao conta como dimensao de perfil.


class GrowthAnalysisConfiguration(BaseModel):
    """Contrato canonico da analise de Potencial de Crescimento.

    Serializavel e deterministico (`model_dump(mode="json")`): listas de IDs
    sao ordenadas/deduplicadas; listas de valores preservam a ordem
    configurada. Sem `company_id` (extra="forbid" rejeita qualquer campo
    desconhecido)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    pesquisa_id: int = Field(gt=0)
    target: TargetCandidacy
    scenario: ElectoralScenario
    intention_taxonomy: IntentionTaxonomy = Field(default_factory=IntentionTaxonomy)
    eligibility: EligibilityPolicy
    signals: List[SignalDefinition] = Field(default_factory=list)
    profile_dimensions: List[ProfileDimension]
    territory: TerritoryConfiguration = Field(default_factory=TerritoryConfiguration)
    filters: StructuralFilters = Field(default_factory=StructuralFilters)
    weighting: WeightingDefinition
    minimum_base: MinimumBasePolicy
    reference: ReferencePopulation
    uncertainty: UncertaintyPolicy = Field(default_factory=UncertaintyPolicy)
    spontaneous_quality: Optional[SpontaneousQualityPolicy] = None

    @field_validator("schema_version")
    @classmethod
    def supported_schema_version(cls, value: int) -> int:
        if value != SCHEMA_VERSION:
            raise ValueError(
                f"UNSUPPORTED_SCHEMA_VERSION::schema_version suportada e {SCHEMA_VERSION}"
            )
        return value

    @model_validator(mode="after")
    def coherent_configuration(self) -> "GrowthAnalysisConfiguration":
        if not self.profile_dimensions:
            raise ValueError(
                "PROFILE_DIMENSION_REQUIRED::a analise exige ao menos uma dimensao de perfil"
            )
        if len(self.profile_dimensions) > MAX_PROFILE_DIMENSIONS:
            raise ValueError(
                "TOO_MANY_PROFILE_DIMENSIONS::"
                f"maximo de {MAX_PROFILE_DIMENSIONS} dimensoes de perfil no MVP"
            )
        dimension_ids = [item.question_id for item in self.profile_dimensions]
        if len(dimension_ids) != len(set(dimension_ids)):
            raise ValueError(
                "INVALID_PROFILE_DIMENSION::profile_dimensions nao pode repetir question_id"
            )

        signal_types = [signal.type for signal in self.signals]
        if len(signal_types) != len(set(signal_types)):
            raise ValueError(
                "DUPLICATE_SIGNAL_TYPE::signals nao pode repetir type"
            )
        signal_question_ids = [signal.question_id for signal in self.signals]
        if len(signal_question_ids) != len(set(signal_question_ids)):
            raise ValueError(
                "DUPLICATE_SIGNAL_QUESTION::signals nao pode repetir question_id"
            )

        # D01/D09: valores da candidatura nas perguntas de intencao nao podem
        # coincidir com nenhuma categoria especial da taxonomia.
        taxonomy_keys = _normalized_set(self.intention_taxonomy.all_values())
        for intention in self.scenario.intention_questions:
            target_values = self.target.values_for(intention.question_id) or []
            for value in target_values:
                if normalizar_resposta_espontanea(value) in taxonomy_keys:
                    raise ValueError(
                        "TARGET_OVERLAPS_TAXONOMY::"
                        f"o valor {value!r} da candidatura tambem esta na taxonomia de intencao"
                    )
        return self

    # ------------------------------------------------------------------
    # Conveniencias de leitura (sem calculo)
    # ------------------------------------------------------------------

    def enabled_signals(self) -> List[SignalDefinition]:
        return [signal for signal in self.signals if signal.enabled]

    def signal_of(self, signal_type: SignalType) -> Optional[SignalDefinition]:
        for signal in self.enabled_signals():
            if signal.type == signal_type:
                return signal
        return None

    def candidate_specific_question_ids(self) -> List[int]:
        """Perguntas que exigem binding da candidatura: intencao + rejeicao +
        segunda opcao. Decisao do voto nao e candidato-especifica (secao 51)."""
        ids = [item.question_id for item in self.scenario.intention_questions]
        for signal in self.enabled_signals():
            if signal.type in (SignalType.REJECTION, SignalType.SECOND_OPTION):
                ids.append(signal.question_id)
        return ids

    def referenced_question_ids(self) -> List[int]:
        """Toda pergunta referenciada, em ordem deterministica."""
        ids: List[int] = [item.question_id for item in self.scenario.intention_questions]
        ids += [signal.question_id for signal in self.enabled_signals()]
        ids += [dimension.question_id for dimension in self.profile_dimensions]
        ids += [item.question_id for item in self.filters.response_filters]
        ids += [binding.question_id for binding in self.target.bindings]
        return sorted(set(ids))
