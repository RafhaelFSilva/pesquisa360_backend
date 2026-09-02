"""Gates de autorizacao comercial para modulos e funcionalidades.

O recurso e autorizado antes do entitlement. Em contexto multiempresa, o
tenant comercial e o tenant do recurso autorizado, nunca um valor do cliente.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from pesquisa360 import crud
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.services import modulos


CAPACIDADE_INDISPONIVEL = "Capacidade comercial nao encontrada."
MODULO_NAO_CONTRATADO = "Modulo nao disponivel para esta empresa."
FEATURE_NAO_CONTRATADA = "Funcionalidade nao disponivel para esta empresa."


@dataclass(frozen=True)
class EntitlementContext:
    company_id: int
    projeto_id: int | None = None
    pesquisa_id: int | None = None
    recurso: Any | None = None


def get_company_entitlement_context(
    current_user=Depends(get_current_user),
) -> EntitlementContext:
    """Contexto sem recurso: usa somente a empresa principal autenticada."""
    return EntitlementContext(company_id=current_user.company_id)


def get_project_entitlement_context(
    projeto_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> EntitlementContext:
    projeto = crud.get_projeto(db, projeto_id, current_user)
    if projeto is None:
        raise HTTPException(status_code=404, detail="Projeto nao encontrado.")
    return EntitlementContext(
        company_id=projeto.company_id,
        projeto_id=projeto.id,
        recurso=projeto,
    )


def get_survey_entitlement_context(
    pesquisa_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> EntitlementContext:
    pesquisa = crud.get_pesquisa(db, pesquisa_id, current_user)
    if pesquisa is None:
        raise HTTPException(status_code=404, detail="Pesquisa nao encontrada.")
    return EntitlementContext(
        company_id=pesquisa.projeto.company_id,
        pesquisa_id=pesquisa.id,
        recurso=pesquisa,
    )


def require_module(
    modulo_chave: str,
    context_dependency: Callable = get_company_entitlement_context,
):
    """Cria dependency que exige modulo ativo e entitlement efetivo."""

    def gate(
        db: Session = Depends(get_db),
        context: EntitlementContext = Depends(context_dependency),
    ) -> EntitlementContext:
        if not modulos.modulo_disponivel(db, modulo_chave):
            raise HTTPException(status_code=404, detail=CAPACIDADE_INDISPONIVEL)
        if not modulos.empresa_tem_modulo(
            db,
            context.company_id,
            modulo_chave,
            projeto_id=context.projeto_id,
            pesquisa_id=context.pesquisa_id,
        ):
            raise HTTPException(status_code=403, detail=MODULO_NAO_CONTRATADO)
        return context

    return gate


def require_feature(
    modulo_chave: str,
    funcionalidade_chave: str,
    context_dependency: Callable = get_company_entitlement_context,
):
    """Cria dependency que exige feature ativa e explicitamente concedida."""

    def gate(
        db: Session = Depends(get_db),
        context: EntitlementContext = Depends(context_dependency),
    ) -> EntitlementContext:
        if not modulos.funcionalidade_disponivel(
            db, modulo_chave, funcionalidade_chave
        ):
            raise HTTPException(status_code=404, detail=CAPACIDADE_INDISPONIVEL)
        if not modulos.empresa_tem_funcionalidade(
            db,
            context.company_id,
            modulo_chave,
            funcionalidade_chave,
            projeto_id=context.projeto_id,
            pesquisa_id=context.pesquisa_id,
        ):
            raise HTTPException(status_code=403, detail=FEATURE_NAO_CONTRATADA)
        return context

    return gate
