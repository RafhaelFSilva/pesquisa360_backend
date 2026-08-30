"""Tentativas de campo (PROMPT 03).

Abordagem operacional do agente, sincronizada pelo Mobile offline-first.
Segue o contrato de `/pesquisas/{id}/coletas/`: pesquisa validada no tenant
(404 fora dele), agente/empresa vindos do token, idempotencia por client_uuid.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from pesquisa360 import crud, schemas
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.db import models
from pesquisa360.core.rbac import Permissao, require_permissao

router = APIRouter()


@router.post(
    "/pesquisas/{pesquisa_id}/tentativas-campo/",
    status_code=status.HTTP_201_CREATED,
    response_model=schemas.TentativaCampoRead, dependencies=[Depends(require_permissao(Permissao.COLETA_ENVIAR))])
def submit_tentativa_campo(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    tentativa_in: schemas.TentativaCampoCreate,
    current_user: models.Usuario = Depends(get_current_user),
):
    pesquisa = crud.get_pesquisa(db=db, pesquisa_id=pesquisa_id, current_user=current_user)
    if not pesquisa:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada ou acesso negado")

    return crud.create_tentativa_campo(
        db=db,
        tentativa_in=tentativa_in,
        pesquisa_id=pesquisa_id,
        current_user=current_user,
    )
