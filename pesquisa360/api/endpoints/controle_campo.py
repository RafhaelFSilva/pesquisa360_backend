"""Painel Web consolidado de Controle de Campo (PROMPT 07).

Escopo gerencial: pesquisa inteira dentro do tenant (404 fora dele). Mesma
autorizacao das rotas de monitoramento/relatorios (usuario autenticado do
tenant). O endpoint do agente (`/agente/.../cobertura-campo/`) permanece
recortado aos setores dele e nao e alterado.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from pesquisa360 import schemas
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.db import models
from pesquisa360.services import controle_campo
from pesquisa360.core.rbac import Permissao, require_permissao

router = APIRouter()


def _csv_ids(value: Optional[str]) -> list[int]:
    if not value:
        return []
    try:
        return [int(item) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="IDs devem ser inteiros separados por virgula.") from exc


def _filtros(
    municipio_id: Optional[int],
    setor_ids: Optional[str],
    agente_ids: Optional[str],
    data_inicio: Optional[datetime],
    data_fim: Optional[datetime],
    resultado: Optional[str],
) -> controle_campo.FiltrosControleCampo:
    if data_inicio and data_fim and data_fim < data_inicio:
        raise HTTPException(status_code=422, detail="data_fim anterior a data_inicio.")
    if resultado and resultado not in {r.value for r in schemas.TentativaResultado}:
        raise HTTPException(status_code=422, detail="resultado invalido.")
    return controle_campo.FiltrosControleCampo(
        municipio_id=municipio_id,
        setor_ids=_csv_ids(setor_ids),
        agente_ids=_csv_ids(agente_ids),
        data_inicio=data_inicio,
        data_fim=data_fim,
        resultado=resultado,
    )


@router.get(
    "/projetos/{projeto_id}/pesquisas/{pesquisa_id}/controle-campo",
    response_model=schemas.ControleCampoRead, dependencies=[Depends(require_permissao(Permissao.CAMPO_MONITORAR))])
def obter_controle_campo(
    projeto_id: int,
    pesquisa_id: int,
    municipio_id: Optional[int] = Query(default=None, gt=0),
    setor_ids: Optional[str] = None,
    agente_ids: Optional[str] = None,
    data_inicio: Optional[datetime] = None,
    data_fim: Optional[datetime] = None,
    resultado: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user),
):
    filtros = _filtros(municipio_id, setor_ids, agente_ids, data_inicio, data_fim, resultado)
    return controle_campo.montar_painel(db, projeto_id, pesquisa_id, current_user, filtros)


@router.get(
    "/projetos/{projeto_id}/pesquisas/{pesquisa_id}/cobertura-campo",
    response_model=schemas.CoberturaCampoGerencialRead, dependencies=[Depends(require_permissao(Permissao.CAMPO_MONITORAR))])
def obter_cobertura_campo_gerencial(
    projeto_id: int,
    pesquisa_id: int,
    municipio_id: Optional[int] = Query(default=None, gt=0),
    setor_ids: Optional[str] = None,
    agente_ids: Optional[str] = None,
    data_inicio: Optional[datetime] = None,
    data_fim: Optional[datetime] = None,
    resultado: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user),
):
    """Somente a camada espacial (mesmos filtros), para o mapa."""
    filtros = _filtros(municipio_id, setor_ids, agente_ids, data_inicio, data_fim, resultado)
    painel = controle_campo.montar_painel(db, projeto_id, pesquisa_id, current_user, filtros)
    return {
        "pesquisa_id": pesquisa_id,
        "snapshot_em": painel["snapshot_em"],
        "setores": painel["setores"],
        **painel["atividade_campo"],
    }
