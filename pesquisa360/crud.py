# pesquisa360/crud.py

import json
from collections import defaultdict
from math import ceil
import pandas as pd
import numpy as np
from fastapi import HTTPException
from typing import List, Optional
from .utils import geocoding
from geopy.geocoders import Nominatim
from sqlalchemy.orm import Session, aliased, joinedload 
from sqlalchemy import func, Text, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import text, bindparam
from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from .db import models
from . import schemas
from .core import security
from .utils.response_normalization import normalizar_resposta_espontanea

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

def create_user(
    db: Session,
    user: schemas.UsuarioCreate,
    current_user: models.Usuario,
    company_id: Optional[int] = None,
):
    """
    Cria um novo usuário VINCULADO à empresa do administrador logado.
    """
    hashed_password = security.get_password_hash(user.senha)
    db_user = models.Usuario(
        email=user.email, 
        nome=user.nome,
        senha_hash=hashed_password, 
        perfil_id=user.perfil_id,
        company_id=company_id if company_id is not None else current_user.company_id
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
    perfil_id = user.perfil_id
    hashed_password = security.get_password_hash(user.senha)
    db_user = models.Usuario(
        email=user.email,
        nome=user.nome,
        senha_hash=hashed_password,
        perfil_id=perfil_id,
        ativo=user.ativo if user.ativo is not None else True,
        company_id=user.company_id,
    )
    db.add(db_user)
    db.flush()
    if db_user.perfil_id != perfil_id:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Perfil informado nao foi persistido corretamente.",
        )
    db.commit()
    db.refresh(db_user)
    return db_user

def update_admin_user(
    db: Session,
    db_user: models.Usuario,
    user_update: schemas.UsuarioAdminUpdate,
):
    perfil_id = user_update.perfil_id
    update_data = user_update.model_dump(exclude_unset=True)
    senha = update_data.pop("senha", None)

    for field, value in update_data.items():
        setattr(db_user, field, value)

    if senha:
        db_user.senha_hash = security.get_password_hash(senha)

    db.add(db_user)
    db.flush()
    if perfil_id is not None and db_user.perfil_id != perfil_id:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Perfil informado nao foi persistido corretamente.",
        )
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

def get_company_by_cnpj(db: Session, cnpj: str, exclude_company_id: Optional[int] = None):
    query = db.query(models.Company).filter(models.Company.cnpj == cnpj)
    if exclude_company_id is not None:
        query = query.filter(models.Company.id != exclude_company_id)
    return query.first()

def create_company(db: Session, company: schemas.CompanyCreate):
    if company.cnpj and get_company_by_cnpj(db, company.cnpj):
        raise HTTPException(status_code=409, detail="CNPJ ja cadastrado.")
    db_company = models.Company(**company.model_dump())
    db.add(db_company)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="CNPJ ja cadastrado.") from exc
    db.refresh(db_company)
    return db_company

def update_company(
    db: Session,
    db_company: models.Company,
    company_update: schemas.CompanyUpdate,
):
    update_data = company_update.model_dump(exclude_unset=True)
    cnpj = update_data.get("cnpj")
    if cnpj and get_company_by_cnpj(db, cnpj, exclude_company_id=db_company.id):
        raise HTTPException(status_code=409, detail="CNPJ ja cadastrado.")
    for field, value in update_data.items():
        setattr(db_company, field, value)

    db.add(db_company)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="CNPJ ja cadastrado.") from exc
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

def validate_project_coordinator(
    db: Session,
    coordenador_id: int,
    company_id: int,
) -> models.Usuario:
    coordenador = db.query(models.Usuario).filter(
        models.Usuario.id == coordenador_id,
        models.Usuario.company_id == company_id,
    ).first()
    if not coordenador:
        raise HTTPException(status_code=404, detail="Coordenador nao encontrado.")
    if coordenador.ativo is not True:
        raise HTTPException(status_code=400, detail="Coordenador deve estar ativo.")

    perfil_nome = (
        getattr(getattr(coordenador, "perfil", None), "nome", None) or ""
    ).strip().casefold()
    if perfil_nome not in {"gerente", "superadmin"}:
        raise HTTPException(status_code=400, detail="Perfil nao permitido para coordenacao.")
    return coordenador


def create_projeto(
    db: Session,
    projeto: schemas.ProjetoCreate,
    current_user: models.Usuario,
    company_id: Optional[int] = None,
):
    project_company_id = company_id if company_id is not None else current_user.company_id
    validate_project_coordinator(
        db=db,
        coordenador_id=projeto.coordenador_id,
        company_id=project_company_id,
    )
    projeto_data = projeto.model_dump(exclude={"company_id"})
    db_projeto = models.Projeto(
        **projeto_data,
        company_id=project_company_id,
    )
    
    db.add(db_projeto)
    db.commit()
    db.refresh(db_projeto)
    return db_projeto

def get_projeto(
    db: Session,
    projeto_id: int,
    current_user: models.Usuario,
    allow_global: bool = False,
):
    """Busca um projeto específico validando a empresa."""
    query = db.query(models.Projeto).filter(models.Projeto.id == projeto_id)
    if not allow_global:
        query = query.filter(models.Projeto.company_id == current_user.company_id)
    return query.first()


