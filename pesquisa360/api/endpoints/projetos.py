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

def ordenar_perguntas_da_pesquisa(pesquisa: models.Pesquisa) -> models.Pesquisa:
    perguntas = getattr(pesquisa, "perguntas", None)
    if perguntas:
        perguntas.sort(key=lambda pergunta: (pergunta.ordem, pergunta.id))
    return pesquisa

def ordenar_perguntas_das_pesquisas(pesquisas):
    for pesquisa in pesquisas or []:
        ordenar_perguntas_da_pesquisa(pesquisa)
    return pesquisas

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
    lista_perguntas = sorted(
        getattr(p, "perguntas", []) or [],
        key=lambda pergunta: (pergunta.ordem, pergunta.id)
    )
    
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
        "ativo": p.ativo,
        "projeto_id": p.projeto_id,
        "tipo_pesquisa": p.tipo_pesquisa,
        "tolerancia_metros": p.tolerancia_metros,
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

def setor_to_dict(s) -> dict:
    geojson = json.loads(s.geojson) if s.geojson else None
    return {
        "id": s.id,
        "nome": s.nome,
        "meta": s.meta,
        "tolerancia": s.tolerancia,
        "tolerancia_metros": s.tolerancia,
        "agente_id": s.agente_id,
        "agente_nome": s.agente_nome,
        "geometria": geojson
    }

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
    ordenar_perguntas_das_pesquisas(getattr(db_projeto, "pesquisas", []))
    return db_projeto

@router.patch("/projetos/{projeto_id}", response_model=schemas.Projeto)
def update_projeto(
    projeto_id: int,
    projeto_update: schemas.ProjetoUpdate,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Atualiza um projeto específico (com validação de empresa)."""
    db_projeto = crud.get_projeto(db=db, projeto_id=projeto_id, current_user=current_user)
    if db_projeto is None:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")

    update_data = projeto_update.model_dump(exclude_unset=True)
    update_data.pop("company_id", None)

    for key, value in update_data.items():
        setattr(db_projeto, key, value)

    db.commit()
    db.refresh(db_projeto)
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
    return ordenar_perguntas_das_pesquisas(pesquisas)

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

@router.patch("/projetos/{projeto_id}/pesquisas/{pesquisa_id}", response_model=schemas.Pesquisa)
def update_pesquisa(
    projeto_id: int,
    pesquisa_id: int,
    pesquisa_update: schemas.PesquisaUpdate,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Atualiza uma pesquisa (título, tipo, ativo)."""
    # 1. Valida se o projeto pertence à empresa do usuário
    projeto = crud.get_projeto(db=db, projeto_id=projeto_id, current_user=current_user)
    if not projeto:
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")

    # 2. Busca a pesquisa para garantir que pertence ao projeto informado
    db_pesquisa = db.query(models.Pesquisa).filter(
        models.Pesquisa.id == pesquisa_id,
        models.Pesquisa.projeto_id == projeto_id
    ).first()

    if not db_pesquisa:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada.")

    # 3. Atualiza apenas os campos enviados
    update_data = pesquisa_update.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_pesquisa, key, value)

    db.commit()
    db.refresh(db_pesquisa)
    return db_pesquisa

