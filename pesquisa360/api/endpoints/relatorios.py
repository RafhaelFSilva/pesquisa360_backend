# pesquisa360/api/endpoints/relatorios.py

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from pesquisa360 import crud, schemas
from pesquisa360.db import models
from pesquisa360.core.dependencies import get_db, get_current_user

router = APIRouter()

@router.get("/pesquisas/{pesquisa_id}/simples/", response_model=schemas.RelatorioPesquisa)
def read_relatorio_simples(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    current_user: models.Usuario = Depends(get_current_user)
):
    """Retorna um relatório agregado simples dos resultados de uma pesquisa."""
    pesquisa = crud.get_pesquisa(db=db, pesquisa_id=pesquisa_id)
    if not pesquisa or pesquisa.projeto.coordenador_id != current_user.id:
        raise HTTPException(status_code=403, detail="Pesquisa não encontrada ou sem permissão")

    relatorio = crud.get_relatorio_pesquisa(db=db, pesquisa_id=pesquisa_id)
    if not relatorio:
         raise HTTPException(status_code=404, detail="Não foi possível gerar o relatório")
    return relatorio

@router.post("/pesquisas/{pesquisa_id}/crosstab/", response_model=schemas.CrosstabResponse)
def read_relatorio_crosstab(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    crosstab_in: schemas.CrosstabRequest,
    current_user: models.Usuario = Depends(get_current_user)
):
    """Retorna um relatório de cruzamento de dados (crosstab) entre duas perguntas."""
    pesquisa = crud.get_pesquisa(db=db, pesquisa_id=pesquisa_id)
    if not pesquisa or pesquisa.projeto.coordenador_id != current_user.id:
        raise HTTPException(status_code=403, detail="Pesquisa não encontrada ou sem permissão")

    relatorio = crud.get_relatorio_crosstab(
        db=db, 
        pesquisa_id=pesquisa_id, 
        pergunta_linha_id=crosstab_in.pergunta_linha_id,
        pergunta_coluna_id=crosstab_in.pergunta_coluna_id
    )
    if not relatorio:
        raise HTTPException(status_code=404, detail="Não foi possível gerar o relatório de cruzamento.")
    return relatorio