def update_projeto(
    db: Session,
    db_projeto: models.Projeto,
    projeto_update: schemas.ProjetoUpdate,
) -> models.Projeto:
    update_data = projeto_update.model_dump(exclude_unset=True)
    update_data.pop("company_id", None)

    coordenador_id = update_data.get("coordenador_id")
    if coordenador_id is not None:
        validate_project_coordinator(
            db=db,
            coordenador_id=coordenador_id,
            company_id=db_projeto.company_id,
        )

    for key, value in update_data.items():
        setattr(db_projeto, key, value)

    db.commit()
    db.refresh(db_projeto)
    return db_projeto

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
    pergunta_data = pergunta.model_dump(exclude_unset=True)
    opcoes_data = _validate_question_options(
        db,
        pesquisa_id=pesquisa_id,
        pergunta_id=None,
        opcoes=pergunta_data.pop("opcoes", []) or [],
    )
    if pergunta_data.get("ordem") is None:
        ultima_ordem = db.query(func.max(models.Pergunta.ordem)).filter(
            models.Pergunta.pesquisa_id == pesquisa_id,
            models.Pergunta.ativo.is_(True)
        ).scalar()
        pergunta_data["ordem"] = (ultima_ordem or 0) + 1

    db_pergunta = models.Pergunta(**pergunta_data, pesquisa_id=pesquisa_id)
    try:
        db.add(db_pergunta)
        db.flush()

        for opt in opcoes_data:
            nova_opcao = models.Opcao(**opt, pergunta_id=db_pergunta.id)
            db.add(nova_opcao)
            db.flush()

        db.commit()
        db.refresh(db_pergunta)
    except Exception:
        db.rollback()
        raise

    return db_pergunta

def get_perguntas(db: Session, pesquisa_id: int):
    return db.query(models.Pergunta)\
             .filter(
                 models.Pergunta.pesquisa_id == pesquisa_id,
                 models.Pergunta.ativo.is_(True)
             )\
             .order_by(models.Pergunta.ordem, models.Pergunta.id)\
             .all()

def get_pergunta(db: Session, pesquisa_id: int, pergunta_id: int):
    return db.query(models.Pergunta).filter(
        models.Pergunta.id == pergunta_id,
        models.Pergunta.pesquisa_id == pesquisa_id,
    ).first()


def _opcao_to_dict(opcao):
    if hasattr(opcao, "model_dump"):
        return opcao.model_dump()
    return opcao.copy()


def _normalize_option_text(texto) -> str:
    return " ".join(str(texto or "").split())


def _validate_question_options(
    db: Session,
    pesquisa_id: int,
    pergunta_id: Optional[int],
    opcoes,
) -> List[dict]:
    opcoes_data = [_opcao_to_dict(opcao) for opcao in opcoes or []]
    textos_normalizados = []
    opcao_ids = set()

    for opcao_data in opcoes_data:
        texto_normalizado = _normalize_option_text(opcao_data.get("texto"))
        if not texto_normalizado:
            raise HTTPException(status_code=422, detail="Texto da opcao e obrigatorio.")
        textos_normalizados.append(texto_normalizado.casefold())

        opcao_id = opcao_data.get("id")
        if opcao_id is not None:
            opcao_ids.add(opcao_id)

    if len(textos_normalizados) != len(set(textos_normalizados)):
        raise HTTPException(status_code=422, detail="Opcoes duplicadas no payload.")

    if opcao_ids:
        ids_validos = {
            opcao_id
            for opcao_id, in db.query(models.Opcao.id).filter(
                models.Opcao.id.in_(opcao_ids),
                models.Opcao.pergunta_id == pergunta_id,
            ).all()
        }
        if ids_validos != opcao_ids:
            raise HTTPException(status_code=404, detail="Opcao nao encontrada.")

    _validate_proximas_perguntas(db, pesquisa_id, opcoes_data)

    resultado = []
    for opcao_data in opcoes_data:
        opcao_limpa = opcao_data.copy()
        opcao_limpa.pop("id", None)
        opcao_limpa.pop("pergunta_id", None)
        opcao_limpa["texto"] = _normalize_option_text(opcao_limpa["texto"])
        resultado.append(opcao_limpa)
    return resultado


def _validate_proximas_perguntas(db: Session, pesquisa_id: int, opcoes) -> None:
    proximas_ids = {
        opcao_data.get("proxima_pergunta_id")
        for opcao_data in (_opcao_to_dict(opcao) for opcao in opcoes or [])
        if opcao_data.get("proxima_pergunta_id") is not None
    }
    if not proximas_ids:
        return

    ids_validos = {
        pergunta_id
        for pergunta_id, in db.query(models.Pergunta.id).filter(
            models.Pergunta.pesquisa_id == pesquisa_id,
            models.Pergunta.id.in_(proximas_ids),
        ).all()
    }
    if ids_validos != proximas_ids:
        raise HTTPException(status_code=404, detail="Pergunta não encontrada.")


