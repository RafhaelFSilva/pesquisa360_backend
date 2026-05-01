import json
from typing import List, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from pesquisa360 import crud, schemas
from pesquisa360.db import models
from pesquisa360.core.dependencies import get_db, get_current_user

router = APIRouter()

# --- Helpers ---

def pesquisa_to_dict(p: models.Pesquisa, db: Session) -> dict:
    """Converte Pesquisa para dict, tratando GeoJSON e Perguntas."""
    # 1. Trata a Cerca Eletrônica (GeoJSON)
    cerca_geojson = None
    if hasattr(p, "cerca_eletronica") and p.cerca_eletronica is not None:
        cerca_str = db.query(func.ST_AsGeoJSON(p.cerca_eletronica)).scalar()
        if cerca_str:
            try:
                cerca_geojson = json.loads(cerca_str)
            except Exception:
                cerca_geojson = cerca_str

    # 2. Trata as Perguntas e Opções
    perguntas_list = []
    lista_perguntas = getattr(p, "perguntas", []) or []
    
    for pergunta in lista_perguntas:
        opcoes_list = None
        if pergunta.opcoes:
            try:
                if isinstance(pergunta.opcoes, str):
                    opcoes_list = json.loads(pergunta.opcoes)
                else:
                    opcoes_list = pergunta.opcoes
            except Exception:
                opcoes_list = []
        
        perguntas_list.append({
            "id": pergunta.id,
            "texto_pergunta": pergunta.texto_pergunta,
            "tipo_pergunta": pergunta.tipo_pergunta,
            "opcoes": opcoes_list,
            "eh_obrigatoria": pergunta.eh_obrigatoria,
            "ordem": pergunta.ordem
        })

    return {
        "id": p.id,
        "titulo": p.titulo,
        "descricao": p.descricao,
        "data_inicio": p.data_inicio.isoformat() if p.data_inicio else None,
        "data_fim": p.data_fim.isoformat() if p.data_fim else None,
        "ativo": p.ativo,
        "projeto_id": p.projeto_id,
        "tipo_pesquisa": p.tipo_pesquisa,
        "cerca_eletronica_geojson": cerca_geojson,
        "perguntas": perguntas_list
    }

def check_pesquisa_access(db: Session, pesquisa_id: int, current_user: models.Usuario):
    """
    Verifica se a pesquisa pertence a um projeto da empresa do usuário.
    Lança 404/403 se não tiver acesso.
    """
    pesquisa = db.query(models.Pesquisa).join(models.Projeto).filter(
        models.Pesquisa.id == pesquisa_id,
        models.Projeto.company_id == current_user.company_id
    ).first()
    
    if not pesquisa:
        raise HTTPException(
            status_code=404, 
            detail="Pesquisa não encontrada ou você não tem permissão para acessá-la."
        )
    return pesquisa

# --- Rotas de Projetos ---

