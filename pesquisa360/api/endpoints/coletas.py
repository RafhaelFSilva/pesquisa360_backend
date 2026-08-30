from datetime import datetime
from typing import List, Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
import json

from pesquisa360 import crud, schemas
from pesquisa360.db import models
from pesquisa360.services import acessos
from pesquisa360.core.dependencies import get_db, get_current_user
from pesquisa360.core.utils import web_point
from pesquisa360.core.rbac import Permissao, require_permissao

router = APIRouter()

def parse_csv_ids(value: str | None) -> list[int] | None:
    if value is None or value.strip() == "":
        return None
    try:
        ids = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="IDs devem ser inteiros separados por virgula.") from exc
    return ids or None

def validar_agentes_monitoramento(db: Session, agente_ids: list[int] | None, current_user: models.Usuario):
    if not agente_ids:
        return

    ids_unicos = set(agente_ids)
    agentes = db.query(models.Usuario.id).filter(
        models.Usuario.id.in_(ids_unicos),
        acessos.filtro_usuario_visivel(current_user)
    ).all()
    ids_validos = {agente.id for agente in agentes}

    if ids_validos != ids_unicos:
        raise HTTPException(status_code=404, detail="Agente nao encontrado para este tenant.")

def validar_setores_monitoramento(db: Session, pesquisa_id: int, setor_ids: list[int] | None):
    if not setor_ids:
        return

    ids_unicos = set(setor_ids)
    setores = db.query(models.Setor.id).filter(
        models.Setor.id.in_(ids_unicos),
        models.Setor.pesquisa_id == pesquisa_id
    ).all()
    ids_validos = {setor.id for setor in setores}

    if ids_validos != ids_unicos:
        raise HTTPException(status_code=404, detail="Setor nao encontrado para esta pesquisa.")

@router.post("/pesquisas/{pesquisa_id}/coletas/", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permissao(Permissao.COLETA_ENVIAR))])
def submit_coleta(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    coleta_in: schemas.ColetaCreate,
    current_user: models.Usuario = Depends(get_current_user)
):
    """
    Recebe uma coleta realizada pelo App Mobile.
    """
    # 1. SEGURANÇA: Valida se a pesquisa pertence à empresa do agente
    pesquisa = crud.get_pesquisa(db=db, pesquisa_id=pesquisa_id, current_user=current_user)
    if not pesquisa:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada ou acesso negado")

    # Garante que a coleta seja salva com o ID do usuário logado (Agente)
    db_coleta = crud.create_coleta(
        db=db,
        coleta_in=coleta_in,
        pesquisa_id=pesquisa_id,
        current_user=current_user,
    )

    # A associação do agente vem do token do usuário autenticado,
    # não do payload de coleta.

    return {
        "msg": "Coleta recebida com sucesso",
        "id": db_coleta.id,
        "client_uuid": db_coleta.client_uuid,
        "setor_id": db_coleta.setor_id,
    }

