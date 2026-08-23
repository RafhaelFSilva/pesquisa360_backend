"""Servico de dominio das liderancas politicas.

Tenant deriva sempre de LiderancaPolitica -> Projeto -> company_id; o cliente
nunca envia company_id. Acesso invalido responde 404, seguindo o padrao do
projeto (nunca 403 para recurso de outro tenant).
"""

from __future__ import annotations

from typing import Optional, Sequence

from fastapi import HTTPException, status
from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from sqlalchemy.orm import Session

from pesquisa360.core.dependencies import _get_profile_name, _is_manager_name, _is_superadmin_name
from pesquisa360.db import models
from pesquisa360.services import base_eleitoral as base_service

TIPO_TERRITORIO_ACEITO = "BAIRRO"


def _nao_encontrado(entidade: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"{entidade} nao encontrado."
    )


def assegurar_permissao_escrita(db: Session, current_user: models.Usuario) -> None:
    """Escrita restrita a Gerente/Superadmin; Agente nunca cadastra nem define cota."""
    perfil = _get_profile_name(db, current_user)
    if not (_is_superadmin_name(perfil) or _is_manager_name(perfil)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso restrito a Gerente ou Superadmin.",
        )


def obter_projeto(db: Session, projeto_id: int, current_user: models.Usuario) -> models.Projeto:
    projeto = (
        db.query(models.Projeto)
        .filter(
            models.Projeto.id == projeto_id,
            models.Projeto.company_id == current_user.company_id,
        )
        .first()
    )
    if projeto is None:
        raise _nao_encontrado("Projeto")
    return projeto


def obter_lideranca(
    db: Session, projeto_id: int, lideranca_id: int, current_user: models.Usuario
) -> models.LiderancaPolitica:
    projeto = obter_projeto(db, projeto_id, current_user)
    lideranca = (
        db.query(models.LiderancaPolitica)
        .filter(
            models.LiderancaPolitica.id == lideranca_id,
            models.LiderancaPolitica.projeto_id == projeto.id,
        )
        .first()
    )
    if lideranca is None:
        raise _nao_encontrado("Lideranca")
    return lideranca


def listar_liderancas(
    db: Session,
    projeto_id: int,
    current_user: models.Usuario,
    *,
    incluir_inativas: bool = False,
    posicionamento: Optional[str] = None,
) -> list[models.LiderancaPolitica]:
    """`posicionamento=None` mantem o comportamento antigo: devolve todas."""
    projeto = obter_projeto(db, projeto_id, current_user)
    query = db.query(models.LiderancaPolitica).filter(
        models.LiderancaPolitica.projeto_id == projeto.id
    )
    if not incluir_inativas:
        query = query.filter(models.LiderancaPolitica.ativo.is_(True))
    if posicionamento is not None:
        query = query.filter(models.LiderancaPolitica.posicionamento == posicionamento)
    return query.order_by(models.LiderancaPolitica.nome, models.LiderancaPolitica.id).all()


def _validar_posicionamento(valor: str) -> str:
    """Ultima barreira do dominio.

    O schema ja rejeita valor fora da lista com 422; isto protege as chamadas
    internas de servico, que nao passam pelo Pydantic.
    """
    if valor not in models.POSICIONAMENTOS_LIDERANCA:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="posicionamento invalido.",
        )
    return valor


def criar_lideranca(
    db: Session,
    projeto_id: int,
    nome: str,
    current_user: models.Usuario,
    *,
    posicionamento: str = models.POSICIONAMENTO_LIDERANCA_PADRAO,
) -> models.LiderancaPolitica:
    """Chamada antiga, sem posicionamento, continua criando INDEFINIDA."""
    projeto = obter_projeto(db, projeto_id, current_user)
    assegurar_permissao_escrita(db, current_user)
    lideranca = models.LiderancaPolitica(
        projeto_id=projeto.id,
        nome=nome.strip(),
        posicionamento=_validar_posicionamento(posicionamento),
    )
    db.add(lideranca)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(lideranca)
    return lideranca


# Sentinela: distingue "campo ausente no PATCH" (manter) de "null" (remover).
NAO_INFORMADO = object()


def _ponto_para_geometria(ponto):
    """Point do contrato -> geometria persistivel.

    A validacao de tipo e de faixa vive no schema; aqui so traduzimos. Nunca
    inventamos coordenada: `None` significa lideranca sem ponto no mapa.
    """
    if ponto is None:
        return None
    longitude, latitude = ponto["coordinates"]
    return from_shape(Point(longitude, latitude), srid=4326)


def atualizar_lideranca(
    db: Session,
    projeto_id: int,
    lideranca_id: int,
    current_user: models.Usuario,
    *,
    nome: Optional[str] = None,
    ativo: Optional[bool] = None,
    localizacao=NAO_INFORMADO,
    posicionamento: Optional[str] = None,
) -> models.LiderancaPolitica:
    lideranca = obter_lideranca(db, projeto_id, lideranca_id, current_user)
    assegurar_permissao_escrita(db, current_user)
    if nome is not None:
        lideranca.nome = nome.strip()
    if ativo is not None:
        lideranca.ativo = ativo
    if posicionamento is not None:
        # Nao mexe em cota, bairros nem config da onda: so troca o campo politico.
        lideranca.posicionamento = _validar_posicionamento(posicionamento)
    if localizacao is not NAO_INFORMADO:
        # Nao toca em setor, cota, bairros nem config da onda.
        lideranca.localizacao = _ponto_para_geometria(localizacao)
    db.add(lideranca)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(lideranca)
    return lideranca


