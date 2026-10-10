"""Paineis personalizados da Apuracao Eleitoral -- configuracao por TENANT (ADR-085).

O tenant vem sempre de `current_user.company_id`. Painel de outra empresa
responde 404 (nunca 403): negar nao pode revelar que o painel existe.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from pesquisa360.db import models
from pesquisa360.db.models_tse import (
    TseCandidato, TseCargo, TseEleicao, TseFederacao, TsePartido,
)
from pesquisa360.schemas_apuracao import PainelIn


def _company_id(current_user) -> int:
    company_id = getattr(current_user, "company_id", None)
    if company_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Usuário sem empresa: painéis de apuração pertencem a uma empresa.",
        )
    return company_id


def _nao_encontrado() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Painel não encontrado.")


def _eleicao_do_painel(db: Session, painel: models.ApuracaoPainel) -> TseEleicao | None:
    return db.scalars(select(TseEleicao).where(
        TseEleicao.origem == painel.origem, TseEleicao.pleito == painel.pleito,
        TseEleicao.codigo_eleicao == painel.codigo_eleicao)).first()


def _eleicao_do_corpo(db: Session, eleicao_id: int) -> TseEleicao:
    eleicao = db.get(TseEleicao, eleicao_id)
    if eleicao is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Eleição inexistente na base de apuração.")
    return eleicao


def resumo(db: Session, painel: models.ApuracaoPainel) -> dict:
    eleicao = _eleicao_do_painel(db, painel)
    return {
        "id": painel.id, "nome": painel.nome, "descricao": painel.descricao,
        "tipo": painel.tipo, "origem": painel.origem, "pleito": painel.pleito,
        "codigo_eleicao": painel.codigo_eleicao,
        "eleicao_id": eleicao.id if eleicao else None,
        "eleicao_nome": eleicao.nome if eleicao else None,
        "uf": painel.uf, "total_itens": len(painel.itens),
        "criado_em": painel.criado_em, "atualizado_em": painel.atualizado_em,
    }


def detalhe(db: Session, painel: models.ApuracaoPainel) -> dict:
    return {**resumo(db, painel), "itens": [{
        "id": item.id, "tipo": item.tipo, "cargo_codigo": item.cargo_codigo,
        "sqcand": item.sqcand, "partido_numero": item.partido_numero,
        "federacao_numero": item.federacao_numero, "ordem": item.ordem, "ativo": item.ativo,
    } for item in painel.itens]}


def listar(db: Session, current_user) -> list[models.ApuracaoPainel]:
    return list(db.scalars(
        select(models.ApuracaoPainel)
        .where(models.ApuracaoPainel.company_id == _company_id(current_user))
        .order_by(models.ApuracaoPainel.nome, models.ApuracaoPainel.id)))


def obter(db: Session, painel_id: int, current_user) -> models.ApuracaoPainel:
    painel = db.scalars(select(models.ApuracaoPainel).where(
        models.ApuracaoPainel.id == painel_id,
        models.ApuracaoPainel.company_id == _company_id(current_user))).first()
    if painel is None:
        raise _nao_encontrado()
    return painel


def _invalido(detalhe: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detalhe)


def _validar_acompanhados(db: Session, eleicao: TseEleicao, dados: PainelIn) -> None:
    """Itens do painel de distribuicao precisam existir no pleito e bater com o cargo.

    Um pleito tem mais de uma eleicao (federal e estadual): o candidato e o
    partido sao procurados em qualquer eleicao do mesmo pleito e origem.
    """
    do_pleito = select(TseEleicao.id).where(TseEleicao.origem == eleicao.origem,
                                            TseEleicao.pleito == eleicao.pleito)
    for item in dados.itens:
        if item.tipo == "CANDIDATO":
            cargo = db.scalar(
                select(TseCargo.codigo)
                .join(TseCandidato, TseCandidato.cargo_id == TseCargo.id)
                .where(TseCandidato.sqcand == item.sqcand,
                       TseCandidato.eleicao_id.in_(do_pleito)))
            if cargo is None:
                raise _invalido(f"Candidato {item.sqcand} não existe neste pleito.")
            if cargo != item.cargo_codigo:
                raise _invalido(
                    f"Candidato {item.sqcand} não disputa o cargo {item.cargo_codigo}.")
        elif item.federacao_numero:
            if db.scalar(select(TseFederacao.id).where(
                    TseFederacao.numero == item.federacao_numero,
                    TseFederacao.eleicao_id.in_(do_pleito))) is None:
                raise _invalido(f"Federação {item.federacao_numero} não existe neste pleito.")
        elif db.scalar(select(TsePartido.id).where(
                TsePartido.numero == item.partido_numero,
                TsePartido.eleicao_id.in_(do_pleito))) is None:
            raise _invalido(f"Partido {item.partido_numero} não existe neste pleito.")


def _aplicar(db: Session, painel: models.ApuracaoPainel, dados: PainelIn) -> None:
    eleicao = _eleicao_do_corpo(db, dados.eleicao_id)
    if dados.tipo == "DISTRIBUICAO_TERRITORIAL":
        _validar_acompanhados(db, eleicao, dados)
    painel.tipo = dados.tipo
    painel.nome, painel.descricao, painel.uf = dados.nome, dados.descricao, dados.uf
    painel.origem, painel.pleito = eleicao.origem, eleicao.pleito
    painel.codigo_eleicao = eleicao.codigo_eleicao
    # Substituicao integral: a ordem dos itens e a ordem da lista recebida.
    painel.itens = [models.ApuracaoPainelItem(
        tipo=item.tipo, cargo_codigo=item.cargo_codigo, sqcand=item.sqcand,
        partido_numero=item.partido_numero, federacao_numero=item.federacao_numero,
        ordem=ordem, ativo=item.ativo,
    ) for ordem, item in enumerate(dados.itens)]


def criar(db: Session, dados: PainelIn, current_user) -> models.ApuracaoPainel:
    painel = models.ApuracaoPainel(company_id=_company_id(current_user),
                                   criado_por_id=getattr(current_user, "id", None))
    _aplicar(db, painel, dados)
    db.add(painel)
    db.commit()
    db.refresh(painel)
    return painel


def atualizar(db: Session, painel_id: int, dados: PainelIn,
              current_user) -> models.ApuracaoPainel:
    painel = obter(db, painel_id, current_user)
    _aplicar(db, painel, dados)
    db.commit()
    db.refresh(painel)
    return painel


def excluir(db: Session, painel_id: int, current_user) -> None:
    db.delete(obter(db, painel_id, current_user))
    db.commit()