@router.get("/pesquisas/{pesquisa_id}/coletas/monitoramento/", dependencies=[Depends(require_permissao(Permissao.CAMPO_MONITORAR))])
def read_coletas_monitoramento(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    agente_ids: str | None = None,
    setor_ids: str | None = None,
    current_user: models.Usuario = Depends(get_current_user)
):
    pesquisa = crud.get_pesquisa(db=db, pesquisa_id=pesquisa_id, current_user=current_user)
    if not pesquisa:
        raise HTTPException(status_code=404, detail="Pesquisa nao encontrada ou acesso negado")

    agente_id_list = parse_csv_ids(agente_ids)
    setor_id_list = parse_csv_ids(setor_ids)
    validar_agentes_monitoramento(db, agente_id_list, current_user)
    validar_setores_monitoramento(db, pesquisa_id, setor_id_list)

    coletas_query = db.query(
        models.Coleta.id,
        models.Coleta.pesquisa_id,
        models.Coleta.agente_id,
        models.Coleta.setor_id,
        models.Usuario.nome.label("agente_nome"),
        models.Coleta.data_inicio_coleta,
        models.Coleta.data_fim_coleta,
        models.Coleta.endereco_estimado,
        models.Coleta.inconformidade_localizacao,
        models.Coleta.status_sincronizacao,
        models.Coleta.foi_offline,
        func.ST_Y(models.Coleta.localizacao_inicio).label("lat_inicio"),
        func.ST_X(models.Coleta.localizacao_inicio).label("lng_inicio"),
        func.ST_Y(models.Coleta.localizacao_fim).label("lat_fim"),
        func.ST_X(models.Coleta.localizacao_fim).label("lng_fim"),
    )
    coletas_query = coletas_query.outerjoin(models.Usuario, models.Coleta.agente_id == models.Usuario.id)
    coletas_query = coletas_query.filter(models.Coleta.pesquisa_id == pesquisa_id)

    if agente_id_list:
        coletas_query = coletas_query.filter(models.Coleta.agente_id.in_(agente_id_list))

    if setor_id_list:
        setor_match = db.query(models.Setor.id).filter(
            models.Setor.id.in_(setor_id_list),
            models.Setor.pesquisa_id == pesquisa_id,
            models.Setor.geometria.isnot(None),
            models.Coleta.localizacao_inicio.isnot(None),
            func.ST_Intersects(models.Setor.geometria, models.Coleta.localizacao_inicio)
        ).exists()
        coletas_query = coletas_query.filter(setor_match)

    coletas_query = coletas_query.order_by(models.Coleta.data_inicio_coleta.desc()).all()

    resultado = []
    for coleta in coletas_query:
        localizacao_inicio = None
        if coleta.lat_inicio is not None and coleta.lng_inicio is not None:
            localizacao_inicio = web_point(coleta.lat_inicio, coleta.lng_inicio)

        localizacao_fim = None
        if coleta.lat_fim is not None and coleta.lng_fim is not None:
            localizacao_fim = web_point(coleta.lat_fim, coleta.lng_fim)

        resultado.append({
            "id": coleta.id,
            "pesquisa_id": coleta.pesquisa_id,
            "agente_id": coleta.agente_id,
            "setor_id": coleta.setor_id,
            "agente_nome": coleta.agente_nome,
            "data_inicio_coleta": coleta.data_inicio_coleta.isoformat() if coleta.data_inicio_coleta else None,
            "data_fim_coleta": coleta.data_fim_coleta.isoformat() if coleta.data_fim_coleta else None,
            "endereco_estimado": coleta.endereco_estimado,
            "localizacao_inicio": localizacao_inicio,
            "localizacao_fim": localizacao_fim,
            "inconformidade_localizacao": coleta.inconformidade_localizacao,
            "status_sincronizacao": coleta.status_sincronizacao,
            "foi_offline": coleta.foi_offline,
        })

    return resultado

@router.get("/pesquisas/{pesquisa_id}/coletas/monitoramento/filtros/", dependencies=[Depends(require_permissao(Permissao.CAMPO_MONITORAR))])
def read_coletas_monitoramento_filtros(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    current_user: models.Usuario = Depends(get_current_user)
):
    pesquisa = crud.get_pesquisa(db=db, pesquisa_id=pesquisa_id, current_user=current_user)
    if not pesquisa:
        raise HTTPException(status_code=404, detail="Pesquisa nao encontrada ou acesso negado")

    agentes_rows = db.query(
        models.Usuario.id,
        models.Usuario.nome,
        func.count(models.Coleta.id).label("total_coletas")
    )\
    .join(models.Coleta, models.Coleta.agente_id == models.Usuario.id)\
    .filter(
        models.Coleta.pesquisa_id == pesquisa_id,
        acessos.filtro_usuario_visivel(current_user)
    )\
    .group_by(models.Usuario.id, models.Usuario.nome)\
    .order_by(models.Usuario.nome)\
    .all()

    setores_rows = db.query(
        models.Setor.id,
        models.Setor.nome,
        func.count(models.Coleta.id).label("total_coletas")
    )\
    .select_from(models.Setor)\
    .join(
        models.Coleta,
        and_(
            models.Coleta.pesquisa_id == models.Setor.pesquisa_id,
            models.Coleta.localizacao_inicio.isnot(None),
            models.Setor.geometria.isnot(None),
            func.ST_Intersects(models.Setor.geometria, models.Coleta.localizacao_inicio)
        )
    )\
    .filter(models.Setor.pesquisa_id == pesquisa_id)\
    .group_by(models.Setor.id, models.Setor.nome)\
    .order_by(models.Setor.nome)\
    .all()

    return {
        "agentes": [
            {
                "id": row.id,
                "nome": row.nome,
                "total_coletas": int(row.total_coletas or 0),
            }
            for row in agentes_rows
        ],
        "setores": [
            {
                "id": row.id,
                "nome": row.nome,
                "total_coletas": int(row.total_coletas or 0),
            }
            for row in setores_rows
        ],
    }

