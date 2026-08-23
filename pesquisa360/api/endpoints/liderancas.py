"""Rotas da Gestao de Liderancas.

Todas ao nivel de PROJETO: a lideranca politica e da campanha, nao da onda.
Nenhuma rota aceita company_id; recurso de outro tenant responde 404.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from pesquisa360 import schemas
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.core.utils import wkb_to_geojson_point
from pesquisa360.db import models
from pesquisa360.services import lideranca as service
from pesquisa360.services import lideranca_analytics as analytics

router = APIRouter()


def _territorio_item(territorio: models.TerritorioEleitoral) -> dict:
    return {
        "id": territorio.id,
        "nome": territorio.nome,
        "eleitorado_apto": territorio.eleitorado_apto,
    }


def _lideranca_response(db: Session, lideranca: models.LiderancaPolitica) -> dict:
    return {
        "id": lideranca.id,
        "nome": lideranca.nome,
        "ativo": lideranca.ativo,
        "posicionamento": lideranca.posicionamento,
        "criado_em": lideranca.criado_em,
        "atualizado_em": lideranca.atualizado_em,
        # Nunca a geometria PostGIS crua (ADR-004).
        "localizacao": wkb_to_geojson_point(lideranca.localizacao),
        "territorios": [
            _territorio_item(territorio)
            for territorio in service.listar_territorios(db, lideranca.id)
        ],
        "configs": [
            {
                "pesquisa_id": config.pesquisa_id,
                "setor_id": config.setor_id,
                "cota_votos_validos": config.cota_votos_validos,
            }
            for config in service.listar_configs(db, lideranca.id)
        ],
    }


# --- CRUD --------------------------------------------------------------------


@router.get(
    "/projetos/{projeto_id}/liderancas",
    response_model=List[schemas.LiderancaPoliticaResponse],
)
def listar_liderancas(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    incluir_inativas: bool = False,
    # Ausente devolve todas: chamadas existentes nao mudam de comportamento.
    posicionamento: Optional[schemas.PosicionamentoLideranca] = None,
    current_user: models.Usuario = Depends(get_current_user),
):
    liderancas = service.listar_liderancas(
        db,
        projeto_id,
        current_user,
        incluir_inativas=incluir_inativas,
        posicionamento=posicionamento.value if posicionamento else None,
    )
    return [_lideranca_response(db, lideranca) for lideranca in liderancas]


@router.post(
    "/projetos/{projeto_id}/liderancas",
    response_model=schemas.LiderancaPoliticaResponse,
    status_code=201,
)
def criar_lideranca(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    payload: schemas.LiderancaPoliticaCreate,
    current_user: models.Usuario = Depends(get_current_user),
):
    lideranca = service.criar_lideranca(
        db,
        projeto_id,
        payload.nome,
        current_user,
        posicionamento=payload.posicionamento.value,
    )
    return _lideranca_response(db, lideranca)


@router.get(
    "/projetos/{projeto_id}/liderancas/{lideranca_id}",
    response_model=schemas.LiderancaPoliticaResponse,
)
def obter_lideranca(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    lideranca_id: int,
    current_user: models.Usuario = Depends(get_current_user),
):
    lideranca = service.obter_lideranca(db, projeto_id, lideranca_id, current_user)
    return _lideranca_response(db, lideranca)


@router.patch(
    "/projetos/{projeto_id}/liderancas/{lideranca_id}",
    response_model=schemas.LiderancaPoliticaResponse,
)
def atualizar_lideranca(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    lideranca_id: int,
    payload: schemas.LiderancaPoliticaUpdate,
    current_user: models.Usuario = Depends(get_current_user),
):
    # Campo ausente mantem a posicao; null explicito remove o ponto do mapa.
    informados = payload.model_dump(exclude_unset=True)
    extras = (
        {"localizacao": informados["localizacao"]} if "localizacao" in informados else {}
    )
    lideranca = service.atualizar_lideranca(
        db,
        projeto_id,
        lideranca_id,
        current_user,
        nome=payload.nome,
        ativo=payload.ativo,
        posicionamento=payload.posicionamento.value if payload.posicionamento else None,
        **extras,
    )
    return _lideranca_response(db, lideranca)


@router.delete(
    "/projetos/{projeto_id}/liderancas/{lideranca_id}",
    response_model=schemas.LiderancaPoliticaResponse,
)
def desativar_lideranca(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    lideranca_id: int,
    current_user: models.Usuario = Depends(get_current_user),
):
    """Soft delete: `ativo=false`, preservando configuracoes e territorios."""
    lideranca = service.desativar_lideranca(db, projeto_id, lideranca_id, current_user)
    return _lideranca_response(db, lideranca)


# --- Configuracao por onda ---------------------------------------------------


@router.put(
    "/projetos/{projeto_id}/liderancas/{lideranca_id}/pesquisas/{pesquisa_id}/config",
    response_model=schemas.LiderancaPesquisaConfigResponse,
)
def definir_config(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    lideranca_id: int,
    pesquisa_id: int,
    payload: schemas.LiderancaConfigRequest,
    current_user: models.Usuario = Depends(get_current_user),
):
    """Setor e cota da lideranca naquela onda; ondas anteriores nao mudam."""
    config = service.definir_config_pesquisa(
        db,
        projeto_id,
        lideranca_id,
        pesquisa_id,
        current_user,
        setor_id=payload.setor_id,
        cota_votos_validos=payload.cota_votos_validos,
    )
    return {
        "pesquisa_id": config.pesquisa_id,
        "setor_id": config.setor_id,
        "cota_votos_validos": config.cota_votos_validos,
    }


# --- Territorios -------------------------------------------------------------


@router.put(
    "/projetos/{projeto_id}/liderancas/{lideranca_id}/territorios",
    response_model=List[schemas.LiderancaTerritorioItem],
)
def definir_territorios(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    lideranca_id: int,
    payload: schemas.LiderancaTerritoriosRequest,
    current_user: models.Usuario = Depends(get_current_user),
):
    """Substituicao transacional: um id invalido derruba o lote inteiro."""
    territorios = service.definir_territorios(
        db, projeto_id, lideranca_id, payload.territorio_ids, current_user
    )
    return [_territorio_item(territorio) for territorio in territorios]


# --- Analise em lote ---------------------------------------------------------


@router.post(
    "/projetos/{projeto_id}/liderancas/analise",
    response_model=schemas.LiderancaAnaliseResponse,
)
def analisar_liderancas(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    payload: schemas.LiderancaAnaliseRequest,
    current_user: models.Usuario = Depends(get_current_user),
):
    """Projecao e Gap/Plus de todas as liderancas em uma unica chamada."""
    return analytics.analisar_liderancas(
        db,
        projeto_id=projeto_id,
        pesquisa_id=payload.pesquisa_id,
        pergunta_alvo_id=payload.alvo.pergunta_id,
        alvo_valores=payload.alvo.valores,
        filtros_respostas=[
            {"pergunta_id": item.pergunta_id, "valores": item.valores}
            for item in payload.filtros_respostas
        ],
        lideranca_ids=payload.lideranca_ids,
        current_user=current_user,
    )