@router.patch("/projetos/{projeto_id}/pesquisas/{pesquisa_id}/geofence")
def update_pesquisa_geofence(
    projeto_id: int,
    pesquisa_id: int,
    geofence: schemas.GeofenceUpdate,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Atualiza a cerca eletrônica e tolerância de uma pesquisa."""
    projeto = crud.get_projeto(db=db, projeto_id=projeto_id, current_user=current_user)
    if not projeto:
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")

    db_pesquisa = db.query(models.Pesquisa).filter(
        models.Pesquisa.id == pesquisa_id,
        models.Pesquisa.projeto_id == projeto_id
    ).first()
    if not db_pesquisa:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada.")

    if not geofence.cerca_eletronica or len(geofence.cerca_eletronica) < 3:
        raise HTTPException(
            status_code=400,
            detail="A cerca eletrônica precisa de pelo menos 3 pontos."
        )

    coords = [coord for coord in geofence.cerca_eletronica]
    if coords[0] != coords[-1]:
        coords.append(coords[0])

    coords_str = ", ".join([f"{point.lng} {point.lat}" for point in coords])
    wkt = f"POLYGON(({coords_str}))"

    db_pesquisa.cerca_eletronica = func.ST_GeomFromText(wkt, 4326)
    db_pesquisa.tolerancia_metros = geofence.tolerancia_metros

    db.commit()
    db.refresh(db_pesquisa)
    
    # Serializar cerca como lista de {lat, lng}
    cerca_list = []
    if db_pesquisa.cerca_eletronica:
        cerca_str = db.query(func.ST_AsGeoJSON(db_pesquisa.cerca_eletronica)).scalar()
        if cerca_str:
            geojson = json.loads(cerca_str)
            coords = geojson.get("coordinates", [[]])[0]  # Primeiro ring do Polygon
            cerca_list = [{"lat": point[1], "lng": point[0]} for point in coords[:-1]]  # Excluir último ponto se fechado
    
    return {
        "id": db_pesquisa.id,
        "projeto_id": db_pesquisa.projeto_id,
        "cerca_eletronica": cerca_list,
        "tolerancia_metros": db_pesquisa.tolerancia_metros,
        "message": "Cerca eletrônica atualizada com sucesso"
    }

@router.get("/projetos/{projeto_id}/pesquisas/{pesquisa_id}/geofence")
def get_pesquisa_geofence(
    projeto_id: int,
    pesquisa_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Retorna a cerca eletrônica de uma pesquisa."""
    projeto = crud.get_projeto(db=db, projeto_id=projeto_id, current_user=current_user)
    if not projeto:
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")

    db_pesquisa = db.query(models.Pesquisa).filter(
        models.Pesquisa.id == pesquisa_id,
        models.Pesquisa.projeto_id == projeto_id
    ).first()
    if not db_pesquisa:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada.")

    # Serializar cerca como lista de {lat, lng}
    cerca_list = []
    if db_pesquisa.cerca_eletronica:
        cerca_str = db.query(func.ST_AsGeoJSON(db_pesquisa.cerca_eletronica)).scalar()
        if cerca_str:
            geojson = json.loads(cerca_str)
            coords = geojson.get("coordinates", [[]])[0]  # Primeiro ring do Polygon
            cerca_list = [{"lat": point[1], "lng": point[0]} for point in coords[:-1]]  # Excluir último ponto se fechado

    return {
        "id": db_pesquisa.id,
        "projeto_id": db_pesquisa.projeto_id,
        "cerca_eletronica": cerca_list,
        "tolerancia_metros": db_pesquisa.tolerancia_metros
    }

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

@router.patch("/projetos/{projeto_id}/pesquisas/{pesquisa_id}/perguntas/reordenar", response_model=List[schemas.Pergunta])
def reordenar_perguntas(
    projeto_id: int,
    pesquisa_id: int,
    payload: schemas.PerguntasReordenarPayload,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Reordena perguntas de uma pesquisa em lote."""
    projeto = crud.get_projeto(db=db, projeto_id=projeto_id, current_user=current_user)
    if not projeto:
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")

    pesquisa = db.query(models.Pesquisa).filter(
        models.Pesquisa.id == pesquisa_id,
        models.Pesquisa.projeto_id == projeto_id
    ).first()
    if not pesquisa:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada.")

    ids = [item.id for item in payload.perguntas]
    if len(ids) != len(set(ids)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="IDs de perguntas duplicados no payload."
        )

    return crud.reordenar_perguntas(
        db=db,
        pesquisa_id=pesquisa_id,
        itens=payload.perguntas
    )

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
    return [setor_to_dict(s) for s in setores_raw]

@router.post("/projetos/{projeto_id}/pesquisas/{pesquisa_id}/setores")
def create_setor_by_projeto_pesquisa(
    projeto_id: int,
    pesquisa_id: int,
    setor_payload: schemas.SetorGeofenceCreate,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Cria um setor para uma pesquisa dentro de um projeto."""
    projeto = crud.get_projeto(db=db, projeto_id=projeto_id, current_user=current_user)
    if not projeto:
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")

    db_pesquisa = db.query(models.Pesquisa).filter(
        models.Pesquisa.id == pesquisa_id,
        models.Pesquisa.projeto_id == projeto_id
    ).first()
    if not db_pesquisa:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada.")

    try:
        coords = setor_payload.get_coords()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    setor_in = schemas.SetorCreate(
        nome=setor_payload.nome,
        meta=setor_payload.meta,
        agente_id=setor_payload.agente_id,
        tolerancia=setor_payload.tolerancia_metros or 50,
        geometria_coords=coords
    )

    db_setor = crud.create_setor(
        db=db,
        setor_in=setor_in,
        pesquisa_id=pesquisa_id,
        current_user=current_user
    )

    geojson = None
    if db_setor.geometria is not None:
        geojson_str = db.query(func.ST_AsGeoJSON(db_setor.geometria)).scalar()
        if geojson_str:
            geojson = json.loads(geojson_str)
            coords_geo = geojson.get("coordinates", [[]])[0]
            if coords_geo and coords_geo[0] == coords_geo[-1]:
                coords_geo = coords_geo[:-1]
            poligono = [{"lat": p[1], "lng": p[0]} for p in coords_geo]
        else:
            poligono = []
    else:
        poligono = []

    return {
        "id": db_setor.id,
        "pesquisa_id": db_setor.pesquisa_id,
        "nome": db_setor.nome,
        "meta": db_setor.meta,
        "tolerancia_metros": db_setor.tolerancia,
        "agente_id": db_setor.agente_id,
        "poligono": poligono
    }

@router.delete("/projetos/{projeto_id}/pesquisas/{pesquisa_id}/setores/{setor_id}")
def delete_setor_by_projeto_pesquisa(
    projeto_id: int,
    pesquisa_id: int,
    setor_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Exclui um setor de uma pesquisa dentro de um projeto."""
    projeto = crud.get_projeto(db=db, projeto_id=projeto_id, current_user=current_user)
    if not projeto:
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")

    db_pesquisa = db.query(models.Pesquisa).filter(
        models.Pesquisa.id == pesquisa_id,
        models.Pesquisa.projeto_id == projeto_id
    ).first()
    if not db_pesquisa:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada.")

    db_setor = db.query(models.Setor).filter(
        models.Setor.id == setor_id,
        models.Setor.pesquisa_id == pesquisa_id
    ).first()
    if not db_setor:
        raise HTTPException(status_code=404, detail="Setor não encontrado.")

    db.delete(db_setor)
    db.commit()
    return {"message": "Setor excluído com sucesso"}

@router.get("/projetos/{projeto_id}/pesquisas/{pesquisa_id}/setores")
def list_setores_by_projeto_pesquisa(
    projeto_id: int,
    pesquisa_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Lista setores de uma pesquisa aninhada em projeto."""
    # 1. Valida projeto
    projeto = crud.get_projeto(db=db, projeto_id=projeto_id, current_user=current_user)
    if not projeto:
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")

    # 2. Valida pesquisa pertence ao projeto
    db_pesquisa = db.query(models.Pesquisa).filter(
        models.Pesquisa.id == pesquisa_id,
        models.Pesquisa.projeto_id == projeto_id
    ).first()
    if not db_pesquisa:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada.")

    # 3. Busca setores
    setores_raw = crud.get_setores_by_pesquisa(db=db, pesquisa_id=pesquisa_id)
    
    # 4. Processa retorno
    return [setor_to_dict(s) for s in setores_raw]
