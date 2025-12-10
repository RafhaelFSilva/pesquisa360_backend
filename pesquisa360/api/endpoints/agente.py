# pesquisa360/api/endpoints/agente.py

import json
from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from pesquisa360 import crud, schemas
from pesquisa360.db import models
from pesquisa360.core.dependencies import get_db, get_current_user

router = APIRouter()

# --- ENDPOINT EXISTENTE (Mantivemos) ---
@router.get("/pesquisas/", response_model=List[schemas.ProjetoSync])
def read_pesquisas_para_sincronizar(
    *,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """
    Endpoint para o agente baixar todos os projetos e pesquisas ativas.
    Retorna uma lista de projetos com suas pesquisas e perguntas aninhadas.
    """
    projetos = crud.get_projetos_em_campo(db=db)
    return projetos

# --- NOVO ENDPOINT (Adicione isto) ---
@router.get("/missao/{pesquisa_id}")
def get_missao_agente(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    current_user: models.Usuario = Depends(get_current_user)
):
    """
    Retorna os dados vitais para o App Mobile trabalhar no modo 'Assistente de Campo':
    - A geometria do setor (Cerca)
    - A tolerância em metros (definida no backend)
    - A cota (Meta) vs Realizado
    """
    
    # 1. Busca o setor do agente
    setor = db.query(models.Setor).filter(
        models.Setor.pesquisa_id == pesquisa_id,
        models.Setor.agente_id == current_user.id
    ).first()

    if not setor:
        # Retorna indicando que não há missão específica (setor) definida
        return {
            "tem_setor": False, 
            "mensagem": "Você não possui um setor designado nesta pesquisa."
        }

    # 2. Calcula o progresso (Cota)
    # Contamos quantas coletas esse agente já fez nesta pesquisa
    coletas_realizadas = db.query(func.count(models.Coleta.id)).filter(
        models.Coleta.pesquisa_id == pesquisa_id,
        models.Coleta.agente_id == current_user.id
    ).scalar()

    # 3. Converte a geometria para GeoJSON (se existir)
    geojson = None
    if setor.geometria is not None:
        cerca_str = db.query(func.ST_AsGeoJSON(setor.geometria)).scalar()
        if cerca_str:
            geojson = json.loads(cerca_str)
            
    # 4. Retorno estruturado para o App
    return {
        "tem_setor": True,
        "setor_id": setor.id,
        "setor_nome": setor.nome,
        "meta": setor.meta,
        "realizado": coletas_realizadas,
        "restante": max(0, setor.meta - coletas_realizadas),
        "tolerancia_metros": setor.tolerancia, # O App usará este valor para validar o GPS
        "geometria": geojson 
    }