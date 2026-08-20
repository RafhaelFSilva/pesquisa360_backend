"""Rotas de leitura e validacao da Base Eleitoral (Fase 3A).

Nao existe upload da fonte real aqui: sem o arquivo eleitoral em maos, expor um
endpoint de importacao generico seria criar uma API de JSON livre para contornar
a ausencia do adapter. O motor de importacao permanece interno ate a Fase 3B.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from pesquisa360 import schemas
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.db import models
from pesquisa360.services import base_eleitoral as service

router = APIRouter()


def _eh_oficial(base: models.BaseEleitoral) -> bool:
    return base.company_id is None


def _base_para_item(base: models.BaseEleitoral) -> dict:
    return {
        "id": base.id,
        "nome": base.nome,
        "ano": base.ano,
        "uf": base.uf,
        "fonte": base.fonte,
        "versao": base.versao,
        "data_referencia": base.data_referencia,
        "status": base.status,
        "eh_oficial": _eh_oficial(base),
    }


def _territorio_para_item(territorio: models.TerritorioEleitoral) -> dict:
    # Geometria PostGIS nunca sai crua (ADR-004): a listagem e alfanumerica e
    # apenas sinaliza a presenca de geometria.
    return {
        "id": territorio.id,
        "base_eleitoral_id": territorio.base_eleitoral_id,
        "tipo": territorio.tipo,
        "codigo": territorio.codigo,
        "nome": territorio.nome,
        "nome_normalizado": territorio.nome_normalizado,
        "parent_id": territorio.parent_id,
        "municipio_id": territorio.municipio_id,
        "zona_eleitoral": territorio.zona_eleitoral,
        "numero_secao": territorio.numero_secao,
        "eleitorado_apto": territorio.eleitorado_apto,
        "eleitorado_apto_origem": territorio.eleitorado_apto_origem,
        "eleitorado_apto_divergente": territorio.eleitorado_apto_divergente,
        "status_validacao": territorio.status_validacao,
        "possui_geometria": territorio.geometria is not None,
    }


def _contar_territorios(db: Session, base_id: int) -> int:
    return (
        db.query(models.TerritorioEleitoral)
        .filter(models.TerritorioEleitoral.base_eleitoral_id == base_id)
        .count()
    )


# --- Leitura ------------------------------------------------------------------


@router.get("/base-eleitoral/", response_model=List[schemas.BaseEleitoralListItem])
def listar_bases_eleitorais(
    *,
    db: Session = Depends(get_db),
    status_validacao: Optional[schemas.StatusBaseEleitoral] = None,
    uf: Optional[str] = None,
    ano: Optional[int] = None,
    current_user: models.Usuario = Depends(get_current_user),
):
    """Bases oficiais mais as privadas do tenant do usuario autenticado."""
    bases = service.listar_bases_eleitorais_visiveis(
        db,
        current_user,
        status_filtro=status_validacao.value if status_validacao else None,
        uf=uf,
        ano=ano,
    )
    return [_base_para_item(base) for base in bases]


@router.get("/base-eleitoral/{base_id}", response_model=schemas.BaseEleitoralDetalhe)
def obter_base_eleitoral(
    *,
    db: Session = Depends(get_db),
    base_id: int,
    current_user: models.Usuario = Depends(get_current_user),
):
    base = service.obter_base_eleitoral_visivel(db, base_id, current_user)
    detalhe = _base_para_item(base)
    detalhe.update(
        {
            "fonte_referencia": base.fonte_referencia,
            "substituida_por_id": base.substituida_por_id,
            "comparecimento_estimado": (
                float(base.comparecimento_estimado)
                if base.comparecimento_estimado is not None
                else None
            ),
            "percentual_votos_validos": (
                float(base.percentual_votos_validos)
                if base.percentual_votos_validos is not None
                else None
            ),
            "criado_por_id": base.criado_por_id,
            "criado_em": base.criado_em,
            "atualizado_em": base.atualizado_em,
            "total_territorios": _contar_territorios(db, base.id),
            "total_em_conferencia": service.contar_territorios_em_conferencia(db, base.id),
        }
    )
    return detalhe


@router.get(
    "/base-eleitoral/{base_id}/territorios",
    response_model=List[schemas.TerritorioEleitoralListItem],
)
def listar_territorios_base_eleitoral(
    *,
    db: Session = Depends(get_db),
    base_id: int,
    tipo: Optional[schemas.TipoTerritorioEleitoral] = None,
    status_validacao: Optional[schemas.StatusBaseEleitoral] = None,
    municipio_id: Optional[int] = None,
    q: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: models.Usuario = Depends(get_current_user),
):
    territorios = service.listar_territorios(
        db,
        base_id,
        current_user,
        tipo=tipo.value if tipo else None,
        status_validacao=status_validacao.value if status_validacao else None,
        municipio_id=municipio_id,
        q=q,
        limit=limit,
        offset=offset,
    )
    return [_territorio_para_item(territorio) for territorio in territorios]


@router.get(
    "/base-eleitoral/{base_id}/divergencias",
    response_model=List[schemas.DivergenciaBaseEleitoralResponse],
)
def listar_divergencias_base_eleitoral(
    *,
    db: Session = Depends(get_db),
    base_id: int,
    current_user: models.Usuario = Depends(get_current_user),
):
    return service.listar_divergencias(db, base_id, current_user)


# --- Acoes --------------------------------------------------------------------


@router.post(
    "/projetos/{projeto_id}/base-eleitoral/{base_id}/vincular",
    response_model=schemas.VinculoProjetoBaseResponse,
)
def vincular_base_ao_projeto(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    base_id: int,
    payload: Optional[schemas.VincularBaseProjetoRequest] = None,
    current_user: models.Usuario = Depends(get_current_user),
):
    """Fixa a versao eleitoral usada pela campanha. O tenant vem do JWT."""
    principal = payload.principal if payload is not None else True
    return service.vincular_base_eleitoral_ao_projeto(
        db, projeto_id, base_id, current_user, principal=principal
    )


@router.post("/base-eleitoral/{base_id}/validar", response_model=schemas.ResultadoValidacaoBase)
def validar_base_eleitoral(
    *,
    db: Session = Depends(get_db),
    base_id: int,
    current_user: models.Usuario = Depends(get_current_user),
):
    """Validacao humana explicita: nao existe promocao automatica."""
    base = service.validar_base_eleitoral(db, base_id, current_user)
    return {
        "id": base.id,
        "status": base.status,
        "total_territorios": _contar_territorios(db, base.id),
        "total_em_conferencia": service.contar_territorios_em_conferencia(db, base.id),
    }


@router.post(
    "/base-eleitoral/territorios/{territorio_id}/resolver-divergencia",
    response_model=schemas.TerritorioEleitoralListItem,
)
def resolver_divergencia(
    *,
    db: Session = Depends(get_db),
    territorio_id: int,
    payload: schemas.ResolverDivergenciaRequest,
    current_user: models.Usuario = Depends(get_current_user),
):
    territorio = service.resolver_divergencia_territorio(
        db,
        territorio_id,
        payload.valor_final,
        payload.justificativa,
        current_user,
    )
    return _territorio_para_item(territorio)
