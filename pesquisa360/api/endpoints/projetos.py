# pesquisa360/api/endpoints/projetos.py

import json
from typing import List, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func

from pesquisa360 import crud, schemas
from pesquisa360.db import models
from pesquisa360.core.dependencies import get_db, get_current_user

router = APIRouter()

def pesquisa_to_dict(p: models.Pesquisa, db: Session) -> dict:
    """
    Converte um objeto models.Pesquisa para um dicionário JSON-serializável.
    """
    # 1. Trata a Cerca Eletrônica (GeoJSON)
    cerca_geojson = None
    if hasattr(p, "cerca_eletronica") and p.cerca_eletronica is not None:
        cerca_str = db.query(func.ST_AsGeoJSON(p.cerca_eletronica)).scalar()
        if cerca_str:
            try:
                cerca_geojson = json.loads(cerca_str)
            except Exception:
                cerca_geojson = cerca_str

    # 2. Trata as Perguntas e suas Opções
    perguntas_list = []
    # Garante que p.perguntas seja iterável
    lista_perguntas = getattr(p, "perguntas", []) or []
    
    for pergunta in lista_perguntas:
        # Pega as opções da pergunta (se houver) e converte para dicionário
        objs_opcoes = getattr(pergunta, "opcoes", []) or []
        opcoes_dict_list = []
        
        for op in objs_opcoes:
            opcoes_dict_list.append({
                "id": op.id,
                "texto": op.texto,
                "ordem": op.ordem,
                "proxima_pergunta_id": op.proxima_pergunta_id
            })

        perguntas_list.append({
            "id": getattr(pergunta, "id", None),
            "texto_pergunta": getattr(pergunta, "texto_pergunta", None),
            "tipo_pergunta": getattr(pergunta, "tipo_pergunta", None),
            "ordem": getattr(pergunta, "ordem", None),
            "eh_obrigatoria": getattr(pergunta, "eh_obrigatoria", True),
            "ativo": getattr(pergunta, "ativo", True),
            "pesquisa_id": getattr(pergunta, "pesquisa_id", None),
            # Agora enviamos a lista processada, não os objetos brutos
            "opcoes": opcoes_dict_list 
        })

    # 3. Monta o objeto final da pesquisa
    pesquisa_dict = {
        "id": getattr(p, "id", None),
        "titulo": getattr(p, "titulo", None),
        "tipo_pesquisa": getattr(p, "tipo_pesquisa", None),
        "ativo": getattr(p, "ativo", None),
        "cerca_eletronica": cerca_geojson,
        "tolerancia_metros": getattr(p, "tolerancia_metros", None),
        "perguntas": perguntas_list,
        "coletas": []
    }
    return pesquisa_dict

def projeto_para_dict(projeto: models.Projeto, db: Session) -> dict:
    """
    Converte um objeto models.Projeto para um dicionário JSON-serializável.
    Inclui conversão das pesquisas associadas (com conversão das perguntas e geofence).
    """
    pesquisas_list = []
    for p in getattr(projeto, "pesquisas", []) or []:
        pesquisas_list.append(pesquisa_to_dict(p, db))

    coordenador_info = None
    if hasattr(projeto, "coordenador") and projeto.coordenador is not None:
        coordenador = projeto.coordenador
        coordenador_info = {
            "id": getattr(coordenador, "id", None),
            "email": getattr(coordenador, "email", None),
            "nome": getattr(coordenador, "nome", None),
        }

    projeto_dict = {
        "id": getattr(projeto, "id", None),
        "nome": getattr(projeto, "nome", None),
        "descricao": getattr(projeto, "descricao", None),
        "status": getattr(projeto, "status", None),
        "coordenador_id": getattr(projeto, "coordenador_id", None),
        "coordenador": coordenador_info,
        "pesquisas": pesquisas_list
    }
    return projeto_dict


# -------------------------
# Endpoints (mantidos todos, com ajustes apenas onde necessário)
# -------------------------

