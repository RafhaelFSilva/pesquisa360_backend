"""Opcoes reais de configuracao do Potencial de Crescimento (Prompt 04).

Constroi o payload do GET opcoes-configuracao: perguntas utilizaveis com
compatibilidades calculadas por REGRA TECNICA explicita (tipo canonico +
espontaneidade + aplicabilidade + contrato do produto — NUNCA texto),
valores reportaveis canonicos, territorios da Pesquisa e constraints
estaveis do contrato. Sem defaults metodologicos (base minima e limiar de
espontanea sao sempre parametros explicitos).

Carregamento em lote: uma query de perguntas, uma de opcoes, uma de
categorias espontaneas ativas, uma de setores e a resolucao municipal
oficial — nada por pergunta.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from sqlalchemy.orm import Session

from pesquisa360 import crud
from pesquisa360.db import models
from pesquisa360.question_types import normalize_question_type
from pesquisa360.services import pergunta_territorio

from pesquisa360.inteligencia_eleitoral.config import (
    BallotSelectionMode,
    ProfileDimensionMode,
    RESERVED_TECHNICAL_VALUES,
    ReferencePopulationType,
    SignalType,
    TerritoryLevel,
    UncertaintyMethod,
    WeightingMode,
)
from pesquisa360.inteligencia_eleitoral.engine import GROWTH_ENGINE_VERSION
from pesquisa360.inteligencia_eleitoral.http_contract import (
    ConfigurationConstraintsDTO,
    GrowthConfigurationOptionsResponse,
    MunicipioOptionDTO,
    QuestionOptionDTO,
    SetorOptionDTO,
    TerritoryOptionsDTO,
)

# Papeis de compatibilidade expostos ao Web (contrato do produto).
COMPAT_INTENTION = "INTENTION"
COMPAT_REJECTION = "REJECTION"
COMPAT_SECOND_OPTION = "SECOND_OPTION"
COMPAT_VOTE_DECISION = "VOTE_DECISION"
COMPAT_PROFILE_CATEGORICAL = "PROFILE_CATEGORICAL"
COMPAT_PROFILE_NUMERIC = "PROFILE_NUMERIC"
COMPAT_RESPONSE_FILTER = "RESPONSE_FILTER"

_SIGNAL_COMPATS = (
    COMPAT_INTENTION,
    COMPAT_REJECTION,
    COMPAT_SECOND_OPTION,
    COMPAT_VOTE_DECISION,
)


def _compatibilities(question: models.Pergunta) -> List[str]:
    """Regra tecnica explicita, espelhando a matriz do validator (doc/15 §8).

    Pergunta TERRITORIAL nunca recebe compatibilidade de SINAL (D14);
    `papel_analitico` NAO participa — e apenas metadata de sugestao."""
    tipo = normalize_question_type(question.tipo_pergunta)
    spontaneous = bool(question.eh_resposta_espontanea)
    territorial = question.aplicabilidade == pergunta_territorio.APLICABILIDADE_TERRITORIAL

    compatible: List[str] = []
    categorical_like = spontaneous or tipo in ("ESCOLHA_SIMPLES", "MULTIPLA_ESCOLHA")
    if categorical_like and not territorial:
        compatible += [COMPAT_INTENTION, COMPAT_REJECTION, COMPAT_SECOND_OPTION]
        if tipo == "ESCOLHA_SIMPLES" and not spontaneous:
            compatible.append(COMPAT_VOTE_DECISION)
    if spontaneous or (tipo == "ESCOLHA_SIMPLES" and not spontaneous):
        compatible.append(COMPAT_PROFILE_CATEGORICAL)
    if tipo == "NUMERO" and not spontaneous:
        compatible.append(COMPAT_PROFILE_NUMERIC)
    if categorical_like:
        compatible.append(COMPAT_RESPONSE_FILTER)
    assert not (territorial and set(compatible) & set(_SIGNAL_COMPATS))
    return compatible


def build_configuration_options(
    db: Session, current_user, pesquisa_id: int
) -> GrowthConfigurationOptionsResponse:
    questions = (
        db.query(models.Pergunta)
        .filter(
            models.Pergunta.pesquisa_id == pesquisa_id,
            models.Pergunta.ativo.is_(True),
        )
        .order_by(models.Pergunta.ordem, models.Pergunta.id)
        .all()
    )

    option_rows = (
        db.query(models.Opcao.pergunta_id, models.Opcao.texto)
        .filter(models.Opcao.pergunta_id.in_([q.id for q in questions] or [0]))
        .order_by(models.Opcao.pergunta_id, models.Opcao.ordem, models.Opcao.id)
        .all()
    )
    values_by_question: Dict[int, List[str]] = defaultdict(list)
    for question_id, texto in option_rows:
        if texto and texto not in values_by_question[question_id]:
            values_by_question[question_id].append(texto)

    active_categories = [
        nome
        for (nome,) in db.query(models.CategoriaRespostaEspontanea.nome)
        .filter(
            models.CategoriaRespostaEspontanea.pesquisa_id == pesquisa_id,
            models.CategoriaRespostaEspontanea.ativo.is_(True),
        )
        .order_by(models.CategoriaRespostaEspontanea.nome)
        .all()
    ]

    question_dtos = []
    for question in questions:
        if bool(question.eh_resposta_espontanea):
            values = list(active_categories)
        else:
            values = values_by_question.get(question.id, [])
        question_dtos.append(
            QuestionOptionDTO(
                id=question.id,
                text=question.texto_pergunta,
                question_type=normalize_question_type(question.tipo_pergunta),
                is_spontaneous=bool(question.eh_resposta_espontanea),
                analytic_role=question.papel_analitico,
                analytic_metadata=question.metadados_analiticos or {},
                applicability=question.aplicabilidade,
                compatible_as=_compatibilities(question),
                values=values,
            )
        )

    setores = (
        db.query(
            models.Setor.id,
            models.Setor.nome,
            models.Setor.finalidade,
            models.Setor.geometria.isnot(None).label("tem_geometria"),
        )
        .filter(models.Setor.pesquisa_id == pesquisa_id)
        .order_by(models.Setor.nome, models.Setor.id)
        .all()
    )
    setor_dtos = [
        SetorOptionDTO(
            id=row.id,
            nome=row.nome,
            finalidade=row.finalidade,
            analytically_eligible=(
                row.finalidade in crud.FINALIDADES_ANALITICAS and bool(row.tem_geometria)
            ),
        )
        for row in setores
    ]

    analytical_ids = [row.id for row in setores if row.finalidade in crud.FINALIDADES_ANALITICAS]
    municipios: Dict[int, str] = {}
    if analytical_ids:
        for resolucao in pergunta_territorio.resolver_municipios_setores(
            db, analytical_ids
        ).values():
            if resolucao.resolvido:
                municipios[resolucao.municipio.id] = resolucao.municipio.nome
    municipio_dtos = [
        MunicipioOptionDTO(id=municipio_id, nome=nome)
        for municipio_id, nome in sorted(municipios.items(), key=lambda item: (item[1], item[0]))
    ]

    constraints = ConfigurationConstraintsDTO(
        max_profile_dimensions=2,
        supported_ballot_modes=list(BallotSelectionMode),
        supported_signals=[
            SignalType.REJECTION, SignalType.SECOND_OPTION, SignalType.VOTE_DECISION
        ],
        supported_profile_modes=list(ProfileDimensionMode),
        supported_territory_levels=list(TerritoryLevel),
        weighting_modes=list(WeightingMode),
        reference_types=list(ReferencePopulationType),
        uncertainty_methods=list(UncertaintyMethod),
        reserved_values=sorted(RESERVED_TECHNICAL_VALUES),
    )

    return GrowthConfigurationOptionsResponse(
        engine_version=GROWTH_ENGINE_VERSION,
        constraints=constraints,
        questions=question_dtos,
        territory=TerritoryOptionsDTO(
            supported_levels=list(TerritoryLevel),
            setores=setor_dtos,
            municipios=municipio_dtos,
            municipio_level_available=bool(municipio_dtos),
        ),
    )
