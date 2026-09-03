"""API do Potencial de Crescimento (Inteligencia Eleitoral, MVP 1 — Prompt 04).

Camada HTTP FINA sobre os contratos ja validados
(`pesquisa360/inteligencia_eleitoral/`): nenhuma matematica aqui.

Cadeia de seguranca (ordem garantida pelo composto `growth_access`):

    autenticacao -> resolucao Projeto/Pesquisa + ACL (404, inclusive
    cross-tenant, ANTES de qualquer avaliacao comercial)
    -> Permissao.INTELIGENCIA_VER (403 RBAC)
    -> require_feature("inteligencia_eleitoral", "potencial_crescimento")
       no contexto da Pesquisa (404 capacidade inativa / 403 nao contratada)
    -> endpoint

A feature permanece INATIVA no catalogo real: fora de fixtures de teste, as
rotas respondem 404 CAPACIDADE_INDISPONIVEL ate a ativacao formal do
produto. API implementada != produto liberado.

Execucao sincrona e efemera: nada e persistido; nao existem job_id,
config_id ou analysis_id nesta fase (snapshot carrega configuration_hash e
input_fingerprint).
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from pesquisa360 import crud
from pesquisa360.api.dependencies.modulos import EntitlementContext, require_feature
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.core.rbac import Permissao, require_permissao
from pesquisa360.db import models
from pesquisa360.services import acessos

from pesquisa360.inteligencia_eleitoral.config import GrowthAnalysisConfiguration
from pesquisa360.inteligencia_eleitoral.engine import (
    GrowthAnalysisConfigurationError,
    GrowthSegmentLimitExceededError,
    analyze_growth_potential,
)
from pesquisa360.inteligencia_eleitoral.http_contract import (
    GrowthAnalysisErrorDTO,
    GrowthAnalysisResponse,
    GrowthConfigurationOptionsResponse,
    GrowthValidationResponse,
    analysis_to_http,
)
from pesquisa360.inteligencia_eleitoral.options import build_configuration_options
from pesquisa360.inteligencia_eleitoral.validation import validate_growth_configuration

logger = logging.getLogger(__name__)

MODULO_CHAVE = "inteligencia_eleitoral"
FEATURE_CHAVE = "potencial_crescimento"
SURVEY_PATH_BODY_MISMATCH = "SURVEY_PATH_BODY_MISMATCH"
GROWTH_CONFIGURATION_INVALID = "GROWTH_CONFIGURATION_INVALID"
SEGMENT_LIMIT_EXCEEDED = "SEGMENT_LIMIT_EXCEEDED"

router = APIRouter(
    prefix=(
        "/projetos/{projeto_id}/pesquisas/{pesquisa_id}"
        "/inteligencia-eleitoral/potencial-crescimento"
    ),
    tags=["Inteligencia Eleitoral"],
)


def get_growth_survey_context(
    projeto_id: int,
    pesquisa_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user),
) -> EntitlementContext:
    """Resolve o recurso pelo PATH (autoridade) com ACL/multitenancy.

    Pesquisa inexistente, cross-tenant ou fora do Projeto do path: 404 —
    sempre ANTES de permissao/entitlement."""
    pesquisa = (
        db.query(models.Pesquisa)
        .join(models.Projeto)
        .filter(
            models.Pesquisa.id == pesquisa_id,
            models.Pesquisa.projeto_id == projeto_id,
            acessos.filtro_projeto_acessivel(current_user),
        )
        .first()
    )
    if pesquisa is None:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada.")
    # Somente pesquisa_id no contexto: o resolver de entitlements deriva o
    # projeto pai internamente (Empresa ∪ Projeto pai ∪ Pesquisa) e rejeita
    # contexto com os dois escopos explicitos.
    return EntitlementContext(
        company_id=pesquisa.projeto.company_id,
        pesquisa_id=pesquisa.id,
        recurso=pesquisa,
    )


def growth_access(
    context: EntitlementContext = Depends(get_growth_survey_context),
    _permission: models.Usuario = Depends(require_permissao(Permissao.INTELIGENCIA_VER)),
    gated: EntitlementContext = Depends(
        require_feature(MODULO_CHAVE, FEATURE_CHAVE, get_growth_survey_context)
    ),
) -> EntitlementContext:
    """Ordem garantida: recurso/ACL (404) -> RBAC (403) -> comercial (404/403).

    A dependency de contexto e cacheada pelo FastAPI: uma unica resolucao."""
    return gated


def _ensure_survey_matches(pesquisa_id: int, configuration: GrowthAnalysisConfiguration):
    if configuration.pesquisa_id != pesquisa_id:
        raise HTTPException(
            status_code=422,
            detail={
                "code": SURVEY_PATH_BODY_MISMATCH,
                "message": "pesquisa_id do body difere do pesquisa_id do path.",
                "path_pesquisa_id": pesquisa_id,
                "body_pesquisa_id": configuration.pesquisa_id,
            },
        )


@router.get(
    "/opcoes-configuracao",
    response_model=GrowthConfigurationOptionsResponse,
    summary="Opções reais para configurar a análise de Potencial de Crescimento",
    description=(
        "Perguntas utilizáveis (compatibilidades por regra técnica, nunca por "
        "texto), valores reportáveis canônicos, territórios da Pesquisa e "
        "constraints estáveis do contrato. Sem defaults metodológicos: base "
        "mínima e limiar de espontânea são parâmetros explícitos do usuário."
    ),
)
def opcoes_configuracao(
    context: EntitlementContext = Depends(growth_access),
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user),
) -> GrowthConfigurationOptionsResponse:
    return build_configuration_options(db, current_user, context.pesquisa_id)


@router.post(
    "/validar-configuracao",
    response_model=GrowthValidationResponse,
    summary="Valida uma configuração de análise sem executar o motor",
    description=(
        "Configuração semanticamente inválida NÃO é erro operacional: retorna "
        "HTTP 200 com valid=false e issues tipadas (code/message/path/context) "
        "para uso como formulário. JSON malformado ou contrato Pydantic "
        "impossível (ex.: campo desconhecido, enum inválido) retorna 422. O "
        "motor não é executado e nada é persistido."
    ),
)
def validar_configuracao(
    pesquisa_id: int,
    configuration: GrowthAnalysisConfiguration,
    context: EntitlementContext = Depends(growth_access),
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user),
) -> GrowthValidationResponse:
    _ensure_survey_matches(pesquisa_id, configuration)
    result = validate_growth_configuration(db, current_user, configuration)
    return GrowthValidationResponse(
        valid=result.valid,
        normalized_configuration=result.normalized_configuration,
        errors=result.errors,
        warnings=result.warnings,
    )


@router.post(
    "/analisar",
    response_model=GrowthAnalysisResponse,
    responses={422: {"model": GrowthAnalysisErrorDTO}},
    summary="Executa a análise de Potencial de Crescimento (síncrona, efêmera)",
    description=(
        "Executa o motor estatístico e devolve o GrowthAnalysis completo "
        "(universos, findings, evidências com denominadores explícitos, "
        "warnings metodológicos e snapshot). Métricas em unidade canônica "
        "(rate 0–1, delta em pontos percentuais, lift adimensional) como JSON "
        "numbers, sem arredondamento de apresentação. Configuração inválida "
        "retorna 422 tipado (GROWTH_CONFIGURATION_INVALID); nada é persistido."
    ),
)
def analisar(
    pesquisa_id: int,
    configuration: GrowthAnalysisConfiguration,
    context: EntitlementContext = Depends(growth_access),
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user),
) -> GrowthAnalysisResponse:
    _ensure_survey_matches(pesquisa_id, configuration)
    started = time.monotonic()
    try:
        analysis = analyze_growth_potential(db, current_user, configuration)
    except GrowthAnalysisConfigurationError as exc:
        raise HTTPException(
            status_code=422,
            detail=GrowthAnalysisErrorDTO(
                code=GROWTH_CONFIGURATION_INVALID,
                message="Configuração inválida para execução da análise.",
                errors=exc.validation_result.errors,
                warnings=exc.validation_result.warnings,
            ).model_dump(),
        ) from exc
    except GrowthSegmentLimitExceededError as exc:
        raise HTTPException(
            status_code=422,
            detail=GrowthAnalysisErrorDTO(
                code=SEGMENT_LIMIT_EXCEEDED,
                message=str(exc),
                context={"limit": exc.limit, "found": exc.found},
            ).model_dump(),
        ) from exc
    # Erro inesperado NAO e capturado: segue o fluxo 500/log padrao da app.
    elapsed_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "growth-analysis pesquisa=%s engine=%s config=%s input=%s findings=%s tempo_ms=%s",
        analysis.snapshot.pesquisa_id,
        analysis.snapshot.engine_version,
        analysis.snapshot.configuration_hash[:12],
        analysis.snapshot.input_fingerprint[:12],
        len(analysis.findings),
        elapsed_ms,
    )
    return analysis_to_http(analysis)
