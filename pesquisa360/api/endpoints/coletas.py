# pesquisa360/api/endpoints/coletas.py
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func

from pesquisa360 import crud, schemas
from pesquisa360.db import models
from pesquisa360.core.dependencies import get_db, get_current_user

router = APIRouter()

@router.post("/pesquisas/{pesquisa_id}/coletas/", status_code=status.HTTP_201_CREATED)
def submit_coleta(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    coleta_in: schemas.ColetaCreate,
    current_user: models.Usuario = Depends(get_current_user)
):
    pesquisa = crud.get_pesquisa(db=db, pesquisa_id=pesquisa_id)
    if not pesquisa:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada")

    agente_id = current_user.id

    db_coleta = crud.create_coleta(
        db=db, coleta=coleta_in, pesquisa_id=pesquisa_id, agente_id=agente_id
    )

    coords = db.query(
        models.Coleta.id,
        func.ST_Y(models.Coleta.localizacao_inicio).label("lat_inicio"),
        func.ST_X(models.Coleta.localizacao_inicio).label("lon_inicio"),
        func.ST_Y(models.Coleta.localizacao_fim).label("lat_fim"),
        func.ST_X(models.Coleta.localizacao_fim).label("lon_fim"),
    ).filter(models.Coleta.id == db_coleta.id).first()

    localizacao_inicio_dict = (
        {"lat": coords.lat_inicio, "lon": coords.lon_inicio}
        if coords.lat_inicio is not None and coords.lon_inicio is not None
        else None
    )
    localizacao_fim_dict = (
        {"lat": coords.lat_fim, "lon": coords.lon_fim}
        if coords.lat_fim is not None and coords.lon_fim is not None
        else None
    )

    response_data = {
        "id": db_coleta.id,
        "pesquisa_id": db_coleta.pesquisa_id,
        "agente_id": db_coleta.agente_id,
        "data_inicio_coleta": db_coleta.data_inicio_coleta.isoformat(),
        "data_fim_coleta": db_coleta.data_fim_coleta.isoformat() if db_coleta.data_fim_coleta else None,
        "localizacao_inicio": localizacao_inicio_dict,
        "localizacao_fim": localizacao_fim_dict,
        "respostas": [
            {
                "id": resp.id,
                "pergunta_id": resp.pergunta_id,
                "coleta_id": resp.coleta_id,
                "valor_resposta": resp.valor_resposta,
            }
            for resp in db_coleta.respostas
        ],
    }

    return JSONResponse(content=response_data)

@router.get(
    "/pesquisas/{pesquisa_id}/coletas/monitoramento/",
    response_model=List[schemas.ColetaMonitoramento],
    summary="Listar Coletas para Monitoramento"
)
def get_coletas_for_monitoring(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    current_user: models.Usuario = Depends(get_current_user)
):
    """
    Retorna uma lista otimizada de coletas de uma pesquisa para
    serem exibidas no painel de monitoramento.
    Apenas o coordenador do projeto pode acessar estes dados.
    """
    pesquisa = crud.get_pesquisa(db=db, pesquisa_id=pesquisa_id)
    if not pesquisa:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pesquisa não encontrada")

    # Verificação de segurança crucial
    if pesquisa.projeto.coordenador_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acesso não permitido")

    return crud.get_coletas_for_monitoring_by_pesquisa(db=db, pesquisa_id=pesquisa_id)


@router.get("/pesquisas/{pesquisa_id}/coletas/", response_model=list[schemas.Coleta])
def listar_coletas(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    current_user: models.Usuario = Depends(get_current_user)
):
    pesquisa = crud.get_pesquisa(db=db, pesquisa_id=pesquisa_id)
    if not pesquisa:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada")

    coletas_query = (
        db.query(
            models.Coleta.id,
            models.Coleta.pesquisa_id,
            models.Coleta.agente_id,
            models.Coleta.data_inicio_coleta,
            models.Coleta.data_fim_coleta,
            func.ST_Y(models.Coleta.localizacao_inicio).label("lat_inicio"),
            func.ST_X(models.Coleta.localizacao_inicio).label("lon_inicio"),
            func.ST_Y(models.Coleta.localizacao_fim).label("lat_fim"),
            func.ST_X(models.Coleta.localizacao_fim).label("lon_fim"),
        )
        .filter(models.Coleta.pesquisa_id == pesquisa_id)
        .all()
    )

    resultado = []
    for c in coletas_query:
        localizacao_inicio = (
            {"lat": c.lat_inicio, "lon": c.lon_inicio}
            if c.lat_inicio is not None and c.lon_inicio is not None
            else None
        )
        localizacao_fim = (
            {"lat": c.lat_fim, "lon": c.lon_fim}
            if c.lat_fim is not None and c.lon_fim is not None
            else None
        )

        # Busca respostas associadas a essa coleta
        respostas = db.query(models.Resposta).filter(models.Resposta.coleta_id == c.id).all()
        respostas_dict = [
            {
                "id": r.id,
                "pergunta_id": r.pergunta_id,
                "coleta_id": r.coleta_id,
                "valor_resposta": r.valor_resposta,
            }
            for r in respostas
        ]

        resultado.append({
            "id": c.id,
            "pesquisa_id": c.pesquisa_id,
            "agente_id": c.agente_id,
            "data_inicio_coleta": c.data_inicio_coleta.isoformat(),
            "data_fim_coleta": c.data_fim_coleta.isoformat() if c.data_fim_coleta else None,
            "localizacao_inicio": localizacao_inicio,
            "localizacao_fim": localizacao_fim,
            "respostas": respostas_dict
        })

    return JSONResponse(content=resultado)