def desativar_lideranca(
    db: Session, projeto_id: int, lideranca_id: int, current_user: models.Usuario
) -> models.LiderancaPolitica:
    """Soft delete: preserva historico de configuracoes e territorios."""
    return atualizar_lideranca(
        db, projeto_id, lideranca_id, current_user, ativo=False
    )


# --- Configuracao por onda ---------------------------------------------------


def listar_configs(
    db: Session, lideranca_id: int, pesquisa_id: Optional[int] = None
) -> list[models.LiderancaPesquisaConfig]:
    query = db.query(models.LiderancaPesquisaConfig).filter(
        models.LiderancaPesquisaConfig.lideranca_id == lideranca_id
    )
    if pesquisa_id is not None:
        query = query.filter(models.LiderancaPesquisaConfig.pesquisa_id == pesquisa_id)
    return query.order_by(models.LiderancaPesquisaConfig.pesquisa_id).all()


def definir_config_pesquisa(
    db: Session,
    projeto_id: int,
    lideranca_id: int,
    pesquisa_id: int,
    current_user: models.Usuario,
    *,
    setor_id: Optional[int] = None,
    cota_votos_validos: Optional[int] = None,
) -> models.LiderancaPesquisaConfig:
    """Setor e cota da lideranca NAQUELA onda. Alterar uma onda nao afeta as outras."""
    lideranca = obter_lideranca(db, projeto_id, lideranca_id, current_user)
    assegurar_permissao_escrita(db, current_user)

    pesquisa = (
        db.query(models.Pesquisa)
        .filter(
            models.Pesquisa.id == pesquisa_id,
            models.Pesquisa.projeto_id == lideranca.projeto_id,
        )
        .first()
    )
    if pesquisa is None:
        raise _nao_encontrado("Pesquisa")

    if setor_id is not None:
        # Setor precisa ser da MESMA pesquisa configurada.
        setor = (
            db.query(models.Setor)
            .filter(models.Setor.id == setor_id, models.Setor.pesquisa_id == pesquisa.id)
            .first()
        )
        if setor is None:
            raise _nao_encontrado("Setor")

    if cota_votos_validos is not None and cota_votos_validos < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="cota_votos_validos deve ser maior ou igual a zero.",
        )

    config = (
        db.query(models.LiderancaPesquisaConfig)
        .filter(
            models.LiderancaPesquisaConfig.lideranca_id == lideranca.id,
            models.LiderancaPesquisaConfig.pesquisa_id == pesquisa.id,
        )
        .first()
    )
    if config is None:
        config = models.LiderancaPesquisaConfig(
            lideranca_id=lideranca.id, pesquisa_id=pesquisa.id
        )
    config.setor_id = setor_id
    config.cota_votos_validos = cota_votos_validos
    db.add(config)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(config)
    return config


# --- Territorios -------------------------------------------------------------


def listar_territorios(
    db: Session, lideranca_id: int
) -> list[models.TerritorioEleitoral]:
    return (
        db.query(models.TerritorioEleitoral)
        .join(
            models.LiderancaTerritorioEleitoral,
            models.LiderancaTerritorioEleitoral.territorio_eleitoral_id
            == models.TerritorioEleitoral.id,
        )
        .filter(models.LiderancaTerritorioEleitoral.lideranca_id == lideranca_id)
        .order_by(models.TerritorioEleitoral.nome, models.TerritorioEleitoral.id)
        .all()
    )


def definir_territorios(
    db: Session,
    projeto_id: int,
    lideranca_id: int,
    territorio_ids: Sequence[int],
    current_user: models.Usuario,
) -> list[models.TerritorioEleitoral]:
    """Substituicao transacional do conjunto de bairros.

    O cliente envia apenas ids de territorio; a Base valida e derivada do
    Projeto, nunca aceita do payload. Um id invalido derruba o lote inteiro.
    """
    lideranca = obter_lideranca(db, projeto_id, lideranca_id, current_user)
    assegurar_permissao_escrita(db, current_user)

    unicos = list(dict.fromkeys(territorio_ids))
    if unicos:
        base = base_service.obter_base_principal_projeto_opcional(
            db, lideranca.projeto_id, current_user
        )
        if base is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="O projeto nao possui Base Eleitoral principal vinculada.",
            )
        encontrados = (
            db.query(models.TerritorioEleitoral)
            .filter(
                models.TerritorioEleitoral.id.in_(unicos),
                models.TerritorioEleitoral.base_eleitoral_id == base.id,
                models.TerritorioEleitoral.tipo == TIPO_TERRITORIO_ACEITO,
            )
            .all()
        )
        if len(encontrados) != len(unicos):
            # Territorio de outra base, de outro tenant ou de nivel diferente
            # de BAIRRO: nao existe para este projeto.
            raise _nao_encontrado("Territorio eleitoral")

    try:
        db.query(models.LiderancaTerritorioEleitoral).filter(
            models.LiderancaTerritorioEleitoral.lideranca_id == lideranca.id
        ).delete(synchronize_session=False)
        for territorio_id in unicos:
            db.add(
                models.LiderancaTerritorioEleitoral(
                    lideranca_id=lideranca.id, territorio_eleitoral_id=territorio_id
                )
            )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return listar_territorios(db, lideranca.id)
