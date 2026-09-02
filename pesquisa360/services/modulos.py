"""Resolucao de licenciamento comercial no monolito modular.

Entitlement nao substitui multitenancy nem ACL. O chamador sempre informa o
tenant derivado do usuario autenticado e esta camada valida qualquer recurso de
escopo antes de consultar licencas.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.exc import IntegrityError

from pesquisa360.db import models


STATUS_ATIVO = "ATIVO"


def _escopo_valido(db, company_id, projeto_id=None, pesquisa_id=None):
    if projeto_id is not None and pesquisa_id is not None:
        raise HTTPException(status_code=422, detail="Informe projeto ou pesquisa, nao ambos.")
    if pesquisa_id is not None:
        pesquisa = (
            db.query(models.Pesquisa)
            .join(models.Projeto, models.Projeto.id == models.Pesquisa.projeto_id)
            .filter(models.Pesquisa.id == pesquisa_id, models.Projeto.company_id == company_id)
            .first()
        )
        if pesquisa is None:
            raise HTTPException(status_code=404, detail="Pesquisa nao encontrada.")
        return pesquisa.projeto_id, pesquisa_id
    if projeto_id is not None:
        projeto = db.query(models.Projeto).filter(
            models.Projeto.id == projeto_id,
            models.Projeto.company_id == company_id,
        ).first()
        if projeto is None:
            raise HTTPException(status_code=404, detail="Projeto nao encontrado.")
    return projeto_id, pesquisa_id


def criar_entitlement(
    db: Session,
    company_id: int,
    modulo_id: int,
    *,
    projeto_id: int | None = None,
    pesquisa_id: int | None = None,
    **dados,
) -> models.ModuloEntitlement:
    """Constroi uma licenca somente depois de validar catalogo e tenant do escopo."""
    if db.get(models.Modulo, modulo_id) is None:
        raise ValueError("Modulo inexistente.")
    _escopo_valido(db, company_id, projeto_id, pesquisa_id)
    entitlement = models.ModuloEntitlement(
        company_id=company_id,
        modulo_id=modulo_id,
        projeto_id=projeto_id,
        pesquisa_id=pesquisa_id,
        **dados,
    )
    db.add(entitlement)
    return entitlement


def resolver_entitlements(
    db: Session,
    company_id: int,
    *,
    projeto_id: int | None = None,
    pesquisa_id: int | None = None,
    agora: datetime | None = None,
) -> list[models.ModuloEntitlement]:
    """Retorna licencas efetivas aplicaveis ao escopo, sempre de forma aditiva."""
    projeto_id, pesquisa_id = _escopo_valido(db, company_id, projeto_id, pesquisa_id)
    instante = agora or datetime.now(timezone.utc)
    escopos = [and_(
        models.ModuloEntitlement.projeto_id.is_(None),
        models.ModuloEntitlement.pesquisa_id.is_(None),
    )]
    if projeto_id is not None:
        escopos.append(models.ModuloEntitlement.projeto_id == projeto_id)
    if pesquisa_id is not None:
        escopos.append(models.ModuloEntitlement.pesquisa_id == pesquisa_id)

    return (
        db.query(models.ModuloEntitlement)
        .options(
            selectinload(models.ModuloEntitlement.modulo),
            selectinload(models.ModuloEntitlement.funcionalidades).selectinload(
                models.ModuloEntitlementFuncionalidade.funcionalidade
            ),
        )
        .filter(
            models.ModuloEntitlement.company_id == company_id,
            models.ModuloEntitlement.status == STATUS_ATIVO,
            or_(models.ModuloEntitlement.inicia_em.is_(None), models.ModuloEntitlement.inicia_em <= instante),
            or_(models.ModuloEntitlement.expira_em.is_(None), models.ModuloEntitlement.expira_em >= instante),
            or_(*escopos),
        )
        .all()
    )


def resolver_modulos_empresa(db, company_id, *, projeto_id=None, pesquisa_id=None, agora=None):
    """Normaliza o catalogo licenciado sem expor IDs internos."""
    resolvidos = {}
    for entitlement in resolver_entitlements(
        db, company_id, projeto_id=projeto_id, pesquisa_id=pesquisa_id, agora=agora
    ):
        modulo = entitlement.modulo
        if modulo is None or not modulo.ativo:
            continue
        item = resolvidos.setdefault(
            modulo.chave,
            {"chave": modulo.chave, "nome": modulo.nome, "funcionalidades": {}},
        )
        for vinculo in entitlement.funcionalidades:
            funcionalidade = vinculo.funcionalidade
            if funcionalidade is not None and funcionalidade.ativo:
                item["funcionalidades"][funcionalidade.chave] = {
                    "chave": funcionalidade.chave,
                    "nome": funcionalidade.nome,
                }
    return [
        {
            "chave": item["chave"],
            "nome": item["nome"],
            "funcionalidades": sorted(item["funcionalidades"].values(), key=lambda feature: feature["chave"]),
        }
        for item in sorted(resolvidos.values(), key=lambda modulo: modulo["chave"])
    ]


def empresa_tem_modulo(db, company_id, chave, **escopo):
    return any(
        entitlement.modulo is not None
        and entitlement.modulo.ativo
        and entitlement.modulo.chave == chave
        for entitlement in resolver_entitlements(db, company_id, **escopo)
    )


def empresa_tem_funcionalidade(db, company_id, modulo_chave, funcionalidade_chave, **escopo):
    return any(
        modulo["chave"] == modulo_chave
        and any(feature["chave"] == funcionalidade_chave for feature in modulo["funcionalidades"])
        for modulo in resolver_modulos_empresa(db, company_id, **escopo)
    )


def modulo_disponivel(db: Session, chave: str) -> bool:
    """Modulo inexistente ou inativo nao e uma capacidade disponivel."""
    return (
        db.query(models.Modulo.id)
        .filter(models.Modulo.chave == chave, models.Modulo.ativo.is_(True))
        .first()
        is not None
    )


def funcionalidade_disponivel(
    db: Session,
    modulo_chave: str,
    funcionalidade_chave: str,
) -> bool:
    """A feature precisa existir, estar ativa e pertencer ao modulo ativo."""
    return (
        db.query(models.ModuloFuncionalidade.id)
        .join(models.Modulo, models.Modulo.id == models.ModuloFuncionalidade.modulo_id)
        .filter(
            models.Modulo.chave == modulo_chave,
            models.Modulo.ativo.is_(True),
            models.ModuloFuncionalidade.chave == funcionalidade_chave,
            models.ModuloFuncionalidade.ativo.is_(True),
        )
        .first()
        is not None
    )


def vincular_funcionalidade(db, entitlement, funcionalidade):
    """Novas features so entram em contratos por concessao explicita."""
    if entitlement.modulo_id != funcionalidade.modulo_id:
        raise ValueError("A funcionalidade nao pertence ao modulo do entitlement.")
    vinculo = models.ModuloEntitlementFuncionalidade(
        entitlement=entitlement,
        funcionalidade=funcionalidade,
    )
    db.add(vinculo)
    return vinculo


def funcionalidades_concediveis(db: Session, modulo_id: int, ids: list[int]):
    if not ids:
        return []
    funcionalidades = db.query(models.ModuloFuncionalidade).filter(
        models.ModuloFuncionalidade.id.in_(ids)
    ).all()
    if len(funcionalidades) != len(ids) or any(
        item.modulo_id != modulo_id for item in funcionalidades
    ):
        raise HTTPException(status_code=422, detail="Funcionalidade invalida para o modulo.")
    if any(not item.ativo for item in funcionalidades):
        raise HTTPException(status_code=422, detail="Funcionalidade indisponivel para concessao.")
    return funcionalidades


def salvar_entitlement(db: Session, entitlement: models.ModuloEntitlement) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Ja existe entitlement deste modulo para este escopo.",
        ) from exc
