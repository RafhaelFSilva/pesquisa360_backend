import json
from typing import List, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from pesquisa360 import crud, schemas
from pesquisa360.db import models
from pesquisa360.services import acessos, auditoria, notificacoes
from pesquisa360.core.dependencies import (
    get_db,
    get_current_user,
    is_superadmin,
    require_manager_or_superadmin,
)
from pesquisa360.core.utils import web_point
from pesquisa360.services import pergunta_territorio, setor_territorio
from pesquisa360.question_types import normalize_question_type
from pesquisa360.core.rbac import Permissao, require_permissao

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
            "tipo_pergunta": normalize_question_type(pergunta.tipo_pergunta),
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
        acessos.filtro_projeto_acessivel(current_user)
    ).first()
    
    if not pesquisa:
        raise HTTPException(
            status_code=404, 
            detail="Pesquisa não encontrada ou você não tem permissão para acessá-la."
        )
    return pesquisa

def setor_to_dict(s, db: Session, progresso: dict | None = None, municipio=None) -> dict:
    geojson = json.loads(s.geojson) if s.geojson else None
    agentes = crud.listar_agentes_ativos_setor(db, s.id)
    # ADR-035: municipio de referencia formal do setor (persistido; composicao
    # como fallback historico). `municipio` chega em lote pelas listagens.
    if municipio is None:
        municipio = pergunta_territorio.resolver_municipio_setor(db, s.id)
    payload = {
        "id": s.id,
        "nome": s.nome,
        "meta": s.meta,
        "finalidade": s.finalidade,
        "tolerancia": s.tolerancia,
        "tolerancia_metros": s.tolerancia,
        "agente_id": s.agente_id,
        "agente_nome": s.agente_nome,
        "agente_ids": [agente.id for agente in agentes],
        "agentes": [{"id": agente.id, "nome": agente.nome} for agente in agentes],
        "geometria": geojson,
        "municipio_territorio_id": getattr(s, "municipio_territorio_id", None),
        "municipio": municipio.municipio.to_dict() if municipio.resolvido else None,
        "municipio_status": municipio.status,
    }
    if progresso is not None:
        payload.update(progresso)
    return payload

# --- Rotas de Projetos ---