def update_pergunta(db: Session, pesquisa_id: int, pergunta_id: int, pergunta_in: schemas.PerguntaUpdate):
    # 1. Busca a pergunta existente
    db_pergunta = get_pergunta(db, pesquisa_id=pesquisa_id, pergunta_id=pergunta_id)
    if not db_pergunta:
        return None

    # 2. Transforma os dados que vieram do React em dicionário
    update_data = pergunta_in.model_dump(exclude_unset=True)
    update_data.pop("ordem", None)

    # 3. EXTRAI as opções para não quebrar o banco (Igual fizemos no Create)
    opcoes_data = None
    if 'opcoes' in update_data:
        opcoes_data = _validate_question_options(
            db,
            pesquisa_id=pesquisa_id,
            pergunta_id=pergunta_id,
            opcoes=update_data.pop('opcoes') or [],
        )

    try:
        for key, value in update_data.items():
            setattr(db_pergunta, key, value)

        if opcoes_data is not None:
            db.query(models.Opcao).filter(
                models.Opcao.pergunta_id == pergunta_id
            ).delete(synchronize_session=False)

            for opt in opcoes_data:
                db.add(models.Opcao(**opt, pergunta_id=db_pergunta.id))
                db.flush()

        db.commit()
        db.refresh(db_pergunta)
    except Exception:
        db.rollback()
        raise

    return db_pergunta

