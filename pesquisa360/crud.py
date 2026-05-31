# pesquisa360/crud.py

import json
import pandas as pd
import numpy as np
from fastapi import HTTPException
from typing import List, Optional
from .utils import geocoding
from geopy.geocoders import Nominatim
from sqlalchemy.orm import Session, aliased, joinedload 
from sqlalchemy import func, Text, and_
from sqlalchemy.sql import text, bindparam
from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from .db import models
from . import schemas
from .core import security

# ==============================================================================
# USUÁRIOS (Gestão de Acesso)
# ==============================================================================

def get_user_by_email(db: Session, email: str):
    """
    Busca usuário por email para o Login.
    Não filtra por empresa aqui, pois o login é a porta de entrada.
    """
    return db.query(models.Usuario).filter(models.Usuario.email == email).first()

def get_user_by_id(db: Session, usuario_id: int):
    return db.query(models.Usuario).filter(models.Usuario.id == usuario_id).first()

def create_user(db: Session, user: schemas.UsuarioCreate, current_user: models.Usuario):
    """
    Cria um novo usuário VINCULADO à empresa do administrador logado.
    """
    hashed_password = security.get_password_hash(user.senha)
    db_user = models.Usuario(
        email=user.email, 
        nome=user.nome,
        senha_hash=hashed_password, 
        perfil_id=user.perfil_id,
        company_id=current_user.company_id  # <--- VÍNCULO AUTOMÁTICO DE EMPRESA
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def get_users(db: Session, current_user: models.Usuario, skip: int = 0, limit: int = 100):
    """
    Retorna apenas usuários da MESMA EMPRESA que o solicitante.
    """
    return db.query(models.Usuario)\
             .filter(models.Usuario.company_id == current_user.company_id)\
             .offset(skip).limit(limit).all()

def get_admin_users(
    db: Session,
    company_id: Optional[int] = None,
    skip: int = 0,
    limit: int = 100,
):
    query = db.query(models.Usuario)
    if company_id is not None:
        query = query.filter(models.Usuario.company_id == company_id)
    return query.offset(skip).limit(limit).all()

def get_perfil_by_name(db: Session, nome: str):
    """Busca um perfil pelo nome (ex: 'Agente')."""
    return db.query(models.Perfil).filter(models.Perfil.nome == nome).first()

def get_perfil(db: Session, perfil_id: int):
    return db.query(models.Perfil).filter(models.Perfil.id == perfil_id).first()

def create_perfil(db: Session, perfil: schemas.PerfilCreate):
    """Cria um novo perfil no banco."""
    db_perfil = models.Perfil(nome=perfil.nome)
    # Se o modelo Perfil tiver descrição ou outros campos, adicione aqui
    db.add(db_perfil)
    db.commit()
    db.refresh(db_perfil)
    return db_perfil

def create_admin_user(db: Session, user: schemas.UsuarioAdminCreate):
    hashed_password = security.get_password_hash(user.senha)
    db_user = models.Usuario(
        email=user.email,
        nome=user.nome,
        senha_hash=hashed_password,
        perfil_id=user.perfil_id,
        ativo=user.ativo if user.ativo is not None else True,
        company_id=user.company_id,
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def update_admin_user(
    db: Session,
    db_user: models.Usuario,
    user_update: schemas.UsuarioAdminUpdate,
):
    update_data = user_update.model_dump(exclude_unset=True)
    senha = update_data.pop("senha", None)

    for field, value in update_data.items():
        setattr(db_user, field, value)

    if senha:
        db_user.senha_hash = security.get_password_hash(senha)

    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

# ==============================================================================
# EMPRESAS / TENANTS (Admin SaaS)
# ==============================================================================

def get_companies(db: Session, skip: int = 0, limit: int = 100):
    return db.query(models.Company).offset(skip).limit(limit).all()

def get_company(db: Session, company_id: int):
    return db.query(models.Company).filter(models.Company.id == company_id).first()

def create_company(db: Session, company: schemas.CompanyCreate):
    db_company = models.Company(**company.model_dump())
    db.add(db_company)
    db.commit()
    db.refresh(db_company)
    return db_company

def update_company(
    db: Session,
    db_company: models.Company,
    company_update: schemas.CompanyUpdate,
):
    update_data = company_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_company, field, value)

    db.add(db_company)
    db.commit()
    db.refresh(db_company)
    return db_company

# ==============================================================================
# PROJETOS (Multitenancy)
# ==============================================================================

def get_projetos(db: Session, current_user: models.Usuario, skip: int = 0, limit: int = 100):
    """Lista apenas projetos da empresa do usuário."""
    return db.query(models.Projeto)\
             .filter(models.Projeto.company_id == current_user.company_id)\
             .offset(skip).limit(limit).all()

def create_projeto(db: Session, projeto: schemas.ProjetoCreate, current_user: models.Usuario):
    # O backend assume o controlo: pega nos dados do frontend e injeta o company_id real
    
    # Nota: se estiver a usar uma versão antiga do Pydantic, use projeto.dict() em vez de model_dump()
    db_projeto = models.Projeto(
        **projeto.model_dump(), 
        company_id=current_user.company_id
    )
    
    db.add(db_projeto)
    db.commit()
    db.refresh(db_projeto)
    return db_projeto

def get_projeto(db: Session, projeto_id: int, current_user: models.Usuario):
    """Busca um projeto específico validando a empresa."""
    return db.query(models.Projeto).filter(
        models.Projeto.id == projeto_id,
        models.Projeto.company_id == current_user.company_id
    ).first()

# ==============================================================================
# PESQUISAS E PERGUNTAS
# ==============================================================================

def get_pesquisas(db: Session, projeto_id: int, current_user: models.Usuario):
    """
    Lista pesquisas. Valida se o projeto pertence à empresa do usuário antes.
    """
    # 1. Validação de Segurança
    projeto = get_projeto(db, projeto_id, current_user)
    if not projeto:
        return [] # Ou poderia lançar exceção, mas lista vazia é seguro
        
    return db.query(models.Pesquisa).filter(models.Pesquisa.projeto_id == projeto_id).all()

def get_pesquisa(db: Session, pesquisa_id: int, current_user: models.Usuario):
    """
    Busca uma pesquisa específica validando se pertence à empresa do usuário.
    Retorna a pesquisa se o usuário tiver acesso, None caso contrário.
    """
    return db.query(models.Pesquisa).join(models.Projeto).filter(
        models.Pesquisa.id == pesquisa_id,
        models.Projeto.company_id == current_user.company_id
    ).first()

def create_pesquisa(db: Session, pesquisa: schemas.PesquisaCreate, projeto_id: int, current_user: models.Usuario):
    # 1. Validação de Segurança Correta: Verifica se o projeto existe e pertence à empresa do usuário
    projeto = db.query(models.Projeto).filter(
        models.Projeto.id == projeto_id,
        models.Projeto.company_id == current_user.company_id
    ).first()
    
    if not projeto:
        raise Exception("Projeto não encontrado ou você não tem permissão para adicionar pesquisas a ele.")

    # 2. Criação da Pesquisa (SEM o company_id, pois a tabela não tem esse campo)
    db_pesquisa = models.Pesquisa(
        **pesquisa.model_dump(), # ou .dict()
        projeto_id=projeto_id
    )
    
    db.add(db_pesquisa)
    db.commit()
    db.refresh(db_pesquisa)
    return db_pesquisa

def create_pergunta(db: Session, pergunta: schemas.PerguntaCreate, pesquisa_id: int):
    
    # 1. Extrair as opções do payload
    pergunta_data = pergunta.dict(exclude_unset=True)
    opcoes_data = pergunta_data.pop('opcoes', []) 
    if pergunta_data.get("ordem") is None:
        ultima_ordem = db.query(func.max(models.Pergunta.ordem)).filter(
            models.Pergunta.pesquisa_id == pesquisa_id,
            models.Pergunta.ativo.is_(True)
        ).scalar()
        pergunta_data["ordem"] = (ultima_ordem or 0) + 1

    # 2. Criar a instância principal da Pergunta
    db_pergunta = models.Pergunta(**pergunta_data, pesquisa_id=pesquisa_id)
    db.add(db_pergunta)
    db.commit()
    db.refresh(db_pergunta)

    # 3. Se houver opções, mapeá-las para models.Opcao e associá-las
    if opcoes_data:
        for opt in opcoes_data:
            nova_opcao = models.Opcao(**opt, pergunta_id=db_pergunta.id)
            db.add(nova_opcao)
        
        db.commit()
        db.refresh(db_pergunta)

    return db_pergunta

def get_perguntas(db: Session, pesquisa_id: int):
    return db.query(models.Pergunta)\
             .filter(
                 models.Pergunta.pesquisa_id == pesquisa_id,
                 models.Pergunta.ativo.is_(True)
             )\
             .order_by(models.Pergunta.ordem, models.Pergunta.id)\
             .all()

def update_pergunta(db: Session, pergunta_id: int, pergunta_in: schemas.PerguntaUpdate):
    # 1. Busca a pergunta existente
    db_pergunta = db.query(models.Pergunta).filter(models.Pergunta.id == pergunta_id).first()
    if not db_pergunta:
        return None

    # 2. Transforma os dados que vieram do React em dicionário
    update_data = pergunta_in.dict(exclude_unset=True)
    update_data.pop("ordem", None)

    # 3. EXTRAI as opções para não quebrar o banco (Igual fizemos no Create)
    opcoes_data = None
    if 'opcoes' in update_data:
        opcoes_data = update_data.pop('opcoes')

    # 4. Atualiza apenas os dados de texto, tipo e obrigatoriedade
    for key, value in update_data.items():
        setattr(db_pergunta, key, value)

    db.commit()
    db.refresh(db_pergunta)

    # 5. Atualiza as opções (Estratégia segura: apaga as antigas e recria as novas)
    if opcoes_data is not None:
        # Deleta as opções vinculadas a esta pergunta
        db.query(models.Opcao).filter(models.Opcao.pergunta_id == pergunta_id).delete()
        
        # Cria as novas opções que vieram da edição
        # Cria as novas opções que vieram da edição
        for opt in opcoes_data:
            opt_copy = opt.copy()
            opt_copy.pop('id', None) # Já tínhamos feito isso
            opt_copy.pop('pergunta_id', None) # <--- ADICIONE ESTA LINHA PARA SALVAR O DIA
            
            # Agora ele desempacota limpo e adiciona o pergunta_id apenas 1 vez
            nova_opcao = models.Opcao(**opt_copy, pergunta_id=db_pergunta.id)
            db.add(nova_opcao)
            
        db.commit()
        db.refresh(db_pergunta)

    return db_pergunta

def reordenar_perguntas(db: Session, pesquisa_id: int, itens: List[schemas.PerguntaReordenarItem]):
    perguntas = get_perguntas(db, pesquisa_id=pesquisa_id)
    perguntas_por_id = {pergunta.id: pergunta for pergunta in perguntas}
    ids_enviados = [item.id for item in itens]
    ids_invalidos = [pergunta_id for pergunta_id in ids_enviados if pergunta_id not in perguntas_por_id]

    if ids_invalidos:
        raise HTTPException(
            status_code=400,
            detail="Todas as perguntas devem pertencer à pesquisa informada."
        )

    nova_ordem = [pergunta for pergunta in perguntas if pergunta.id not in ids_enviados]
    itens_ordenados = sorted(enumerate(itens), key=lambda item: (item[1].ordem, item[0]))

    for _, item in itens_ordenados:
        pergunta = perguntas_por_id[item.id]
        posicao = min(item.ordem - 1, len(nova_ordem))
        nova_ordem.insert(posicao, pergunta)

    try:
        for ordem, pergunta in enumerate(nova_ordem, start=1):
            pergunta.ordem = ordem
        db.commit()
    except Exception:
        db.rollback()
        raise

    return get_perguntas(db, pesquisa_id=pesquisa_id)

# ==============================================================================
# SOFT DELETE
# ==============================================================================

def delete_pergunta(db: Session, *, db_obj: models.Pergunta) -> models.Pergunta:
    # REGRA: Verifica se existem respostas para esta pergunta
    tem_respostas = db.query(models.Resposta).filter(models.Resposta.pergunta_id == db_obj.id).first()
    if tem_respostas:
        raise HTTPException(status_code=400, detail="Não é possível excluir: esta pergunta já possui respostas registradas.")
    
    db_obj.ativo = False
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

def delete_pesquisa(db: Session, *, db_obj: models.Pesquisa) -> models.Pesquisa:
    # REGRA: Verifica se existem coletas para esta pesquisa
    tem_coletas = db.query(models.Coleta).filter(models.Coleta.pesquisa_id == db_obj.id).first()
    if tem_coletas:
        raise HTTPException(status_code=400, detail="Não é possível excluir: esta pesquisa já possui dados de campo coletados.")
    
    db_obj.ativo = False
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

def delete_projeto(db: Session, *, db_obj: models.Projeto) -> models.Projeto:
    # REGRA: Verifica se QUALQUER pesquisa deste projeto tem coletas
    pesquisas_do_projeto = db.query(models.Pesquisa).filter(models.Pesquisa.projeto_id == db_obj.id).all()
    for p in pesquisas_do_projeto:
        tem_coletas = db.query(models.Coleta).filter(models.Coleta.pesquisa_id == p.id).first()
        if tem_coletas:
            raise HTTPException(status_code=400, detail=f"Não é possível excluir o projeto: a pesquisa '{p.titulo}' já possui coletas.")

    db_obj.status = "Excluído"
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

# ==============================================================================
# COLETAS E RESPOSTAS
# ==============================================================================

def create_coleta(db: Session, coleta_in: schemas.ColetaCreate, pesquisa_id: int, agente_id: int):
    # Coletas vêm do App Mobile. A validação de empresa geralmente é feita
    # garantindo que o Agente só baixou pesquisas da empresa dele.
    
    # Converte lat/lon para GeoAlchemy Element
    ponto_inicio = None
    if coleta_in.localizacao_inicio:
        ponto_inicio = from_shape(Point(coleta_in.localizacao_inicio.lon, coleta_in.localizacao_inicio.lat), srid=4326)
        
    ponto_fim = None
    if coleta_in.localizacao_fim:
        ponto_fim = from_shape(Point(coleta_in.localizacao_fim.lon, coleta_in.localizacao_fim.lat), srid=4326)

    ponto_endereco = coleta_in.localizacao_fim or coleta_in.localizacao_inicio
    endereco_estimado = None
    if ponto_endereco:
        try:
            endereco_estimado = geocoding.obter_endereco_por_coords(
                ponto_endereco.lat,
                ponto_endereco.lon
            )
        except Exception:
            endereco_estimado = "Endereço não identificado"

    db_coleta = models.Coleta(
        data_inicio_coleta=coleta_in.data_inicio_coleta,
        data_fim_coleta=coleta_in.data_fim_coleta,
        localizacao_inicio=ponto_inicio,
        localizacao_fim=ponto_fim,
        endereco_estimado=endereco_estimado,
        pesquisa_id=pesquisa_id,
        agente_id=agente_id,
        foi_offline=coleta_in.foi_offline,
        status_sincronizacao="sincronizado"  # Sempre "sincronizado" quando chega via POST
    )
    db.add(db_coleta)
    db.commit()
    db.refresh(db_coleta)
    
    # Salvar Respostas
    for resp in coleta_in.respostas:
        db_resposta = models.Resposta(
            coleta_id=db_coleta.id,
            pergunta_id=resp.pergunta_id,
            valor_resposta=resp.valor_resposta
        )
        db.add(db_resposta)
    
    db.commit()
    return db_coleta

# ==============================================================================
# SETORES E MISSÕES
# ==============================================================================

def create_setor(db: Session, setor_in: schemas.SetorCreate, pesquisa_id: int, current_user: models.Usuario):
    # Valida acesso à pesquisa através do projeto
    # (Juntando tabelas para validar empresa numa query só)
    pesquisa_valida = db.query(models.Pesquisa).join(models.Projeto)\
        .filter(models.Pesquisa.id == pesquisa_id, models.Projeto.company_id == current_user.company_id)\
        .first()
        
    if not pesquisa_valida:
        raise Exception("Acesso negado à pesquisa.")

    # Monta o WKT do Polígono
    coords = setor_in.geometria_coords
    if coords[0] != coords[-1]:
        coords.append(coords[0])
        
    coords_str = ", ".join([f"{p[1]} {p[0]}" for p in coords]) # PostGIS usa Lon Lat
    wkt = f"POLYGON(({coords_str}))"

    db_setor = models.Setor(
        nome=setor_in.nome,
        meta=setor_in.meta,
        pesquisa_id=pesquisa_id,
        agente_id=setor_in.agente_id,
        tolerancia=setor_in.tolerancia,
        geometria=func.ST_GeomFromText(wkt, 4326)
    )
    db.add(db_setor)
    db.commit()
    db.refresh(db_setor)
    return db_setor

def get_setores_by_pesquisa(db: Session, pesquisa_id: int):
    # Retorna GeoJSON
    return db.query(
        models.Setor.id,
        models.Setor.nome,
        models.Setor.meta,
        models.Setor.tolerancia,
        func.ST_AsGeoJSON(models.Setor.geometria).label("geojson")
    ).filter(models.Setor.pesquisa_id == pesquisa_id).all()

# ==============================================================================
# DASHBOARD E RELATÓRIOS (Com Filtro de Empresa)
# ==============================================================================

def get_coletas_monitoramento(db: Session, pesquisa_id: int, current_user: models.Usuario):
    """
    Retorna pontos para o mapa. Valida a empresa.
    """
    # Validação
    pesquisa_valida = db.query(models.Pesquisa).join(models.Projeto)\
        .filter(models.Pesquisa.id == pesquisa_id, models.Projeto.company_id == current_user.company_id)\
        .first()
    if not pesquisa_valida:
        return []

    return db.query(
        models.Coleta.id,
        models.Coleta.data_inicio,
        func.ST_AsGeoJSON(models.Coleta.localizacao_inicio).label("geojson_inicio"),
        models.Usuario.nome.label("agente_nome")
    ).join(models.Usuario, models.Coleta.agente_id == models.Usuario.id)\
     .filter(models.Coleta.pesquisa_id == pesquisa_id).all()

def get_dashboard_stats(db: Session, pesquisa_id: int, current_user: models.Usuario):
    """Retorna estatísticas simples validando a empresa."""
    # Validação rápida
    pesquisa_valida = db.query(models.Pesquisa).join(models.Projeto)\
        .filter(models.Pesquisa.id == pesquisa_id, models.Projeto.company_id == current_user.company_id)\
        .first()
    if not pesquisa_valida:
        return {"total_coletas": 0, "coletas_hoje": 0}

    total_coletas = db.query(func.count(models.Coleta.id))\
        .filter(models.Coleta.pesquisa_id == pesquisa_id).scalar()
        
    return {
        "total_coletas": total_coletas,
        # Adicione mais stats conforme necessário
    }

def _validar_pesquisa_relatorio(db: Session, pesquisa_id: int, current_user: models.Usuario):
    pesquisa = db.query(models.Pesquisa).join(models.Projeto).filter(
        models.Pesquisa.id == pesquisa_id,
        models.Projeto.company_id == current_user.company_id
    ).first()
    if not pesquisa:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada.")
    return pesquisa

def _validar_setores_relatorio(db: Session, pesquisa_id: int, setor_ids: Optional[List[int]], current_user: models.Usuario):
    if not setor_ids:
        return

    ids_unicos = set(setor_ids)
    setores = db.query(models.Setor.id).join(models.Pesquisa).join(models.Projeto).filter(
        models.Setor.id.in_(ids_unicos),
        models.Setor.pesquisa_id == pesquisa_id,
        models.Projeto.company_id == current_user.company_id
    ).all()
    ids_validos = {setor.id for setor in setores}

    if ids_validos != ids_unicos:
        raise HTTPException(status_code=404, detail="Setor não encontrado para esta pesquisa.")

def _aplicar_filtros_coletas(query, db: Session, current_user: models.Usuario, agente_ids=None, setor_ids=None):
    if agente_ids:
        query = query.filter(models.Coleta.agente_id.in_(agente_ids))

    if setor_ids:
        setor_match = db.query(models.Setor.id).join(models.Pesquisa).join(models.Projeto).filter(
            models.Setor.id.in_(setor_ids),
            models.Setor.pesquisa_id == models.Coleta.pesquisa_id,
            models.Projeto.company_id == current_user.company_id,
            models.Setor.geometria.isnot(None),
            models.Coleta.localizacao_inicio.isnot(None),
            func.ST_Intersects(models.Setor.geometria, models.Coleta.localizacao_inicio)
        ).exists()
        query = query.filter(setor_match)

    return query

def get_relatorio_filtros(db: Session, pesquisa_id: int, current_user: models.Usuario):
    _validar_pesquisa_relatorio(db, pesquisa_id, current_user)

    agentes_rows = db.query(
        models.Usuario.id,
        models.Usuario.nome,
        func.count(models.Coleta.id).label("total_coletas")
    )\
    .join(models.Coleta, models.Coleta.agente_id == models.Usuario.id)\
    .filter(
        models.Coleta.pesquisa_id == pesquisa_id,
        models.Usuario.company_id == current_user.company_id
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

def get_relatorio_pesquisa(
    db: Session,
    pesquisa_id: int,
    current_user: models.Usuario,
    agente_ids: Optional[List[int]] = None,
    setor_ids: Optional[List[int]] = None
):
    """
    Gera o relatÃ³rio simples de frequÃªncia por pergunta.
    A validaÃ§Ã£o multitenant Ã© feita no endpoint antes desta chamada.
    """
    pesquisa = _validar_pesquisa_relatorio(db, pesquisa_id, current_user)
    _validar_setores_relatorio(db, pesquisa_id, setor_ids, current_user)

    perguntas = db.query(models.Pergunta)\
        .filter(
            models.Pergunta.pesquisa_id == pesquisa_id,
            models.Pergunta.ativo.is_(True)
        )\
        .order_by(models.Pergunta.ordem, models.Pergunta.id)\
        .all()

    total_query = db.query(func.count(models.Coleta.id))\
        .filter(models.Coleta.pesquisa_id == pesquisa_id)
    total_coletas = _aplicar_filtros_coletas(
        total_query, db, current_user, agente_ids, setor_ids
    ).scalar() or 0

    resultados = []
    for pergunta in perguntas:
        rows_query = db.query(
            models.Resposta.valor_resposta,
            func.count(models.Resposta.id).label("contagem")
        )\
            .join(models.Coleta, models.Coleta.id == models.Resposta.coleta_id)\
            .filter(
                models.Coleta.pesquisa_id == pesquisa_id,
                models.Resposta.pergunta_id == pergunta.id
            )
        rows = _aplicar_filtros_coletas(
            rows_query, db, current_user, agente_ids, setor_ids
        )\
        .group_by(models.Resposta.valor_resposta)\
        .order_by(models.Resposta.valor_resposta)\
        .all()

        total_pergunta = sum(int(contagem or 0) for _, contagem in rows)
        dados = []
        opcoes_resposta = {}
        resultados_opcoes = []

        for valor_resposta, contagem in rows:
            valor = "" if valor_resposta is None else str(valor_resposta)
            quantidade = int(contagem or 0)
            percentual = round((quantidade / total_pergunta) * 100, 2) if total_pergunta else 0.0

            opcoes_resposta[valor] = quantidade
            dados.append({
                "valor_resposta": valor,
                "contagem": quantidade,
                "percentual": percentual,
            })
            resultados_opcoes.append({
                "opcao": valor,
                "contagem": quantidade,
                "percentual": percentual,
            })

        resultados.append({
            "pergunta_id": pergunta.id,
            "texto_pergunta": pergunta.texto_pergunta,
            "tipo_pergunta": pergunta.tipo_pergunta,
            "total": total_pergunta,
            "opcoes_resposta": opcoes_resposta,
            "dados": dados,
            "resultados": resultados_opcoes,
        })

    return {
        "pesquisa_id": pesquisa.id,
        "titulo_pesquisa": pesquisa.titulo,
        "total_coletas": int(total_coletas),
        "resultados": resultados,
        "resultados_por_pergunta": resultados,
    }

def get_report_crosstab(
    db: Session,
    pesquisa_id: int,
    pergunta_linha_id: int,
    pergunta_coluna_id: int,
    current_user: models.Usuario,
    agente_ids: Optional[List[int]] = None,
    setor_ids: Optional[List[int]] = None
):
    """
    Gera dados para tabulação cruzada (Crosstab).
    Valida se a pesquisa pertence à empresa do usuário.
    """
    _validar_pesquisa_relatorio(db, pesquisa_id, current_user)
    _validar_setores_relatorio(db, pesquisa_id, setor_ids, current_user)

    resposta_linha = aliased(models.Resposta)
    resposta_coluna = aliased(models.Resposta)

    query = db.query(
        resposta_linha.valor_resposta.label("linha"),
        resposta_coluna.valor_resposta.label("coluna"),
        func.count().label("total")
    ).select_from(models.Coleta)\
        .join(resposta_linha, models.Coleta.id == resposta_linha.coleta_id)\
        .join(resposta_coluna, models.Coleta.id == resposta_coluna.coleta_id)\
        .filter(
            models.Coleta.pesquisa_id == pesquisa_id,
            resposta_linha.pergunta_id == pergunta_linha_id,
            resposta_coluna.pergunta_id == pergunta_coluna_id
        )

    result = _aplicar_filtros_coletas(
        query, db, current_user, agente_ids, setor_ids
    )\
    .group_by(resposta_linha.valor_resposta, resposta_coluna.valor_resposta)\
    .order_by(resposta_linha.valor_resposta, resposta_coluna.valor_resposta)\
    .all()
    
    # Formata para JSON amigável ao Frontend
    data = []
    for row in result:
        data.append({
            "linha": row.linha,
            "coluna": row.coluna,
            "valor": row.total
        })
        
    return data
