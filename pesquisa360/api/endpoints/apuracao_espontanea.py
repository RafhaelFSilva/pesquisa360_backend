from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from pesquisa360 import crud, schemas
from pesquisa360.core.dependencies import get_db, get_current_user
from pesquisa360.db import models

router = APIRouter(prefix="/pesquisas/{pesquisa_id}/apuracao-espontanea", tags=["Apuracao Espontanea"])

ALLOWED_PROFILE_NAMES = {"gerente", "superadmin", "coordenador", "supervisor"}


def require_apuracao_espontanea_access(
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user),
):
    profile_name = db.query(models.Perfil.nome).filter(models.Perfil.id == current_user.perfil_id).scalar()
    if (profile_name or "").strip().casefold() not in ALLOWED_PROFILE_NAMES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acesso restrito a perfis administrativos.")
    return current_user


@router.get("/respostas", response_model=schemas.RespostaEspontaneaResumo)
def read_respostas_espontaneas(
    pesquisa_id: int,
    busca: str | None = None,
    modo_busca: Literal["contem", "comeca_com", "termina_com", "igual"] = "contem",
    pergunta_id: int | None = None,
    status: Literal["categorizada", "pendente"] | None = None,
    categoria_id: int | None = None,
    pagina: int = Query(default=1, ge=1),
    por_pagina: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_apuracao_espontanea_access),
):
    return crud.get_respostas_espontaneas_resumo(
        db=db,
        pesquisa_id=pesquisa_id,
        current_user=current_user,
        busca=busca,
        modo_busca=modo_busca,
        pergunta_id=pergunta_id,
        status=status,
        categoria_id=categoria_id,
        pagina=pagina,
        por_pagina=por_pagina,
    )


@router.get("/categorias", response_model=list[schemas.RespostaEspontaneaCategoriaRead])
def read_categorias_espontaneas(
    pesquisa_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_apuracao_espontanea_access),
):
    return crud.get_resposta_espontanea_categorias(db=db, pesquisa_id=pesquisa_id, current_user=current_user)


@router.post("/categorias", response_model=schemas.RespostaEspontaneaCategoriaRead, status_code=status.HTTP_201_CREATED)
def create_categoria_espontanea(
    pesquisa_id: int,
    categoria_in: schemas.RespostaEspontaneaCategoriaCreate,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_apuracao_espontanea_access),
):
    return crud.create_resposta_espontanea_categoria(
        db=db,
        pesquisa_id=pesquisa_id,
        categoria_in=categoria_in,
        current_user=current_user,
    )


@router.patch("/categorias/{categoria_id}", response_model=schemas.RespostaEspontaneaCategoriaRead)
def update_categoria_espontanea(
    pesquisa_id: int,
    categoria_id: int,
    categoria_in: schemas.RespostaEspontaneaCategoriaUpdate,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_apuracao_espontanea_access),
):
    return crud.update_resposta_espontanea_categoria(
        db=db,
        pesquisa_id=pesquisa_id,
        categoria_id=categoria_id,
        categoria_in=categoria_in,
        current_user=current_user,
    )


@router.delete("/categorias/{categoria_id}", response_model=schemas.RespostaEspontaneaCategoriaRead)
def delete_categoria_espontanea(
    pesquisa_id: int,
    categoria_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_apuracao_espontanea_access),
):
    return crud.delete_resposta_espontanea_categoria(
        db=db,
        pesquisa_id=pesquisa_id,
        categoria_id=categoria_id,
        current_user=current_user,
    )


@router.post("/mapeamentos/lote", response_model=schemas.RespostaEspontaneaMapeamentoLoteResultado)
def mapear_respostas_espontaneas_em_lote(
    pesquisa_id: int,
    payload: schemas.RespostaEspontaneaMapeamentoLote,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_apuracao_espontanea_access),
):
    return crud.mapear_respostas_espontaneas_em_lote(
        db=db,
        pesquisa_id=pesquisa_id,
        payload=payload,
        current_user=current_user,
    )


@router.delete("/mapeamentos/{mapeamento_id}")
def delete_mapeamento_espontaneo(
    pesquisa_id: int,
    mapeamento_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_apuracao_espontanea_access),
):
    crud.delete_resposta_espontanea_mapeamento(
        db=db,
        pesquisa_id=pesquisa_id,
        mapeamento_id=mapeamento_id,
        current_user=current_user,
    )
    return {"detail": "Mapeamento desativado com sucesso."}
