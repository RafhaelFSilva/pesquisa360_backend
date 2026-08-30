"""Cotas de Perfil amostral (PROMPT 05) -- configuracao administrativa.

Sem painel Web nesta fase: o plano e definido via API por Gerente/Superadmin.
Cota de perfil e ORIENTATIVA; nada aqui bloqueia coleta ou sincronizacao.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from pesquisa360 import schemas
from pesquisa360.core.dependencies import get_db, require_manager_or_superadmin
from pesquisa360.db import models
from pesquisa360.services import cota_perfil

router = APIRouter()

_BASE = "/projetos/{projeto_id}/pesquisas/{pesquisa_id}/cotas-perfil"


@router.get(_BASE, response_model=schemas.PlanoCotaPerfilRead)
def obter_plano_cota_perfil(
    projeto_id: int,
    pesquisa_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_manager_or_superadmin),
):
    plano = cota_perfil.obter_plano(db, projeto_id, pesquisa_id, current_user)
    if plano is None:
        raise HTTPException(status_code=404, detail="Plano de cotas de perfil nao configurado.")
    return cota_perfil.plano_para_leitura(db, plano)


@router.put(_BASE, response_model=schemas.PlanoCotaPerfilRead)
def definir_plano_cota_perfil(
    projeto_id: int,
    pesquisa_id: int,
    payload: schemas.PlanoCotaPerfilRequest,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_manager_or_superadmin),
):
    """Substitui o plano inteiro de forma transacional (PUT idempotente)."""
    plano = cota_perfil.definir_plano(db, projeto_id, pesquisa_id, payload, current_user)
    return cota_perfil.plano_para_leitura(db, plano)


@router.get(_BASE + "/progresso", response_model=schemas.ProgressoCotaPerfilRead)
def obter_progresso_cota_perfil(
    projeto_id: int,
    pesquisa_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_manager_or_superadmin),
):
    """Snapshot completo (com numeros) para gestao/auditoria e painel futuro."""
    plano = cota_perfil.obter_plano(db, projeto_id, pesquisa_id, current_user)
    if plano is None:
        raise HTTPException(status_code=404, detail="Plano de cotas de perfil nao configurado.")
    progresso = cota_perfil.calcular_progresso(db, plano, pesquisa_id)
    for territorio in progresso["territorios"]:
        territorio.pop("prioridades", None)
    return progresso


@router.get(_BASE + "/contexto-territorial", response_model=list[schemas.ContextoTerritorialCota])
def obter_contexto_territorial_cota_perfil(
    projeto_id: int,
    pesquisa_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_manager_or_superadmin),
):
    """ADR-035: setores operacionais e meta territorial consolidada por municipio.

    Nao depende de plano existente: serve a etapa "Municipios" do painel.
    """
    cota_perfil._pesquisa_do_tenant(db, projeto_id, pesquisa_id, current_user)
    return cota_perfil.contexto_territorial(db, pesquisa_id)