@router.post("/", response_model=schemas.Projeto, status_code=status.HTTP_201_CREATED)
def create_projeto(
    *,
    db: Session = Depends(get_db),
    projeto_in: schemas.ProjetoCreate,
    current_user: models.Usuario = Depends(get_current_user)
):
    """Cria um novo projeto."""
    projeto = crud.create_projeto(db=db, projeto=projeto_in, coordenador_id=current_user.id)
    # Retornar dicionário serializável para evitar WKBElement em resposta
    return JSONResponse(content=projeto_para_dict(projeto, db))


@router.get("/", response_model=List[schemas.Projeto])
def read_projetos_do_usuario(
    *,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Retorna a lista de projetos do usuário logado."""
    projetos = crud.get_projetos_by_coordenador(db, coordenador_id=current_user.id)
    projetos_list = [projeto_para_dict(p, db) for p in projetos]
    return JSONResponse(content=projetos_list)


@router.get("/{projeto_id}", response_model=schemas.Projeto)
def read_projeto(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    current_user: models.Usuario = Depends(get_current_user)
) -> Any:
    """Obtém um projeto específico pelo ID."""
    projeto = crud.get_projeto(db=db, projeto_id=projeto_id)
    if not projeto:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")
    if projeto.coordenador_id != current_user.id:
        raise HTTPException(status_code=403, detail="Não tem permissão para ver este projeto")
    # converter antes de retornar para evitar erros de serialização
    return JSONResponse(content=projeto_para_dict(projeto, db))


@router.patch("/{projeto_id}", response_model=schemas.Projeto)
def update_projeto(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    projeto_in: schemas.ProjetoUpdate,
    current_user: models.Usuario = Depends(get_current_user)
):
    """Atualiza um projeto."""
    db_projeto = crud.get_projeto(db, projeto_id=projeto_id)
    if not db_projeto:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")
    if db_projeto.coordenador_id != current_user.id:
        raise HTTPException(status_code=403, detail="Não tem permissão para editar este projeto")

    projeto = crud.update_projeto(db=db, db_obj=db_projeto, obj_in=projeto_in)
    return JSONResponse(content=projeto_para_dict(projeto, db))


@router.post("/{projeto_id}/pesquisas/", response_model=schemas.Pesquisa, status_code=status.HTTP_201_CREATED)
def create_pesquisa_for_projeto(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    pesquisa_in: schemas.PesquisaCreate,
    current_user: models.Usuario = Depends(get_current_user)
):
    """Cria uma nova pesquisa dentro de um projeto."""
    projeto = crud.get_projeto(db=db, projeto_id=projeto_id)
    if not projeto:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")
    if projeto.coordenador_id != current_user.id:
        raise HTTPException(status_code=403, detail="Não tem permissão para adicionar pesquisas a este projeto")

    pesquisa = crud.create_pesquisa_for_projeto(db=db, pesquisa=pesquisa_in, projeto_id=projeto_id)
    # retornar dict serializável
    return JSONResponse(content=pesquisa_to_dict(pesquisa, db))


@router.post("/{projeto_id}/pesquisas/{pesquisa_id}/perguntas/", response_model=schemas.Pergunta, status_code=status.HTTP_201_CREATED)
def create_pergunta_for_pesquisa(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    pesquisa_id: int,
    pergunta_in: schemas.PerguntaCreate,
    current_user: models.Usuario = Depends(get_current_user)
):
    """Cria uma nova pergunta dentro de uma pesquisa."""
    projeto = crud.get_projeto(db=db, projeto_id=projeto_id)
    if not projeto:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")
    if projeto.coordenador_id != current_user.id:
        raise HTTPException(status_code=403, detail="Não tem permissão para criar perguntas neste projeto")

    pergunta = crud.create_pergunta_for_pesquisa(db=db, pergunta=pergunta_in, pesquisa_id=pesquisa_id)
    return pergunta


@router.get("/{projeto_id}/pesquisas/{pesquisa_id}/perguntas/", response_model=List[schemas.Pergunta])
def read_perguntas_da_pesquisa(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    pesquisa_id: int,
    current_user: models.Usuario = Depends(get_current_user)
):
    """Retorna a lista de perguntas de uma pesquisa."""
    projeto = crud.get_projeto(db=db, projeto_id=projeto_id)
    if not projeto:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")
    if projeto.coordenador_id != current_user.id:
        raise HTTPException(status_code=403, detail="Não tem permissão para ver as perguntas deste projeto")

    perguntas = crud.get_perguntas_by_pesquisa(db=db, pesquisa_id=pesquisa_id)
    return perguntas


@router.patch("/{projeto_id}/pesquisas/{pesquisa_id}/perguntas/{pergunta_id}", response_model=schemas.Pergunta)
def update_pergunta(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    pesquisa_id: int,
    pergunta_id: int,
    pergunta_in: schemas.PerguntaUpdate,
    current_user: models.Usuario = Depends(get_current_user)
):
    """Atualiza uma pergunta. Apenas o dono do projeto pode atualizá-la."""
    projeto = crud.get_projeto(db=db, projeto_id=projeto_id)
    if not projeto or projeto.coordenador_id != current_user.id:
        raise HTTPException(status_code=403, detail="Não tem permissão para editar neste projeto")

    db_pergunta = crud.get_pergunta(db=db, pergunta_id=pergunta_id)
    if not db_pergunta or db_pergunta.pesquisa_id != pesquisa_id:
        raise HTTPException(status_code=404, detail="Pergunta não encontrada nesta pesquisa")

    pergunta = crud.update_pergunta(db=db, db_obj=db_pergunta, obj_in=pergunta_in)
    return pergunta


@router.delete("/{projeto_id}/pesquisas/{pesquisa_id}/perguntas/{pergunta_id}", response_model=schemas.Pergunta)
def delete_pergunta(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    pesquisa_id: int,
    pergunta_id: int,
    current_user: models.Usuario = Depends(get_current_user)
):
    """Marca uma pergunta como inativa (soft delete)."""
    projeto = crud.get_projeto(db=db, projeto_id=projeto_id)
    if not projeto or projeto.coordenador_id != current_user.id:
        raise HTTPException(status_code=403, detail="Não tem permissão para excluir neste projeto")

    db_pergunta = crud.get_pergunta(db=db, pergunta_id=pergunta_id)
    if not db_pergunta or db_pergunta.pesquisa_id != pesquisa_id:
        raise HTTPException(status_code=404, detail="Pergunta não encontrada nesta pesquisa")

    pergunta = crud.delete_pergunta(db=db, db_obj=db_pergunta)
    return pergunta


@router.patch("/{projeto_id}/pesquisas/{pesquisa_id}", response_model=schemas.Pesquisa)
def update_pesquisa(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    pesquisa_id: int,
    pesquisa_in: schemas.PesquisaUpdate,
    current_user: models.Usuario = Depends(get_current_user)
):
    """Atualiza uma pesquisa. Apenas o dono do projeto pode editá-la."""
    projeto = crud.get_projeto(db=db, projeto_id=projeto_id)
    if not projeto or projeto.coordenador_id != current_user.id:
        raise HTTPException(status_code=403, detail="Não tem permissão para editar neste projeto")

    db_pesquisa = crud.get_pesquisa(db=db, pesquisa_id=pesquisa_id)
    if not db_pesquisa or db_pesquisa.projeto_id != projeto.id:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada neste projeto")

    pesquisa = crud.update_pesquisa(db=db, db_obj=db_pesquisa, obj_in=pesquisa_in)
    # Retornar versão serializável
    return JSONResponse(content=pesquisa_to_dict(pesquisa, db))


@router.delete("/{projeto_id}/pesquisas/{pesquisa_id}", response_model=schemas.Pesquisa)
def delete_pesquisa(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    pesquisa_id: int,
    current_user: models.Usuario = Depends(get_current_user)
):
    """Marca uma pesquisa como inativa (soft delete)."""
    projeto = crud.get_projeto(db=db, projeto_id=projeto_id)
    if not projeto or projeto.coordenador_id != current_user.id:
        raise HTTPException(status_code=403, detail="Não tem permissão para excluir neste projeto")

    db_pesquisa = crud.get_pesquisa(db=db, pesquisa_id=pesquisa_id)
    if not db_pesquisa or db_pesquisa.projeto_id != projeto.id:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada neste projeto")

    pesquisa = crud.delete_pesquisa(db=db, db_obj=db_pesquisa)
    return JSONResponse(content=pesquisa_to_dict(pesquisa, db))


@router.delete("/{projeto_id}", response_model=schemas.Projeto)
def delete_projeto(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    current_user: models.Usuario = Depends(get_current_user)
):
    """Marca um projeto como Excluído (soft delete)."""
    db_projeto = crud.get_projeto(db, projeto_id=projeto_id)
    if not db_projeto or db_projeto.coordenador_id != current_user.id:
        raise HTTPException(status_code=404, detail="Projeto não encontrado ou sem permissão")

    projeto = crud.delete_projeto(db=db, db_obj=db_projeto)
    # retornar dicionário serializável (ou mensagem)
    return JSONResponse(content=projeto_para_dict(projeto, db))


@router.patch("/{projeto_id}/pesquisas/{pesquisa_id}/geofence", response_model=schemas.Pesquisa)
def update_pesquisa_geofence(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    pesquisa_id: int,
    geofence_in: schemas.GeofenceUpdate,
    current_user: models.Usuario = Depends(get_current_user)
):
    """
    Atualiza a cerca eletrônica (geofence) de uma pesquisa.
    Apenas o dono do projeto pode realizar esta ação.
    """
    projeto = crud.get_projeto(db=db, projeto_id=projeto_id)
    if not projeto or projeto.coordenador_id != current_user.id:
        raise HTTPException(status_code=403, detail="Não tem permissão para editar neste projeto")

    db_pesquisa = crud.get_pesquisa(db=db, pesquisa_id=pesquisa_id)
    if not db_pesquisa or db_pesquisa.projeto_id != projeto.id:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada neste projeto")

    pesquisa = crud.update_geofence_pesquisa(db=db, db_obj=db_pesquisa, obj_in=geofence_in)
    return JSONResponse(content=pesquisa_to_dict(pesquisa, db))

@router.post("/pesquisas/{pesquisa_id}/setores", response_model=schemas.Setor)
def create_setor_endpoint(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    setor_in: schemas.SetorCreate,
    current_user: models.Usuario = Depends(get_current_user)
):
    # (Adicione validação de permissão se necessário)
    return crud.create_setor(db=db, setor_in=setor_in, pesquisa_id=pesquisa_id)

@router.get("/pesquisas/{pesquisa_id}/setores")
def list_setores_endpoint(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    current_user: models.Usuario = Depends(get_current_user)
):
    setores_raw = crud.get_setores_by_pesquisa(db=db, pesquisa_id=pesquisa_id)
    # Processa o GeoJSON para retorno
    resultado = []
    for s in setores_raw:
        geojson = json.loads(s.geometria_geojson) if s.geometria_geojson else None
        resultado.append({
            "id": s.id,
            "nome": s.nome,
            "meta": s.meta,
            "agente_id": s.agente_id,
            "pesquisa_id": s.pesquisa_id,
            "geometria": geojson # O front vai ler isso para desenhar
        })
    return resultado

@router.delete("/setores/{setor_id}")
def delete_setor_endpoint(
    *,
    db: Session = Depends(get_db),
    setor_id: int,
    current_user: models.Usuario = Depends(get_current_user)
):
    crud.delete_setor(db=db, setor_id=setor_id)
    return {"ok": True}