@router.get("/projetos/", response_model=List[schemas.Projeto])
def read_projetos(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Lista projetos da empresa do usuário."""
    return crud.get_projetos(db, current_user=current_user, skip=skip, limit=limit)

@router.post("/projetos/", response_model=schemas.Projeto)
def create_projeto(
    projeto: schemas.ProjetoCreate,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Cria projeto vinculado à empresa do usuário."""
    return crud.create_projeto(db=db, projeto=projeto, current_user=current_user)

@router.get("/projetos/{projeto_id}", response_model=schemas.Projeto)
def read_projeto(
    projeto_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Busca um projeto específico (com validação de empresa)."""
    db_projeto = crud.get_projeto(db, projeto_id=projeto_id, current_user=current_user)
    if db_projeto is None:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")
    return db_projeto

# --- Rotas de Pesquisas ---

@router.get("/projetos/{projeto_id}/pesquisas/", response_model=List[schemas.Pesquisa])
def read_pesquisas(
    projeto_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Lista pesquisas de um projeto (validando acesso)."""
    # crud.get_pesquisas já valida se o projeto pertence ao current_user
    pesquisas = crud.get_pesquisas(db, projeto_id=projeto_id, current_user=current_user)
    # Se retornou vazio, pode ser que o projeto não exista ou não tenha pesquisas.
    # O CRUD cuida da segurança.
    return pesquisas

@router.post("/projetos/{projeto_id}/pesquisas/", response_model=schemas.Pesquisa)
def create_pesquisa(
    projeto_id: int,
    pesquisa: schemas.PesquisaCreate,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Cria pesquisa dentro de um projeto da empresa."""
    try:
        return crud.create_pesquisa(
            db=db, 
            pesquisa=pesquisa, 
            projeto_id=projeto_id, 
            current_user=current_user
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/pesquisas/{pesquisa_id}")
def read_pesquisa_detail(
    pesquisa_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Retorna detalhes completos da pesquisa (incluindo GeoJSON e Perguntas)."""
    # Valida acesso
    pesquisa = check_pesquisa_access(db, pesquisa_id, current_user)
    
    # Retorna usando o helper de conversão
    return JSONResponse(content=pesquisa_to_dict(pesquisa, db))

# --- Rotas de Perguntas ---

@router.post("/pesquisas/{pesquisa_id}/perguntas/", response_model=schemas.Pergunta)
def create_pergunta(
    pesquisa_id: int,
    pergunta: schemas.PerguntaCreate,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Cria pergunta em uma pesquisa (com validação de acesso)."""
    # 1. Valida se a pesquisa pertence à empresa do usuário
    check_pesquisa_access(db, pesquisa_id, current_user)

    # 2. Cria a pergunta
    return crud.create_pergunta(db=db, pergunta=pergunta, pesquisa_id=pesquisa_id)

@router.get("/pesquisas/{pesquisa_id}/perguntas/", response_model=List[schemas.Pergunta])
def read_perguntas(
    pesquisa_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Lista perguntas de uma pesquisa."""
    check_pesquisa_access(db, pesquisa_id, current_user)
    return crud.get_perguntas(db, pesquisa_id=pesquisa_id)

@router.patch("/pesquisas/{pesquisa_id}/perguntas/{pergunta_id}", response_model=schemas.Pergunta)
def update_pergunta(
    pesquisa_id: int,
    pergunta_id: int,
    pergunta_in: schemas.PerguntaUpdate,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Atualiza uma pergunta específica."""
    check_pesquisa_access(db, pesquisa_id, current_user)
    return crud.update_pergunta(db=db, pergunta_id=pergunta_id, pergunta_in=pergunta_in)

@router.delete("/pesquisas/{pesquisa_id}/perguntas/{pergunta_id}")
def delete_pergunta(
    pesquisa_id: int,
    pergunta_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Exclui uma pergunta."""
    check_pesquisa_access(db, pesquisa_id, current_user)
    crud.delete_pergunta(db=db, pergunta_id=pergunta_id)
    return {"detail": "Pergunta excluída com sucesso"}


# --- Soft Delete ---

# In pesquisa360/api/endpoints/projetos.py

@router.delete("/pesquisas/{pesquisa_id}/perguntas/{pergunta_id}")
def delete_pergunta_endpoint(
    pesquisa_id: int,
    pergunta_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Marca uma pergunta como inativa (soft delete)."""
    # 1. Valida acesso à pesquisa
    check_pesquisa_access(db, pesquisa_id, current_user)

    # 2. Busca o objeto completo da Pergunta
    db_pergunta = db.query(models.Pergunta).filter(
        models.Pergunta.id == pergunta_id, 
        models.Pergunta.pesquisa_id == pesquisa_id
    ).first()
    
    if not db_pergunta:
        raise HTTPException(status_code=404, detail="Pergunta não encontrada.")

    # 3. Chama o CRUD passando o objeto 'db_obj', NÃO o 'pergunta_id'
    crud.delete_pergunta(db=db, db_obj=db_pergunta)
    
    return {"detail": "Pergunta excluída com sucesso."}

@router.delete("/projetos/{projeto_id}/pesquisas/{pesquisa_id}")
def delete_pesquisa_endpoint(
    projeto_id: int,
    pesquisa_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    # Valida se o projeto pertence à empresa/coordenador
    projeto = crud.get_projeto(db=db, projeto_id=projeto_id, current_user=current_user)
    if not projeto:
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")

    db_pesquisa = db.query(models.Pesquisa).filter(
        models.Pesquisa.id == pesquisa_id, 
        models.Pesquisa.projeto_id == projeto_id
    ).first()
    
    if not db_pesquisa:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada.")

    pesquisa = crud.delete_pesquisa(db=db, db_obj=db_pesquisa)
    return JSONResponse(content={"detail": "Pesquisa desativada com sucesso"})


@router.delete("/projetos/{projeto_id}")
def delete_projeto_endpoint(
    projeto_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    db_projeto = crud.get_projeto(db=db, projeto_id=projeto_id, current_user=current_user)
    if not db_projeto:
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")

    crud.delete_projeto(db=db, db_obj=db_projeto)
    return {"detail": "Projeto marcado como excluído."}

# --- Rotas de Setores (Missões) ---

@router.post("/pesquisas/{pesquisa_id}/setores", response_model=schemas.Setor)
def create_setor_endpoint(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    setor_in: schemas.SetorCreate,
    current_user: models.Usuario = Depends(get_current_user)
):
    """Cria um setor/missão para um agente."""
    # O crud.create_setor atualizado já recebe current_user para validar
    try:
        return crud.create_setor(
            db=db, 
            setor_in=setor_in, 
            pesquisa_id=pesquisa_id, 
            current_user=current_user
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/pesquisas/{pesquisa_id}/setores")
def list_setores_endpoint(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    current_user: models.Usuario = Depends(get_current_user)
):
    """Lista setores de uma pesquisa (convertendo GeoJSON)."""
    # 1. Valida acesso
    check_pesquisa_access(db, pesquisa_id, current_user)

    # 2. Busca setores
    setores_raw = crud.get_setores_by_pesquisa(db=db, pesquisa_id=pesquisa_id)
    
    # 3. Processa retorno
    resultado = []
    for s in setores_raw:
        geojson = json.loads(s.geojson) if s.geojson else None
        resultado.append({
            "id": s.id,
            "nome": s.nome,
            "meta": s.meta,
            "tolerancia": s.tolerancia,
            "geometria": geojson
        })
    return resultado