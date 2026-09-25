"""Rotas dos Cenarios de Base Eleitoral Operacional (ADR-075).

Mesmo dominio e mesma politica de permissao da Gestao de Liderancas:
LIDERANCA_VER le, LIDERANCA_GERENCIAR escreve. Nenhuma rota aceita
company_id; cenario de outro tenant responde 404.

Este router e incluido por `liderancas.py` antes do CRUD de liderancas, para
que o segmento literal `cenarios` nunca seja lido como `{lideranca_id}`.
`/cenarios/ativo` precisa vir ANTES de `/cenarios/{cenario_id}`.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from pesquisa360 import schemas
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.core.rbac import Permissao, require_permissao
from pesquisa360.db import models
from pesquisa360.services import lideranca_cenario as cenarios

router = APIRouter()


@router.get(
    "/projetos/{projeto_id}/liderancas/cenarios",
    response_model=List[schemas.LiderancaCenarioResumo],
    dependencies=[Depends(require_permissao(Permissao.LIDERANCA_VER))],
)
def listar_cenarios(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    pesquisa_id: Optional[int] = None,
    current_user: models.Usuario = Depends(get_current_user),
):
    """Cenarios do projeto; `pesquisa_id` restringe a uma onda."""
    return [
        cenarios.resumo(cenario)
        for cenario in cenarios.listar_cenarios(
            db, projeto_id, current_user, pesquisa_id=pesquisa_id
        )
    ]


@router.get(
    "/projetos/{projeto_id}/liderancas/cenarios/ativo",
    response_model=schemas.LiderancaCenarioAtivoResponse,
    dependencies=[Depends(require_permissao(Permissao.LIDERANCA_VER))],
)
def obter_cenario_ativo(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    pesquisa_id: int,
    current_user: models.Usuario = Depends(get_current_user),
):
    """Base de calculo vigente da onda: PADRAO (sem cenario) ou o cenario ATIVO."""
    cenarios.obter_pesquisa_do_projeto(db, projeto_id, pesquisa_id, current_user)
    base_calculo = cenarios.resolver_base_calculo(db, pesquisa_id)
    return {
        "base_calculo": base_calculo.contrato(),
        "cenario": cenarios.resumo(base_calculo.cenario) if base_calculo.cenario else None,
    }


@router.post(
    "/projetos/{projeto_id}/liderancas/cenarios",
    response_model=schemas.LiderancaCenarioDetalhe,
    status_code=201,
    dependencies=[Depends(require_permissao(Permissao.LIDERANCA_GERENCIAR))],
)
def criar_cenario(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    payload: schemas.LiderancaCenarioCreate,
    current_user: models.Usuario = Depends(get_current_user),
):
    cenario = cenarios.criar_cenario(
        db,
        projeto_id,
        current_user,
        pesquisa_id=payload.pesquisa_id,
        nome=payload.nome,
        metodologia=payload.metodologia,
        data_referencia=payload.data_referencia,
    )
    return cenarios.detalhe(db, projeto_id, cenario, current_user)


@router.get(
    "/projetos/{projeto_id}/liderancas/cenarios/{cenario_id}",
    response_model=schemas.LiderancaCenarioDetalhe,
    dependencies=[Depends(require_permissao(Permissao.LIDERANCA_VER))],
)
def obter_cenario(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    cenario_id: int,
    current_user: models.Usuario = Depends(get_current_user),
):
    cenario = cenarios.obter_cenario(db, projeto_id, cenario_id, current_user)
    return cenarios.detalhe(db, projeto_id, cenario, current_user)


@router.patch(
    "/projetos/{projeto_id}/liderancas/cenarios/{cenario_id}",
    response_model=schemas.LiderancaCenarioDetalhe,
    dependencies=[Depends(require_permissao(Permissao.LIDERANCA_GERENCIAR))],
)
def atualizar_cenario(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    cenario_id: int,
    payload: schemas.LiderancaCenarioUpdate,
    current_user: models.Usuario = Depends(get_current_user),
):
    """Metadados de um RASCUNHO. Campo ausente mantem; ATIVO/ARQUIVADO -> 409."""
    cenario = cenarios.atualizar_cenario(
        db, projeto_id, cenario_id, current_user, campos=payload.model_dump(exclude_unset=True)
    )
    return cenarios.detalhe(db, projeto_id, cenario, current_user)


@router.put(
    "/projetos/{projeto_id}/liderancas/cenarios/{cenario_id}/setores",
    response_model=schemas.LiderancaCenarioDetalhe,
    dependencies=[Depends(require_permissao(Permissao.LIDERANCA_GERENCIAR))],
)
def salvar_setores_cenario(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    cenario_id: int,
    payload: schemas.LiderancaCenarioSetoresRequest,
    current_user: models.Usuario = Depends(get_current_user),
):
    """Lote transacional: substitui todos os valores por setor do RASCUNHO."""
    cenario = cenarios.salvar_setores(
        db,
        projeto_id,
        cenario_id,
        current_user,
        setores=[item.model_dump() for item in payload.setores],
    )
    return cenarios.detalhe(db, projeto_id, cenario, current_user)


@router.post(
    "/projetos/{projeto_id}/liderancas/cenarios/{cenario_id}/duplicar",
    response_model=schemas.LiderancaCenarioDetalhe,
    status_code=201,
    dependencies=[Depends(require_permissao(Permissao.LIDERANCA_GERENCIAR))],
)
def duplicar_cenario(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    cenario_id: int,
    current_user: models.Usuario = Depends(get_current_user),
):
    copia = cenarios.duplicar_cenario(db, projeto_id, cenario_id, current_user)
    return cenarios.detalhe(db, projeto_id, copia, current_user)


@router.post(
    "/projetos/{projeto_id}/liderancas/cenarios/{cenario_id}/ativar",
    response_model=schemas.LiderancaCenarioDetalhe,
    dependencies=[Depends(require_permissao(Permissao.LIDERANCA_GERENCIAR))],
)
def ativar_cenario(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    cenario_id: int,
    current_user: models.Usuario = Depends(get_current_user),
):
    """Valida e ativa; o ATIVO anterior da onda e arquivado na mesma transacao."""
    cenario = cenarios.ativar_cenario(db, projeto_id, cenario_id, current_user)
    return cenarios.detalhe(db, projeto_id, cenario, current_user)


@router.post(
    "/projetos/{projeto_id}/liderancas/cenarios/{cenario_id}/arquivar",
    response_model=schemas.LiderancaCenarioDetalhe,
    dependencies=[Depends(require_permissao(Permissao.LIDERANCA_GERENCIAR))],
)
def arquivar_cenario(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    cenario_id: int,
    current_user: models.Usuario = Depends(get_current_user),
):
    """Arquivar o ATIVO devolve a Gestao de Liderancas ao modo PADRAO."""
    cenario = cenarios.arquivar_cenario(db, projeto_id, cenario_id, current_user)
    return cenarios.detalhe(db, projeto_id, cenario, current_user)
