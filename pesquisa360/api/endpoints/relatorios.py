from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from pesquisa360 import crud, schemas
from pesquisa360.db import models
from pesquisa360.core.dependencies import get_db, get_current_user

router = APIRouter()

def check_access(db: Session, pesquisa_id: int, current_user: models.Usuario):
    """
    Helper para validar se a pesquisa pertence à empresa do usuário.
    """
    pesquisa = db.query(models.Pesquisa).join(models.Projeto).filter(
        models.Pesquisa.id == pesquisa_id,
        models.Projeto.company_id == current_user.company_id
    ).first()
    
    if not pesquisa:
        raise HTTPException(
            status_code=403, 
            detail="Pesquisa não encontrada ou você não tem permissão para visualizar este relatório."
        )
    return pesquisa

@router.get("/pesquisas/{pesquisa_id}/simples/", response_model=schemas.RelatorioPesquisa)
def read_relatorio_simples(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    current_user: models.Usuario = Depends(get_current_user)
):
    """
    Retorna um relatório agregado simples.
    FILTRO: Apenas usuários da mesma empresa do projeto podem visualizar.
    """
    # 1. Validação de Segurança (Empresa)
    check_access(db, pesquisa_id, current_user)

    # 2. Geração do Relatório
    # (O crud.get_relatorio_pesquisa lê dados brutos, mas já validamos o acesso acima)
    relatorio = crud.get_relatorio_pesquisa(db=db, pesquisa_id=pesquisa_id)
    
    if not relatorio:
         raise HTTPException(status_code=404, detail="Não foi possível gerar o relatório (sem dados ou erro interno)")
         
    return relatorio

@router.post("/pesquisas/{pesquisa_id}/crosstab/", response_model=schemas.CrosstabResponse)
def read_relatorio_crosstab(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    crosstab_in: schemas.CrosstabRequest,
    current_user: models.Usuario = Depends(get_current_user)
):
    """
    Retorna tabulação cruzada (Crosstab).
    FILTRO: Apenas usuários da mesma empresa.
    """
    # 1. Validação de Segurança
    check_access(db, pesquisa_id, current_user)

    # 2. Geração do Relatório
    # Nota: Atualizamos a chamada do CRUD para passar o current_user se necessário,
    # ou confiamos na validação acima. O CRUD enviado anteriormente recebia current_user.
    try:
        dados_crosstab = crud.get_report_crosstab(
            db=db, 
            pesquisa_id=pesquisa_id,
            pergunta_linha_id=crosstab_in.pergunta_linha_id,
            pergunta_coluna_id=crosstab_in.pergunta_coluna_id,
            current_user=current_user
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    return {
        "pesquisa_id": pesquisa_id,
        "pergunta_linha_id": crosstab_in.pergunta_linha_id,
        "pergunta_coluna_id": crosstab_in.pergunta_coluna_id,
        "data": dados_crosstab
    }