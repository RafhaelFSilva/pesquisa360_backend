"""Administracao global, read-only do catalogo e mutacao de entitlements."""
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, selectinload

from pesquisa360 import crud, schemas
from pesquisa360.core.dependencies import get_db, require_superadmin
from pesquisa360.db import models
from pesquisa360.services import auditoria, modulos

router = APIRouter(prefix="/admin", tags=["Admin Modulos"])


def _empresa(db, company_id):
    empresa = crud.get_company(db, company_id)
    if empresa is None:
        raise HTTPException(status_code=404, detail="Empresa nao encontrada.")
    return empresa


def _entitlement(db, company_id, entitlement_id):
    item = db.query(models.ModuloEntitlement).options(
        selectinload(models.ModuloEntitlement.modulo),
        selectinload(models.ModuloEntitlement.funcionalidades).selectinload(
            models.ModuloEntitlementFuncionalidade.funcionalidade
        ),
    ).filter(
        models.ModuloEntitlement.id == entitlement_id,
        models.ModuloEntitlement.company_id == company_id,
    ).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Entitlement nao encontrado.")
    return item


def _estado(item):
    return {
        "id": item.id,
        "modulo": {"id": item.modulo.id, "chave": item.modulo.chave, "nome": item.modulo.nome},
        "escopo": "PESQUISA" if item.pesquisa_id else "PROJETO" if item.projeto_id else "EMPRESA",
        "projeto_id": item.projeto_id,
        "pesquisa_id": item.pesquisa_id,
        "status": item.status,
        "inicia_em": item.inicia_em,
        "expira_em": item.expira_em,
        "funcionalidades": [
            {"id": v.funcionalidade.id, "chave": v.funcionalidade.chave, "nome": v.funcionalidade.nome}
            for v in item.funcionalidades if v.funcionalidade is not None
        ],
    }


def _auditavel(estado):
    return {
        **estado,
        "inicia_em": estado["inicia_em"].isoformat() if estado["inicia_em"] else None,
        "expira_em": estado["expira_em"].isoformat() if estado["expira_em"] else None,
    }


def _utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@router.get("/modulos/")
def catalogo(db: Session = Depends(get_db), _=Depends(require_superadmin)):
    itens = db.query(models.Modulo).options(selectinload(models.Modulo.funcionalidades)).order_by(models.Modulo.id).all()
    return {"modulos": [{
        "id": item.id, "chave": item.chave, "nome": item.nome, "ativo": item.ativo,
        "funcionalidades": [{"id": f.id, "chave": f.chave, "nome": f.nome, "ativo": f.ativo}
                            for f in sorted(item.funcionalidades, key=lambda x: x.id)],
    } for item in itens]}


@router.get("/empresas/{company_id}/entitlements/")
def listar(company_id: int, db: Session = Depends(get_db), _=Depends(require_superadmin)):
    _empresa(db, company_id)
    itens = db.query(models.ModuloEntitlement).options(
        selectinload(models.ModuloEntitlement.modulo),
        selectinload(models.ModuloEntitlement.funcionalidades).selectinload(models.ModuloEntitlementFuncionalidade.funcionalidade),
    ).filter(models.ModuloEntitlement.company_id == company_id).order_by(models.ModuloEntitlement.id).all()
    return {"company_id": company_id, "entitlements": [_estado(item) for item in itens]}


@router.get("/empresas/{company_id}/recursos/")
def listar_recursos(company_id: int, db: Session = Depends(get_db), _=Depends(require_superadmin)):
    _empresa(db, company_id)
    projetos = db.query(models.Projeto).options(selectinload(models.Projeto.pesquisas)).filter(
        models.Projeto.company_id == company_id
    ).order_by(models.Projeto.id).all()
    return {"projetos": [{
        "id": projeto.id,
        "nome": projeto.nome,
        "pesquisas": [{"id": pesquisa.id, "titulo": pesquisa.titulo} for pesquisa in projeto.pesquisas],
    } for projeto in projetos]}


@router.post("/empresas/{company_id}/entitlements/", status_code=status.HTTP_201_CREATED)
def criar(company_id: int, payload: schemas.AdminEntitlementCreate, db: Session = Depends(get_db), user=Depends(require_superadmin)):
    _empresa(db, company_id)
    modulo = db.get(models.Modulo, payload.modulo_id)
    if modulo is None or not modulo.ativo:
        raise HTTPException(status_code=422, detail="Modulo indisponivel para concessao.")
    features = modulos.funcionalidades_concediveis(db, modulo.id, payload.funcionalidade_ids)
    item = modulos.criar_entitlement(
        db, company_id, modulo.id, projeto_id=payload.projeto_id, pesquisa_id=payload.pesquisa_id,
        inicia_em=payload.inicia_em, expira_em=payload.expira_em, criado_por_usuario_id=user.id,
    )
    modulos.salvar_entitlement(db, item)
    for feature in features:
        modulos.vincular_funcionalidade(db, item, feature)
    db.flush()
    estado = _estado(_entitlement(db, company_id, item.id))
    auditoria.adicionar_evento(db, auditoria.MODULE_ENTITLEMENT_CREATED, user=user, company_id=company_id,
                               details={"entitlement_id": item.id, "before": None, "after": _auditavel(estado)})
    db.commit()
    return estado


@router.patch("/empresas/{company_id}/entitlements/{entitlement_id}")
def atualizar(company_id: int, entitlement_id: int, payload: schemas.AdminEntitlementUpdate,
              db: Session = Depends(get_db), user=Depends(require_superadmin)):
    item = _entitlement(db, company_id, entitlement_id)
    before = _auditavel(_estado(item))
    dados = payload.model_dump(exclude_unset=True)
    inicio = dados.get("inicia_em", item.inicia_em)
    fim = dados.get("expira_em", item.expira_em)
    if inicio and fim and _utc(fim) < _utc(inicio):
        raise HTTPException(status_code=422, detail="expira_em deve ser maior ou igual a inicia_em.")
    for chave, valor in dados.items():
        setattr(item, chave, valor)
    db.flush()
    after = _auditavel(_estado(item))
    auditoria.adicionar_evento(db, auditoria.MODULE_ENTITLEMENT_UPDATED, user=user, company_id=company_id,
                               details={"entitlement_id": item.id, "before": before, "after": after})
    db.commit()
    return _estado(item)


@router.put("/empresas/{company_id}/entitlements/{entitlement_id}/funcionalidades")
def atualizar_features(company_id: int, entitlement_id: int, payload: schemas.AdminEntitlementFeaturesUpdate,
                       db: Session = Depends(get_db), user=Depends(require_superadmin)):
    item = _entitlement(db, company_id, entitlement_id)
    features = modulos.funcionalidades_concediveis(db, item.modulo_id, payload.funcionalidade_ids)
    before = _auditavel(_estado(item))
    item.funcionalidades[:] = []
    db.flush()
    for feature in features:
        modulos.vincular_funcionalidade(db, item, feature)
    db.flush()
    after = _auditavel(_estado(_entitlement(db, company_id, item.id)))
    auditoria.adicionar_evento(db, auditoria.MODULE_ENTITLEMENT_FEATURES_UPDATED, user=user, company_id=company_id,
                               details={"entitlement_id": item.id, "before": before, "after": after})
    db.commit()
    return _estado(item)