@router.get("/pesquisas/{pesquisa_id}/coletas/", dependencies=[Depends(require_permissao(Permissao.CAMPO_MONITORAR))])
def read_coletas_por_pesquisa(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    data_inicio_coleta: datetime | None = None,
    data_fim_coleta: datetime | None = None,
    current_user: models.Usuario = Depends(get_current_user)
):
    """
    Retorna todas as coletas de uma pesquisa (Para o Dashboard Web).
    AGORA COM FILTRO DE EMPRESA.
    """
    # 1. SEGURANÇA: Valida acesso à pesquisa
    pesquisa = crud.get_pesquisa(db=db, pesquisa_id=pesquisa_id, current_user=current_user)
    if not pesquisa:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada ou acesso negado")

    # 2. Busca Coletas (Manual para ter performance com GIS)
    # Como já validamos a pesquisa acima, podemos buscar as coletas pelo ID da pesquisa com segurança.
    coletas_query = db.query(
        models.Coleta.id,
        models.Coleta.pesquisa_id,
        models.Coleta.agente_id,
        models.Coleta.setor_id,
        models.Coleta.data_inicio_coleta,
        models.Coleta.data_fim_coleta,
        models.Coleta.foi_offline,
        func.ST_Y(models.Coleta.localizacao_inicio).label("latitude_inicio"),
        func.ST_X(models.Coleta.localizacao_inicio).label("longitude_inicio"),
        func.ST_Y(models.Coleta.localizacao_fim).label("latitude_fim"),
        func.ST_X(models.Coleta.localizacao_fim).label("longitude_fim"),
    )
    coletas_query = coletas_query.filter(models.Coleta.pesquisa_id == pesquisa_id)

    if data_inicio_coleta is not None:
        coletas_query = coletas_query.filter(
            models.Coleta.data_inicio_coleta >= data_inicio_coleta
        )
    if data_fim_coleta is not None:
        coletas_query = coletas_query.filter(
            models.Coleta.data_fim_coleta <= data_fim_coleta
        )

    coletas_query = coletas_query.order_by(models.Coleta.data_inicio_coleta).all()

    resultado = []
    for c in coletas_query:
        localizacao_inicio = None
        if c.latitude_inicio is not None and c.longitude_inicio is not None:
            localizacao_inicio = web_point(c.latitude_inicio, c.longitude_inicio)
            
        localizacao_fim = None
        if c.latitude_fim is not None and c.longitude_fim is not None:
            localizacao_fim = web_point(c.latitude_fim, c.longitude_fim)

        # Busca respostas (Isso pode ser otimizado com joinedload no futuro)
        respostas = db.query(models.Resposta).filter(models.Resposta.coleta_id == c.id).all()
        
        respostas_list = [
            {
                "id": r.id,
                "pergunta_id": r.pergunta_id,
                "valor_resposta": r.valor_resposta,
            }
            for r in respostas
        ]

        resultado.append({
            "id": c.id,
            "pesquisa_id": c.pesquisa_id,
            "agente_id": c.agente_id,
            "setor_id": c.setor_id,
            "data_inicio_coleta": c.data_inicio_coleta,
            "data_fim_coleta": c.data_fim_coleta,
            "foi_offline": c.foi_offline,
            "localizacao_inicio": localizacao_inicio,
            "localizacao_fim": localizacao_fim,
            "respostas": respostas_list
        })

    return resultado