@router.get("/projetos/", response_model=List[schemas.Projeto], dependencies=[Depends(require_permissao(Permissao.PROJETO_VER))])
def read_projetos(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Lista projetos da empresa do usuário."""
    return crud.get_projetos(db, current_user=current_user, skip=skip, limit=limit)

@router.post("/projetos/", response_model=schemas.Projeto, dependencies=[Depends(require_permissao(Permissao.PROJETO_CRIAR))])
def create_projeto(
    projeto: schemas.ProjetoCreate,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Cria projeto vinculado à empresa do usuário."""
    if is_superadmin(current_user):
        if projeto.company_id is None or not crud.get_company(
            db=db,
            company_id=projeto.company_id,
        ):
            raise HTTPException(status_code=404, detail="Empresa nao encontrada.")
        company_id = projeto.company_id
    else:
        company_id = current_user.company_id

    return crud.create_projeto(
        db=db,
        projeto=projeto,
        current_user=current_user,
        company_id=company_id,
    )

@router.get("/projetos/{projeto_id}", response_model=schemas.Projeto, dependencies=[Depends(require_permissao(Permissao.PROJETO_VER))])
def read_projeto(
    projeto_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Busca um projeto específico (com validação de empresa)."""
    db_projeto = crud.get_projeto(db, projeto_id=projeto_id, current_user=current_user)
    if db_projeto is None:
        # ADR-039: classifica o 404 (inexistente / sem ACL / outro tenant).
        auditoria.registrar_negacao_projeto(db, current_user, projeto_id)
        raise HTTPException(status_code=404, detail="Projeto não encontrado")
    ordenar_perguntas_das_pesquisas(getattr(db_projeto, "pesquisas", []))
    # Ponto por onde o Web "entra" no projeto: a entrada logica e auditada
    # aqui, no Backend -- nunca por sinal do Frontend.
    # Deduplicado por 15 min (usuario, projeto); company_id = tenant do projeto.
    # ADR-040: a notificacao ao Gerente responsavel e consequencia do evento
    # PERSISTIDO -- um GET deduplicado nao notifica. Fail-soft: nunca 500.
    if auditoria.registrar_acesso_projeto(current_user, db_projeto):
        notificacoes.notificar_acesso_projeto(current_user, db_projeto)
    return db_projeto

@router.patch("/projetos/{projeto_id}", response_model=schemas.Projeto, dependencies=[Depends(require_permissao(Permissao.PROJETO_EDITAR))])
def update_projeto(
    projeto_id: int,
    projeto_update: schemas.ProjetoUpdate,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Atualiza um projeto específico (com validação de empresa)."""
    db_projeto = crud.get_projeto(
        db=db,
        projeto_id=projeto_id,
        current_user=current_user,
        allow_global=is_superadmin(current_user),
    )
    if db_projeto is None:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")

    return crud.update_projeto(
        db=db,
        db_projeto=db_projeto,
        projeto_update=projeto_update,
    )

# --- Rotas de Pesquisas ---

@router.get("/projetos/{projeto_id}/pesquisas/", response_model=List[schemas.Pesquisa], dependencies=[Depends(require_permissao(Permissao.PESQUISA_VER))])
def read_pesquisas(
    projeto_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Lista pesquisas de um projeto (validando acesso)."""
    # ADR-037: projeto fora do escopo responde 404, como todo recurso fora da
    # ACL. Antes devolvia 200 com lista vazia -- nao vazava dado, mas confundia
    # "sem pesquisas" com "sem acesso".
    acessos.assegurar_acesso_projeto(db, current_user, projeto_id)
    pesquisas = crud.get_pesquisas(db, projeto_id=projeto_id, current_user=current_user)
    return ordenar_perguntas_das_pesquisas(pesquisas)

@router.post("/projetos/{projeto_id}/pesquisas/", response_model=schemas.Pesquisa, dependencies=[Depends(require_permissao(Permissao.PESQUISA_GERENCIAR))])
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

@router.patch("/projetos/{projeto_id}/pesquisas/{pesquisa_id}", response_model=schemas.Pesquisa, dependencies=[Depends(require_permissao(Permissao.PESQUISA_GERENCIAR))])
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

@router.patch("/projetos/{projeto_id}/pesquisas/{pesquisa_id}/geofence", dependencies=[Depends(require_permissao(Permissao.PESQUISA_GERENCIAR))])
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
            cerca_list = [web_point(point[1], point[0]) for point in coords[:-1]]  # Excluir último ponto se fechado
    
    return {
        "id": db_pesquisa.id,
        "projeto_id": db_pesquisa.projeto_id,
        "cerca_eletronica": cerca_list,
        "tolerancia_metros": db_pesquisa.tolerancia_metros,
        "message": "Cerca eletrônica atualizada com sucesso"
    }

@router.get("/projetos/{projeto_id}/pesquisas/{pesquisa_id}/geofence", dependencies=[Depends(require_permissao(Permissao.PESQUISA_VER))])
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
            cerca_list = [web_point(point[1], point[0]) for point in coords[:-1]]  # Excluir último ponto se fechado

    return {
        "id": db_pesquisa.id,
        "projeto_id": db_pesquisa.projeto_id,
        "cerca_eletronica": cerca_list,
        "tolerancia_metros": db_pesquisa.tolerancia_metros
    }

@router.get("/pesquisas/{pesquisa_id}", dependencies=[Depends(require_permissao(Permissao.PESQUISA_VER))])
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

def _pergunta_com_municipios(db: Session, pergunta, municipios=None):
    """Serializa a pergunta com os municipios associados (FASE F).

    O schema de resposta e montado a partir do ORM e depois recebe a lista de
    municipios, que nao e um atributo simples do modelo.
    """
    dados = schemas.Pergunta.model_validate(pergunta).model_dump()
    if municipios is None:
        municipios = pergunta_territorio.municipios_da_pergunta(db, pergunta.id)
    dados["municipios"] = municipios
    dados["municipio_ids"] = [m["id"] for m in municipios]
    # Instancia do schema, nao dict: quem chama a funcao do endpoint
    # diretamente (testes, outros modulos) continua lendo `.id`, `.opcoes`.
    return schemas.Pergunta(**dados)


@router.post("/pesquisas/{pesquisa_id}/perguntas/", response_model=schemas.Pergunta, dependencies=[Depends(require_permissao(Permissao.PESQUISA_GERENCIAR))])
def create_pergunta(
    pesquisa_id: int,
    pergunta: schemas.PerguntaCreate,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Cria pergunta em uma pesquisa (com validação de acesso)."""
    # 1. Valida se a pesquisa pertence à empresa do usuário
    pesquisa = check_pesquisa_access(db, pesquisa_id, current_user)

    # 2. Cria a pergunta
    db_pergunta = crud.create_pergunta(
        db=db,
        pergunta=pergunta,
        pesquisa_id=pesquisa_id,
        projeto_id=pesquisa.projeto_id,
        current_user=current_user,
    )
    return _pergunta_com_municipios(db, db_pergunta)

@router.get("/pesquisas/{pesquisa_id}/perguntas/", response_model=List[schemas.Pergunta], dependencies=[Depends(require_permissao(Permissao.PESQUISA_VER))])
def read_perguntas(
    pesquisa_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Lista perguntas de uma pesquisa."""
    check_pesquisa_access(db, pesquisa_id, current_user)
    perguntas = crud.get_perguntas(db, pesquisa_id=pesquisa_id)
    # Municipios de todas as perguntas em uma consulta, nao uma por pergunta.
    municipios = pergunta_territorio.municipios_por_pergunta(db, [p.id for p in perguntas])
    return [_pergunta_com_municipios(db, p, municipios.get(p.id, [])) for p in perguntas]

@router.patch("/projetos/{projeto_id}/pesquisas/{pesquisa_id}/perguntas/reordenar", response_model=List[schemas.Pergunta], dependencies=[Depends(require_permissao(Permissao.PESQUISA_GERENCIAR))])
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

@router.patch("/pesquisas/{pesquisa_id}/perguntas/{pergunta_id}", response_model=schemas.Pergunta, dependencies=[Depends(require_permissao(Permissao.PESQUISA_GERENCIAR))])
def update_pergunta(
    pesquisa_id: int,
    pergunta_id: int,
    pergunta_in: schemas.PerguntaUpdate,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Atualiza uma pergunta específica."""
    pesquisa = check_pesquisa_access(db, pesquisa_id, current_user)
    pergunta = crud.update_pergunta(
        db=db,
        pesquisa_id=pesquisa_id,
        pergunta_id=pergunta_id,
        pergunta_in=pergunta_in,
        projeto_id=pesquisa.projeto_id,
        current_user=current_user,
    )
    if not pergunta:
        raise HTTPException(status_code=404, detail="Pergunta não encontrada.")
    return _pergunta_com_municipios(db, pergunta)

@router.delete("/pesquisas/{pesquisa_id}/perguntas/{pergunta_id}", dependencies=[Depends(require_permissao(Permissao.PESQUISA_GERENCIAR))])
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

@router.delete("/projetos/{projeto_id}/pesquisas/{pesquisa_id}", dependencies=[Depends(require_permissao(Permissao.PESQUISA_GERENCIAR))])
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


@router.delete("/projetos/{projeto_id}", dependencies=[Depends(require_permissao(Permissao.PROJETO_EXCLUIR))])
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

@router.post("/pesquisas/{pesquisa_id}/setores", response_model=schemas.Setor, dependencies=[Depends(require_permissao(Permissao.PESQUISA_GERENCIAR))])
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
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/pesquisas/{pesquisa_id}/setores", dependencies=[Depends(require_permissao(Permissao.PESQUISA_VER))])
def list_setores_endpoint(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    finalidade: schemas.FinalidadeSetor | None = None,
    current_user: models.Usuario = Depends(get_current_user)
):
    """Lista setores de uma pesquisa (convertendo GeoJSON)."""
    # 1. Valida acesso
    check_pesquisa_access(db, pesquisa_id, current_user)

    # 2. Busca setores
    setores_raw = crud.get_setores_by_pesquisa(
        db=db,
        pesquisa_id=pesquisa_id,
        finalidade=finalidade,
    )
    progressos = crud.obter_progressos_setores(
        db, setores_raw, pesquisa_id=pesquisa_id
    )
    
    # 3. Processa retorno
    return [setor_to_dict(s, db, progressos[s.id]) for s in setores_raw]

@router.post("/projetos/{projeto_id}/pesquisas/{pesquisa_id}/setores", dependencies=[Depends(require_permissao(Permissao.PESQUISA_GERENCIAR))])
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

    setor_data = {
        "nome": setor_payload.nome,
        "meta": setor_payload.meta,
        "agente_id": setor_payload.agente_id,
        "tolerancia": setor_payload.tolerancia_metros or 50,
        "finalidade": setor_payload.finalidade,
        "geometria_coords": coords,
        "municipio_territorio_id": setor_payload.municipio_territorio_id,
    }
    if "agente_ids" in setor_payload.model_fields_set:
        setor_data["agente_ids"] = setor_payload.agente_ids
    setor_in = schemas.SetorCreate(**setor_data)

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
            poligono = [web_point(p[1], p[0]) for p in coords_geo]
        else:
            poligono = []
    else:
        poligono = []

    # Mesmo caminho de serializacao do GET/PATCH.
    #
    # A assimetria existia porque `setor_to_dict` consome uma LINHA de
    # `get_setores_by_pesquisa` (com os rotulos `geojson` e `agente_nome`), e
    # aqui so ha o objeto ORM recem-criado -- que nao tem esses atributos. O
    # PATCH ja resolve isso reconsultando; o POST passa a fazer igual, e as
    # metricas saem do helper oficial da FASE D, sem calculo duplicado.
    setores_raw = crud.get_setores_by_pesquisa(db=db, pesquisa_id=pesquisa_id)
    setor_criado = next(
        (setor for setor in setores_raw if setor.id == db_setor.id), None
    )
    if setor_criado is None:
        raise HTTPException(status_code=404, detail="Setor nao encontrado.")

    progresso = crud.obter_progressos_setores(
        db, [setor_criado], pesquisa_id=pesquisa_id
    )[setor_criado.id]
    payload = setor_to_dict(setor_criado, db, progresso)
    # Campos que so o POST devolvia continuam saindo: retirar `poligono` ou
    # `pesquisa_id` para "padronizar" quebraria quem ja consome esta resposta.
    payload["pesquisa_id"] = db_setor.pesquisa_id
    payload["poligono"] = poligono
    return payload


@router.patch("/projetos/{projeto_id}/pesquisas/{pesquisa_id}/setores/{setor_id}", dependencies=[Depends(require_permissao(Permissao.PESQUISA_GERENCIAR))])
def update_setor_by_projeto_pesquisa(
    projeto_id: int,
    pesquisa_id: int,
    setor_id: int,
    setor_payload: schemas.SetorUpdate,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_manager_or_superadmin),
):
    """Atualiza parcialmente um setor preservando seu ID."""
    try:
        db_setor = crud.update_setor(
            db=db,
            projeto_id=projeto_id,
            pesquisa_id=pesquisa_id,
            setor_id=setor_id,
            setor_update=setor_payload,
            current_user=current_user,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    setores_raw = crud.get_setores_by_pesquisa(db=db, pesquisa_id=pesquisa_id)
    setor_atualizado = next((setor for setor in setores_raw if setor.id == db_setor.id), None)
    if setor_atualizado is None:
        raise HTTPException(status_code=404, detail="Setor nao encontrado.")
    progresso = crud.obter_progressos_setores(
        db, [setor_atualizado], pesquisa_id=pesquisa_id
    )[setor_atualizado.id]
    return setor_to_dict(setor_atualizado, db, progresso)

# Nome real da FK em `coletas.setor_id` (ON DELETE NO ACTION). O DELETE do
# setor so pode ser barrado por ela; qualquer outra violacao e outro problema.
FK_COLETAS_SETOR = "fk_coletas_setor_id_setores"

SETOR_COM_COLETAS_DETALHE = (
    "Este setor possui coletas vinculadas e não pode ser excluído."
)


def _constraint_violada(exc: IntegrityError) -> str | None:
    """Nome da constraint, quando o driver o expoe (`diag` do psycopg)."""
    orig = getattr(exc, "orig", None)
    nome = getattr(getattr(orig, "diag", None), "constraint_name", None)
    if nome:
        return nome
    # SQLite, por exemplo, diz apenas "FOREIGN KEY constraint failed": sem nome,
    # nao ha o que afirmar a partir do texto.
    if FK_COLETAS_SETOR in str(orig or exc):
        return FK_COLETAS_SETOR
    return None


def _conflito_coletas_do_setor(exc: IntegrityError) -> bool:
    """Reconhece SOMENTE a FK das coletas."""
    return _constraint_violada(exc) == FK_COLETAS_SETOR


@router.delete("/projetos/{projeto_id}/pesquisas/{pesquisa_id}/setores/{setor_id}", dependencies=[Depends(require_permissao(Permissao.PESQUISA_GERENCIAR))])
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

    # A checagem de historico vem DEPOIS da cadeia de tenant acima: para a
    # Empresa A, um setor da Empresa B nao existe, e responder 409 aqui
    # revelaria que ele existe -- e ainda que ele tem coletas.
    if crud.setor_possui_coletas(db, setor_id=db_setor.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=SETOR_COM_COLETAS_DETALHE,
        )

    try:
        db.delete(db_setor)
        db.commit()
    except IntegrityError as exc:
        # Janela de corrida: uma coleta pode ter sido vinculada entre o EXISTS
        # e o commit. A FK barra o DELETE e o historico fica intacto.
        db.rollback()

        constraint = _constraint_violada(exc)
        if constraint is None:
            # Driver que nao nomeia a constraint. Em vez de deduzir da mensagem,
            # PERGUNTAR ao banco: se ha coleta apontando para este setor agora,
            # o conflito esta provado pelos dados.
            conflito_de_coletas = crud.setor_possui_coletas(db, setor_id=setor_id)
        else:
            conflito_de_coletas = constraint == FK_COLETAS_SETOR

        if conflito_de_coletas:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=SETOR_COM_COLETAS_DETALHE,
            ) from exc
        # Qualquer outra constraint sobe como e: traduzi-la para "possui
        # coletas" seria inventar um diagnostico que nao foi observado.
        raise
    return {"message": "Setor excluído com sucesso"}


# --- Composicao eleitoral do Setor -------------------------------------------
# Rota propria em vez de inflar a listagem de setores: a listagem serve o app do
# agente e o mapa de setores, que nao precisam da composicao a cada chamada.


def _setor_territorio_item(territorio: models.TerritorioEleitoral) -> dict:
    return {
        "id": territorio.id,
        "nome": territorio.nome,
        "municipio_id": territorio.municipio_id,
        "eleitorado_apto": territorio.eleitorado_apto,
    }


@router.get(
    "/projetos/{projeto_id}/pesquisas/{pesquisa_id}/setores/{setor_id}/territorios",
    response_model=List[schemas.SetorTerritorioItem], dependencies=[Depends(require_permissao(Permissao.PESQUISA_VER))])
def listar_territorios_do_setor(
    projeto_id: int,
    pesquisa_id: int,
    setor_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user),
):
    """Composicao eleitoral atual do Setor. Setor de outro tenant responde 404."""
    setor = setor_territorio.obter_setor(db, projeto_id, pesquisa_id, setor_id, current_user)
    return [
        _setor_territorio_item(territorio)
        for territorio in setor_territorio.listar_territorios(db, setor.id)
    ]


@router.get(
    "/projetos/{projeto_id}/pesquisas/{pesquisa_id}/setores/{setor_id}/universo-eleitoral",
    response_model=schemas.UniversoEleitoralSetorResponse, dependencies=[Depends(require_permissao(Permissao.PESQUISA_VER))])
def obter_universo_eleitoral_do_setor(
    projeto_id: int,
    pesquisa_id: int,
    setor_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user),
):
    """Eleitores aptos que compoem o Setor na Base principal ATUAL do projeto.

    Rota propria em vez de engordar a listagem de setores, que serve o app do
    agente e o mapa e nao precisa deste calculo a cada chamada.

    Derivado em leitura; nada e persistido. Indisponibilidade vem com motivo
    explicito, nunca como zero.
    """
    return setor_territorio.obter_universo_eleitoral_setor(
        db, projeto_id, pesquisa_id, setor_id, current_user
    )


@router.put(
    "/projetos/{projeto_id}/pesquisas/{pesquisa_id}/setores/{setor_id}/territorios",
    response_model=List[schemas.SetorTerritorioItem], dependencies=[Depends(require_permissao(Permissao.TERRITORIO_GERENCIAR))])
def definir_territorios_do_setor(
    projeto_id: int,
    pesquisa_id: int,
    setor_id: int,
    payload: schemas.SetorTerritoriosRequest,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user),
):
    """Substitui integralmente a composicao eleitoral do Setor.

    Idempotente e transacional: o conjunto enviado passa a ser a composicao, e
    qualquer recusa deixa a composicao anterior intacta. Bairro ja usado por
    outro setor analitico da mesma pesquisa responde 409.
    """
    territorios = setor_territorio.definir_territorios(
        db,
        projeto_id,
        pesquisa_id,
        setor_id,
        payload.territorio_eleitoral_ids,
        current_user,
    )
    return [_setor_territorio_item(territorio) for territorio in territorios]


@router.get("/projetos/{projeto_id}/pesquisas/{pesquisa_id}/setores", dependencies=[Depends(require_permissao(Permissao.PESQUISA_VER))])
def list_setores_by_projeto_pesquisa(
    projeto_id: int,
    pesquisa_id: int,
    finalidade: schemas.FinalidadeSetor | None = None,
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
    setores_raw = crud.get_setores_by_pesquisa(
        db=db,
        pesquisa_id=pesquisa_id,
        finalidade=finalidade,
    )
    progressos = crud.obter_progressos_setores(
        db, setores_raw, pesquisa_id=pesquisa_id
    )
    municipios = pergunta_territorio.resolver_municipios_setores(db, [s.id for s in setores_raw])

    # 4. Processa retorno
    return [setor_to_dict(s, db, progressos[s.id], municipios.get(s.id)) for s in setores_raw]
