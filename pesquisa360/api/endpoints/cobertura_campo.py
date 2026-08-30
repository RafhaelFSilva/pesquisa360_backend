"""Cobertura territorial de campo (PROMPT 06).

GET read-only para o agente (eventos conhecidos dos seus setores) e
configuracao administrativa da distancia recomendada entre abordagens.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from pesquisa360 import crud, schemas
from pesquisa360.core.dependencies import get_current_user, get_db, require_manager_or_superadmin
from pesquisa360.db import models
from pesquisa360.services import cobertura_campo
from pesquisa360.core.rbac import Permissao, require_permissao

router = APIRouter()


@router.get(
    "/agente/pesquisas/{pesquisa_id}/cobertura-campo/",
    response_model=schemas.CoberturaCampoRead, dependencies=[Depends(require_permissao(Permissao.MISSAO_SINCRONIZAR))])
def obter_cobertura_campo(
    pesquisa_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user),
):
    """Atividade de campo CONHECIDA (snapshot), sem dados pessoais nem
    identificacao de agentes. Pesquisa fora do tenant -> 404."""
    pesquisa = crud.get_pesquisa(db=db, pesquisa_id=pesquisa_id, current_user=current_user)
    if not pesquisa:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada ou acesso negado.")
    return cobertura_campo.cobertura_para_agente(db, pesquisa_id, current_user)


_CONFIG = "/projetos/{projeto_id}/pesquisas/{pesquisa_id}/configuracao-campo"


@router.get(_CONFIG, response_model=schemas.ConfiguracaoCampoRead)
def obter_configuracao_campo(
    projeto_id: int,
    pesquisa_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_manager_or_superadmin),
):
    pesquisa = crud.get_pesquisa(db=db, pesquisa_id=pesquisa_id, current_user=current_user)
    if not pesquisa or pesquisa.projeto_id != projeto_id:
        raise HTTPException(status_code=404, detail="Pesquisa nao encontrada.")
    distancia, configurada = cobertura_campo.distancia_recomendada(db, pesquisa_id)
    return {
        "pesquisa_id": pesquisa_id,
        "distancia_recomendada_entre_abordagens_metros": distancia,
        "distancia_configurada": configurada,
    }


@router.put(_CONFIG, response_model=schemas.ConfiguracaoCampoRead)
def definir_configuracao_campo(
    projeto_id: int,
    pesquisa_id: int,
    payload: schemas.ConfiguracaoCampoRequest,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_manager_or_superadmin),
):
    cobertura_campo.definir_configuracao_campo(db, projeto_id, pesquisa_id, payload, current_user)
    distancia, configurada = cobertura_campo.distancia_recomendada(db, pesquisa_id)
    return {
        "pesquisa_id": pesquisa_id,
        "distancia_recomendada_entre_abordagens_metros": distancia,
        "distancia_configurada": configurada,
    }
