from typing import List, Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
import json

from pesquisa360 import crud, schemas
from pesquisa360.db import models
from pesquisa360.core.dependencies import get_db, get_current_user

router = APIRouter()

def ordenar_perguntas_para_sync(projetos):
    for projeto in projetos or []:
        for pesquisa in getattr(projeto, "pesquisas", []) or []:
            perguntas = getattr(pesquisa, "perguntas", None)
            if perguntas:
                perguntas.sort(key=lambda pergunta: (pergunta.ordem, pergunta.id))
    return projetos

@router.get("/pesquisas/", response_model=List[schemas.ProjetoSync])
def read_pesquisas_para_sincronizar(
    *,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """
    Lista projetos e pesquisas para o App Mobile sincronizar.
    AGORA FILTRA APENAS PROJETOS DA EMPRESA DO AGENTE.
    """
    # Você precisará garantir que esta função no CRUD receba o current_user
    # Se ela não existir no CRUD novo, use get_projetos comum.
    try:
        projetos = crud.get_projetos_em_campo(db=db, current_user=current_user)
    except AttributeError:
        # Fallback caso você ainda não tenha criado 'get_projetos_em_campo' no CRUD novo
        projetos = crud.get_projetos(db=db, current_user=current_user)
        
    return ordenar_perguntas_para_sync(projetos)

@router.get("/missao/{pesquisa_id}")
def get_missao_agente(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    current_user: models.Usuario = Depends(get_current_user)
):
    """
    Retorna a missão (setor/cota) específica para o agente logado.
    """
    # 1. SEGURANÇA: Verifica se a pesquisa pertence à empresa do agente
    pesquisa = crud.get_pesquisa(db=db, pesquisa_id=pesquisa_id, current_user=current_user)
    if not pesquisa:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada ou acesso negado.")

    # 2. Busca o setor do agente
    setor = db.query(models.Setor).filter(
        models.Setor.pesquisa_id == pesquisa_id,
        models.Setor.agente_id == current_user.id
    ).first()

    if not setor:
        return {
            "tem_setor": False, 
            "mensagem": "Você não possui um setor designado nesta pesquisa."
        }

    # 3. Calcula o progresso (Cota)
    coletas_realizadas = db.query(func.count(models.Coleta.id)).filter(
        models.Coleta.pesquisa_id == pesquisa_id,
        models.Coleta.agente_id == current_user.id
    ).scalar()

    # 4. Converte a geometria para GeoJSON
    geojson = None
    if setor.geometria is not None:
        # PostGIS function para converter WKB para GeoJSON
        cerca_str = db.query(func.ST_AsGeoJSON(setor.geometria)).scalar()
        if cerca_str:
            geojson = json.loads(cerca_str)
            
    return {
        "tem_setor": True,
        "setor_id": setor.id,
        "setor_nome": setor.nome,
        "meta": setor.meta,
        "realizado": coletas_realizadas,
        "restante": max(0, setor.meta - coletas_realizadas),
        "tolerancia_metros": setor.tolerancia,
        "geometria": geojson 
    }