def reordenar_perguntas(db: Session, pesquisa_id: int, itens: List[schemas.PerguntaReordenarItem]):
    perguntas = get_perguntas(db, pesquisa_id=pesquisa_id)
    perguntas_por_id = {pergunta.id: pergunta for pergunta in perguntas}
    ids_enviados = [item.id for item in itens]
    ids_invalidos = [pergunta_id for pergunta_id in ids_enviados if pergunta_id not in perguntas_por_id]

    if ids_invalidos:
        raise HTTPException(
            status_code=404,
            detail="Pergunta não encontrada."
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
# APURAÇÃO ESPONTÂNEA
# ==============================================================================

def _get_spontaneous_pesquisa(db: Session, pesquisa_id: int, current_user: models.Usuario):
    pesquisa = db.query(models.Pesquisa).join(models.Projeto).filter(
        models.Pesquisa.id == pesquisa_id,
        models.Projeto.company_id == current_user.company_id,
    ).first()
    if not pesquisa:
        raise HTTPException(status_code=404, detail="Pesquisa nao encontrada.")
    return pesquisa


def _get_spontaneous_category(
    db: Session,
    pesquisa_id: int,
    categoria_id: int,
    current_user: models.Usuario,
):
    return db.query(models.CategoriaRespostaEspontanea)\
        .join(models.Pesquisa, models.Pesquisa.id == models.CategoriaRespostaEspontanea.pesquisa_id)\
        .join(models.Projeto, models.Projeto.id == models.Pesquisa.projeto_id).filter(
        models.CategoriaRespostaEspontanea.id == categoria_id,
        models.CategoriaRespostaEspontanea.pesquisa_id == pesquisa_id,
        models.Projeto.company_id == current_user.company_id,
    ).first()


def _get_spontaneous_categories(db: Session, pesquisa_id: int, current_user: models.Usuario):
    return db.query(models.CategoriaRespostaEspontanea)\
        .join(models.Pesquisa, models.Pesquisa.id == models.CategoriaRespostaEspontanea.pesquisa_id)\
        .join(models.Projeto, models.Projeto.id == models.Pesquisa.projeto_id).filter(
        models.CategoriaRespostaEspontanea.pesquisa_id == pesquisa_id,
        models.Projeto.company_id == current_user.company_id,
        models.CategoriaRespostaEspontanea.ativo.is_(True),
    ).order_by(models.CategoriaRespostaEspontanea.nome_normalizado, models.CategoriaRespostaEspontanea.id).all()


def _get_spontaneous_mappings(db: Session, pesquisa_id: int, current_user: models.Usuario):
    return db.query(models.MapeamentoRespostaEspontanea)\
        .join(models.Pesquisa, models.Pesquisa.id == models.MapeamentoRespostaEspontanea.pesquisa_id)\
        .join(models.Projeto, models.Projeto.id == models.Pesquisa.projeto_id)\
        .join(models.CategoriaRespostaEspontanea, models.CategoriaRespostaEspontanea.id == models.MapeamentoRespostaEspontanea.categoria_id).filter(
        models.MapeamentoRespostaEspontanea.pesquisa_id == pesquisa_id,
        models.Projeto.company_id == current_user.company_id,
        models.MapeamentoRespostaEspontanea.ativo.is_(True),
        models.CategoriaRespostaEspontanea.ativo.is_(True),
    ).all()


def _get_spontaneous_raw_answers(
    db: Session,
    pesquisa_id: int,
    current_user: models.Usuario,
    pergunta_id: Optional[int] = None,
):
    query = db.query(
        models.Resposta.valor_resposta.label("valor_resposta"),
        models.Pergunta.id.label("pergunta_id"),
        models.Pergunta.texto_pergunta.label("texto_pergunta"),
        models.Pergunta.ordem.label("pergunta_ordem"),
    ).select_from(models.Resposta)\
        .join(models.Pergunta, models.Pergunta.id == models.Resposta.pergunta_id)\
        .join(models.Coleta, models.Coleta.id == models.Resposta.coleta_id)\
        .join(models.Pesquisa, models.Pesquisa.id == models.Coleta.pesquisa_id)\
        .join(models.Projeto, models.Projeto.id == models.Pesquisa.projeto_id)\
        .filter(
            models.Pesquisa.id == pesquisa_id,
            models.Projeto.company_id == current_user.company_id,
            models.Coleta.company_id == current_user.company_id,
            models.Pergunta.pesquisa_id == pesquisa_id,
            models.Pergunta.ativo.is_(True),
            models.Pergunta.eh_resposta_espontanea.is_(True),
        )
    if pergunta_id is not None:
        query = query.filter(models.Pergunta.id == pergunta_id)
    return query.all()


def _agrupar_respostas_espontaneas(
    raw_rows,
    categorias_por_id,
    mapeamentos_por_chave,
):
    grupos: dict[str, dict] = {}
    for row in raw_rows:
        chave = normalizar_resposta_espontanea(row.valor_resposta)
        if not chave:
            continue
        grupo = grupos.setdefault(
            chave,
            {
                "quantidade_total": 0,
                "variantes": defaultdict(int),
                "perguntas": {},
            },
        )
        grupo["quantidade_total"] += 1
        texto_original = "" if row.valor_resposta is None else str(row.valor_resposta).strip()
        grupo["variantes"][texto_original] += 1
        pergunta_id = int(row.pergunta_id)
        pergunta = grupo["perguntas"].setdefault(
            pergunta_id,
            {
                "pergunta_id": pergunta_id,
                "texto_pergunta": str(row.texto_pergunta),
                "ordem": int(row.pergunta_ordem) if row.pergunta_ordem is not None else 0,
                "quantidade": 0,
            },
        )
        pergunta["quantidade"] += 1

    items = []
    total_respostas = 0
    respostas_categorizadas = 0

    for chave, grupo in grupos.items():
        mapping = mapeamentos_por_chave.get(chave)
        categoria = categorias_por_id.get(mapping.categoria_id) if mapping else None
        status = "categorizada" if mapping else "pendente"
        quantidade_total = int(grupo["quantidade_total"])
        total_respostas += quantidade_total
        if status == "categorizada":
            respostas_categorizadas += quantidade_total

        variantes = [
            {"texto_original": texto, "quantidade": quantidade}
            for texto, quantidade in sorted(
                grupo["variantes"].items(),
                key=lambda item: (-item[1], normalizar_resposta_espontanea(item[0]), item[0]),
            )
        ]
        perguntas = []
        for pergunta in sorted(
            grupo["perguntas"].values(),
            key=lambda item: (item["ordem"], item["pergunta_id"]),
        ):
            perguntas.append({
                "id": pergunta["pergunta_id"],
                "pergunta_id": pergunta["pergunta_id"],
                "texto_pergunta": pergunta["texto_pergunta"],
                "quantidade": pergunta["quantidade"],
            })

        items.append({
            "chave_normalizada": chave,
            "quantidade_total": quantidade_total,
            "variantes": variantes,
            "perguntas": perguntas,
            "mapeamento_id": mapping.id if mapping else None,
            "categoria": None if categoria is None else {
                "id": categoria.id,
                "nome": categoria.nome,
            },
            "status": status,
        })

    items.sort(key=lambda item: (-item["quantidade_total"], item["chave_normalizada"]))
    return items, total_respostas, respostas_categorizadas


def get_respostas_espontaneas_resumo(
    db: Session,
    pesquisa_id: int,
    current_user: models.Usuario,
    *,
    busca: Optional[str] = None,
    modo_busca: str = "contem",
    pergunta_id: Optional[int] = None,
    status: Optional[str] = None,
    categoria_id: Optional[int] = None,
    pagina: int = 1,
    por_pagina: int = 25,
):
    _get_spontaneous_pesquisa(db, pesquisa_id, current_user)
    if pagina < 1 or por_pagina < 1 or por_pagina > 100:
        raise HTTPException(status_code=422, detail="Paginacao invalida.")
    raw_rows = _get_spontaneous_raw_answers(db, pesquisa_id, current_user, pergunta_id=pergunta_id)
    categorias = _get_spontaneous_categories(db, pesquisa_id, current_user)
    categorias_por_id = {categoria.id: categoria for categoria in categorias}
    mapeamentos_por_chave = {
        mapping.chave_normalizada: mapping
        for mapping in _get_spontaneous_mappings(db, pesquisa_id, current_user)
    }
    items, _, _ = _agrupar_respostas_espontaneas(raw_rows, categorias_por_id, mapeamentos_por_chave)

    normalized_busca = normalizar_resposta_espontanea(busca)
    modo = (modo_busca or "contem").strip().casefold()
    if modo not in {"contem", "comeca_com", "termina_com", "igual"}:
        raise HTTPException(status_code=422, detail="Modo de busca invalido.")
    if normalized_busca:
        if modo == "igual":
            items = [item for item in items if item["chave_normalizada"] == normalized_busca]
        elif modo == "comeca_com":
            items = [item for item in items if item["chave_normalizada"].startswith(normalized_busca)]
        elif modo == "termina_com":
            items = [item for item in items if item["chave_normalizada"].endswith(normalized_busca)]
        elif modo == "contem":
            items = [item for item in items if normalized_busca in item["chave_normalizada"]]

    if pergunta_id is not None:
        items = [
            item for item in items
            if any(pergunta["id"] == pergunta_id for pergunta in item["perguntas"])
        ]

    if status is not None:
        status_normalizado = status.strip().casefold()
        if status_normalizado not in {"categorizada", "pendente"}:
            raise HTTPException(status_code=422, detail="Status invalido.")
        items = [item for item in items if item["status"] == status_normalizado]

    if categoria_id is not None:
        items = [
            item for item in items
            if item["categoria"] is not None and item["categoria"]["id"] == categoria_id
        ]

    total_chaves = len(items)
    total_respostas = sum(item["quantidade_total"] for item in items)
    respostas_categorizadas = sum(item["quantidade_total"] for item in items if item["status"] == "categorizada")
    respostas_pendentes = total_respostas - respostas_categorizadas
    percentual_categorizado = round((respostas_categorizadas / total_respostas) * 100, 2) if total_respostas else 0.0
    total_paginas = max(1, ceil(total_chaves / por_pagina))
    pagina_atual = pagina
    inicio = (pagina_atual - 1) * por_pagina
    fim = inicio + por_pagina
    itens_paginados = items[inicio:fim]

    return {
        "pesquisa_id": pesquisa_id,
        "total_chaves": total_chaves,
        "total_respostas": total_respostas,
        "respostas_categorizadas": respostas_categorizadas,
        "respostas_pendentes": respostas_pendentes,
        "percentual_categorizado": percentual_categorizado,
        "pagina": pagina_atual,
        "por_pagina": por_pagina,
        "total_paginas": total_paginas,
        "itens": itens_paginados,
    }


def get_resposta_espontanea_categorias(db: Session, pesquisa_id: int, current_user: models.Usuario):
    _get_spontaneous_pesquisa(db, pesquisa_id, current_user)
    return _get_spontaneous_categories(db, pesquisa_id, current_user)


def create_resposta_espontanea_categoria(
    db: Session,
    pesquisa_id: int,
    categoria_in: schemas.RespostaEspontaneaCategoriaCreate,
    current_user: models.Usuario,
):
    _get_spontaneous_pesquisa(db, pesquisa_id, current_user)
    nome = categoria_in.nome.strip()
    nome_normalizado = normalizar_resposta_espontanea(nome)
    if not nome_normalizado:
        raise HTTPException(status_code=422, detail="Nome da categoria e obrigatorio.")

    existente = db.query(models.CategoriaRespostaEspontanea).filter(
        models.CategoriaRespostaEspontanea.pesquisa_id == pesquisa_id,
        models.CategoriaRespostaEspontanea.nome_normalizado == nome_normalizado,
        models.CategoriaRespostaEspontanea.ativo.is_(True),
    ).first()
    if existente:
        raise HTTPException(status_code=409, detail="Categoria ja cadastrada para esta pesquisa.")

    db_categoria = models.CategoriaRespostaEspontanea(
        pesquisa_id=pesquisa_id,
        nome=nome,
        nome_normalizado=nome_normalizado,
        ativo=True,
        criado_por_id=current_user.id,
        atualizado_por_id=current_user.id,
    )
    try:
        db.add(db_categoria)
        db.commit()
        db.refresh(db_categoria)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Categoria ja cadastrada para esta pesquisa.") from exc
    return db_categoria


def update_resposta_espontanea_categoria(
    db: Session,
    pesquisa_id: int,
    categoria_id: int,
    categoria_in: schemas.RespostaEspontaneaCategoriaUpdate,
    current_user: models.Usuario,
):
    db_categoria = _get_spontaneous_category(db, pesquisa_id, categoria_id, current_user)
    if not db_categoria:
        raise HTTPException(status_code=404, detail="Categoria nao encontrada.")

    update_data = categoria_in.model_dump(exclude_unset=True)
    nome = update_data.get("nome")
    if nome is not None:
        nome_normalizado = normalizar_resposta_espontanea(nome)
        if not nome_normalizado:
            raise HTTPException(status_code=422, detail="Nome da categoria e obrigatorio.")
        existe_outro = db.query(models.CategoriaRespostaEspontanea.id).filter(
            models.CategoriaRespostaEspontanea.pesquisa_id == pesquisa_id,
            models.CategoriaRespostaEspontanea.nome_normalizado == nome_normalizado,
            models.CategoriaRespostaEspontanea.ativo.is_(True),
            models.CategoriaRespostaEspontanea.id != categoria_id,
        ).first()
        if existe_outro:
            raise HTTPException(status_code=409, detail="Categoria ja cadastrada para esta pesquisa.")
        db_categoria.nome = nome.strip()
        db_categoria.nome_normalizado = nome_normalizado

    if "ativo" in update_data:
        if update_data["ativo"] is False:
            db_categoria.ativo = False
            mapeamentos_ativos = db.query(models.MapeamentoRespostaEspontanea).filter(
                models.MapeamentoRespostaEspontanea.pesquisa_id == pesquisa_id,
                models.MapeamentoRespostaEspontanea.categoria_id == categoria_id,
                models.MapeamentoRespostaEspontanea.ativo.is_(True),
            ).all()
            for mapeamento in mapeamentos_ativos:
                mapeamento.ativo = False
                mapeamento.atualizado_por_id = current_user.id
                mapeamento.atualizado_em = func.now()
        elif update_data["ativo"] is True:
            existe_outro = db.query(models.CategoriaRespostaEspontanea.id).filter(
                models.CategoriaRespostaEspontanea.pesquisa_id == pesquisa_id,
                models.CategoriaRespostaEspontanea.nome_normalizado == db_categoria.nome_normalizado,
                models.CategoriaRespostaEspontanea.ativo.is_(True),
                models.CategoriaRespostaEspontanea.id != categoria_id,
            ).first()
            if existe_outro:
                raise HTTPException(status_code=409, detail="Categoria ja cadastrada para esta pesquisa.")
            db_categoria.ativo = True

    db_categoria.atualizado_por_id = current_user.id
    db_categoria.atualizado_em = func.now()
    try:
        db.add(db_categoria)
        db.commit()
        db.refresh(db_categoria)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Categoria ja cadastrada para esta pesquisa.") from exc
    return db_categoria


def delete_resposta_espontanea_categoria(
    db: Session,
    pesquisa_id: int,
    categoria_id: int,
    current_user: models.Usuario,
):
    db_categoria = _get_spontaneous_category(db, pesquisa_id, categoria_id, current_user)
    if not db_categoria:
        raise HTTPException(status_code=404, detail="Categoria nao encontrada.")

    mapeamentos_ativos = db.query(models.MapeamentoRespostaEspontanea).filter(
        models.MapeamentoRespostaEspontanea.pesquisa_id == pesquisa_id,
        models.MapeamentoRespostaEspontanea.categoria_id == categoria_id,
        models.MapeamentoRespostaEspontanea.ativo.is_(True),
    ).all()

    try:
        for mapeamento in mapeamentos_ativos:
            mapeamento.ativo = False
            mapeamento.atualizado_por_id = current_user.id
            mapeamento.atualizado_em = func.now()
        db_categoria.ativo = False
        db_categoria.atualizado_por_id = current_user.id
        db_categoria.atualizado_em = func.now()
        db.commit()
        db.refresh(db_categoria)
    except Exception:
        db.rollback()
        raise
    return db_categoria


def mapear_respostas_espontaneas_em_lote(
    db: Session,
    pesquisa_id: int,
    payload: schemas.RespostaEspontaneaMapeamentoLote,
    current_user: models.Usuario,
):
    _get_spontaneous_pesquisa(db, pesquisa_id, current_user)
    categoria = _get_spontaneous_category(db, pesquisa_id, payload.categoria_id, current_user)
    if not categoria or not categoria.ativo:
        raise HTTPException(status_code=404, detail="Categoria nao encontrada.")

    chaves_normalizadas = [normalizar_resposta_espontanea(chave) for chave in payload.chaves_normalizadas]
    if not chaves_normalizadas or any(not chave for chave in chaves_normalizadas):
        raise HTTPException(status_code=422, detail="Lista de chaves invalida.")
    if len(chaves_normalizadas) != len(set(chaves_normalizadas)):
        raise HTTPException(status_code=422, detail="Chaves duplicadas no payload.")

    query = (
        db.query(models.Resposta.valor_resposta)
        .join(models.Pergunta, models.Pergunta.id == models.Resposta.pergunta_id)
        .join(models.Coleta, models.Coleta.id == models.Resposta.coleta_id)
        .join(models.Pesquisa, models.Pesquisa.id == models.Coleta.pesquisa_id)
        .join(models.Projeto, models.Projeto.id == models.Pesquisa.projeto_id)
        .filter(
            models.Pesquisa.id == pesquisa_id,
            models.Projeto.company_id == current_user.company_id,
            models.Coleta.company_id == current_user.company_id,
            models.Pergunta.pesquisa_id == pesquisa_id,
            models.Pergunta.ativo.is_(True),
            models.Pergunta.eh_resposta_espontanea.is_(True),
        )
        .distinct()
    )
    keys_existentes = {
        normalizar_resposta_espontanea(valor)
        for valor, in query.all()
    }
    if not set(chaves_normalizadas).issubset(keys_existentes):
        raise HTTPException(status_code=404, detail="Uma ou mais chaves nao foram encontradas nas respostas espontaneas da pesquisa.")

    mapeamentos_atual = {
        mapping.chave_normalizada: mapping
        for mapping in db.query(models.MapeamentoRespostaEspontanea).filter(
            models.MapeamentoRespostaEspontanea.pesquisa_id == pesquisa_id,
            models.MapeamentoRespostaEspontanea.ativo.is_(True),
        ).all()
    }

    criadas = atualizadas = inalteradas = 0
    for chave in chaves_normalizadas:
        existente = mapeamentos_atual.get(chave)
        if existente is None:
            db.add(models.MapeamentoRespostaEspontanea(
                pesquisa_id=pesquisa_id,
                categoria_id=categoria.id,
                chave_normalizada=chave,
                texto_referencia=chave,
                ativo=True,
                criado_por_id=current_user.id,
                atualizado_por_id=current_user.id,
            ))
            criadas += 1
            continue

        if existente.categoria_id == categoria.id:
            inalteradas += 1
            continue

        existente.categoria_id = categoria.id
        existente.texto_referencia = chave
        existente.atualizado_por_id = current_user.id
        existente.atualizado_em = func.now()
        atualizadas += 1

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Uma ou mais chaves ja possuem mapeamento ativo.") from exc
    except Exception:
        db.rollback()
        raise

    return {
        "categoria_id": categoria.id,
        "categoria_nome": categoria.nome,
        "criadas": criadas,
        "atualizadas": atualizadas,
        "inalteradas": inalteradas,
        "total_processado": len(chaves_normalizadas),
    }


def delete_resposta_espontanea_mapeamento(
    db: Session,
    pesquisa_id: int,
    mapeamento_id: int,
    current_user: models.Usuario,
):
    mapeamento = db.query(models.MapeamentoRespostaEspontanea)\
        .join(models.Pesquisa, models.Pesquisa.id == models.MapeamentoRespostaEspontanea.pesquisa_id)\
        .join(models.Projeto, models.Projeto.id == models.Pesquisa.projeto_id).filter(
        models.MapeamentoRespostaEspontanea.id == mapeamento_id,
        models.MapeamentoRespostaEspontanea.pesquisa_id == pesquisa_id,
        models.Projeto.company_id == current_user.company_id,
        models.MapeamentoRespostaEspontanea.ativo.is_(True),
    ).first()
    if not mapeamento:
        raise HTTPException(status_code=404, detail="Mapeamento nao encontrado.")

    mapeamento.ativo = False
    mapeamento.atualizado_por_id = current_user.id
    mapeamento.atualizado_em = func.now()
    db.add(mapeamento)
    db.commit()
    db.refresh(mapeamento)
    return mapeamento

# ==============================================================================
# COLETAS E RESPOSTAS
# ==============================================================================

def _get_coleta_by_client_uuid(db: Session, company_id: int, client_uuid: str):
    return db.query(models.Coleta).filter(
        models.Coleta.company_id == company_id,
        models.Coleta.client_uuid == client_uuid,
    ).first()


def _return_idempotent_coleta_or_reject(db_coleta, agente_id: int):
    if db_coleta.agente_id != agente_id:
        raise HTTPException(status_code=409, detail="Operacao de coleta em conflito.")
    return db_coleta


def _validate_collection_answers(
    db: Session,
    respostas,
    pesquisa_id: int,
    company_id: int,
) -> None:
    pergunta_ids = [resposta.pergunta_id for resposta in respostas]
    if len(pergunta_ids) != len(set(pergunta_ids)):
        raise HTTPException(status_code=422, detail="Pergunta duplicada no payload.")
    if not pergunta_ids:
        return

    perguntas_validas = db.query(models.Pergunta.id).join(
        models.Pesquisa,
        models.Pesquisa.id == models.Pergunta.pesquisa_id,
    ).join(
        models.Projeto,
        models.Projeto.id == models.Pesquisa.projeto_id,
    ).filter(
        models.Pergunta.id.in_(pergunta_ids),
        models.Pergunta.pesquisa_id == pesquisa_id,
        models.Pergunta.ativo.is_(True),
        models.Projeto.company_id == company_id,
    ).all()
    ids_validos = {pergunta_id for pergunta_id, in perguntas_validas}
    if ids_validos != set(pergunta_ids):
        raise HTTPException(status_code=404, detail="Pergunta nao encontrada.")


def create_coleta(
    db: Session,
    coleta_in: schemas.ColetaCreate,
    pesquisa_id: int,
    agente_id: int,
    company_id: int,
):
    # Coletas vêm do App Mobile. A validação de empresa geralmente é feita
    # garantindo que o Agente só baixou pesquisas da empresa dele.
    
    client_uuid = str(coleta_in.client_uuid)
    existing_coleta = _get_coleta_by_client_uuid(db, company_id, client_uuid)
    if existing_coleta:
        return _return_idempotent_coleta_or_reject(existing_coleta, agente_id)

    _validate_collection_answers(
        db=db,
        respostas=coleta_in.respostas,
        pesquisa_id=pesquisa_id,
        company_id=company_id,
    )

    # Converte lat/lon para GeoAlchemy Element
    ponto_inicio = None
    if coleta_in.localizacao_inicio:
        latitude_inicio = coleta_in.localizacao_inicio.lat
        longitude_inicio = coleta_in.localizacao_inicio.lon
        ponto_inicio = from_shape(Point(longitude_inicio, latitude_inicio), srid=4326)
        
    ponto_fim = None
    if coleta_in.localizacao_fim:
        latitude_fim = coleta_in.localizacao_fim.lat
        longitude_fim = coleta_in.localizacao_fim.lon
        ponto_fim = from_shape(Point(longitude_fim, latitude_fim), srid=4326)

    ponto_endereco = coleta_in.localizacao_fim or coleta_in.localizacao_inicio
    endereco_estimado = None
    if ponto_endereco:
        latitude_endereco = ponto_endereco.lat
        longitude_endereco = ponto_endereco.lon
        try:
            endereco_estimado = geocoding.obter_endereco_por_coords(
                latitude_endereco,
                longitude_endereco,
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
        company_id=company_id,
        client_uuid=client_uuid,
        foi_offline=coleta_in.foi_offline,
        status_sincronizacao="sincronizado"  # Sempre "sincronizado" quando chega via POST
    )
    db.add(db_coleta)
    try:
        db.flush()

        for resp in coleta_in.respostas:
            db.add(models.Resposta(
                coleta_id=db_coleta.id,
                pergunta_id=resp.pergunta_id,
                valor_resposta=resp.valor_resposta,
            ))

        db.flush()
        db.commit()
    except IntegrityError:
        db.rollback()
        existing_coleta = _get_coleta_by_client_uuid(db, company_id, client_uuid)
        if existing_coleta:
            return _return_idempotent_coleta_or_reject(existing_coleta, agente_id)
        raise
    except Exception:
        db.rollback()
        raise
    db.refresh(db_coleta)
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
        models.Usuario.id.label("agente_id"),
        models.Usuario.nome.label("agente_nome"),
        func.ST_AsGeoJSON(models.Setor.geometria).label("geojson")
    ).join(
        models.Pesquisa,
        models.Pesquisa.id == models.Setor.pesquisa_id
    ).join(
        models.Projeto,
        models.Projeto.id == models.Pesquisa.projeto_id
    ).outerjoin(
        models.Usuario,
        and_(
            models.Usuario.id == models.Setor.agente_id,
            models.Usuario.company_id == models.Projeto.company_id,
        )
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
        models.Coleta.data_inicio_coleta,
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

NAO_CATEGORIZADA = "Não categorizada"


def resolve_reportable_response_value(
    *,
    pergunta,
    valor_resposta,
    spontaneous_mapping: dict[str, str],
) -> str:
    valor_original = "" if valor_resposta is None else str(valor_resposta)
    if not pergunta.eh_resposta_espontanea:
        return valor_original

    chave_normalizada = normalizar_resposta_espontanea(valor_resposta)
    return spontaneous_mapping.get(chave_normalizada, NAO_CATEGORIZADA)


def get_active_spontaneous_mapping_for_report(
    *,
    db: Session,
    pesquisa_id: int,
    current_user: models.Usuario,
) -> dict[str, str]:
    rows = db.query(
        models.MapeamentoRespostaEspontanea.chave_normalizada,
        models.CategoriaRespostaEspontanea.nome,
    ).join(
        models.CategoriaRespostaEspontanea,
        and_(
            models.CategoriaRespostaEspontanea.id
            == models.MapeamentoRespostaEspontanea.categoria_id,
            models.CategoriaRespostaEspontanea.pesquisa_id == pesquisa_id,
        ),
    ).join(
        models.Pesquisa,
        models.Pesquisa.id == models.MapeamentoRespostaEspontanea.pesquisa_id,
    ).join(
        models.Projeto,
        models.Projeto.id == models.Pesquisa.projeto_id,
    ).filter(
        models.MapeamentoRespostaEspontanea.pesquisa_id == pesquisa_id,
        models.MapeamentoRespostaEspontanea.ativo.is_(True),
        models.CategoriaRespostaEspontanea.ativo.is_(True),
        models.Projeto.company_id == current_user.company_id,
    ).all()
    return {row.chave_normalizada: row.nome for row in rows}


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

    spontaneous_mapping = (
        get_active_spontaneous_mapping_for_report(
            db=db,
            pesquisa_id=pesquisa_id,
            current_user=current_user,
        )
        if any(pergunta.eh_resposta_espontanea for pergunta in perguntas)
        else {}
    )

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

        if pergunta.eh_resposta_espontanea:
            reportable_counts = defaultdict(int)
            for valor_resposta, contagem in rows:
                reportable_value = resolve_reportable_response_value(
                    pergunta=pergunta,
                    valor_resposta=valor_resposta,
                    spontaneous_mapping=spontaneous_mapping,
                )
                reportable_counts[reportable_value] += int(contagem or 0)
            rows = sorted(reportable_counts.items())

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

    perguntas = db.query(models.Pergunta).filter(
        models.Pergunta.pesquisa_id == pesquisa_id,
        models.Pergunta.id.in_([pergunta_linha_id, pergunta_coluna_id]),
        models.Pergunta.ativo.is_(True),
    ).all()
    perguntas_por_id = {pergunta.id: pergunta for pergunta in perguntas}
    pergunta_linha = perguntas_por_id.get(pergunta_linha_id)
    pergunta_coluna = perguntas_por_id.get(pergunta_coluna_id)
    if pergunta_linha is None or pergunta_coluna is None:
        raise HTTPException(status_code=404, detail="Pergunta nao encontrada.")

    has_spontaneous_question = (
        pergunta_linha.eh_resposta_espontanea
        or pergunta_coluna.eh_resposta_espontanea
    )
    spontaneous_mapping = (
        get_active_spontaneous_mapping_for_report(
            db=db,
            pesquisa_id=pesquisa_id,
            current_user=current_user,
        )
        if has_spontaneous_question
        else {}
    )

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
    reportable_counts = defaultdict(int)
    for row in result:
        reportable_line = resolve_reportable_response_value(
            pergunta=pergunta_linha,
            valor_resposta=row.linha,
            spontaneous_mapping=spontaneous_mapping,
        )
        reportable_column = resolve_reportable_response_value(
            pergunta=pergunta_coluna,
            valor_resposta=row.coluna,
            spontaneous_mapping=spontaneous_mapping,
        )
        reportable_counts[(reportable_line, reportable_column)] += int(row.total or 0)

    data = []
    for (reportable_line, reportable_column), total in reportable_counts.items():
        data.append({
            "linha": reportable_line,
            "coluna": reportable_column,
            "valor": total
        })
        
    return data
