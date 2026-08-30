"""Cobertura territorial de campo (PROMPT 06) -- atividade CONHECIDA.

Eventos georreferenciados ja registrados (coletas concluidas e tentativas
encerradas) dos setores do agente. Nao e tracking: nao sabe onde os outros
agentes estao agora, nem o trajeto percorrido. ORIENTATIVO: nada aqui
bloqueia abordagem, coleta ou sincronizacao.

Deduplicacao: uma entrevista concluida e representada UMA vez (a Coleta);
a TentativaCampo CONCLUIDA vinculada a ela nao vira segundo evento.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from fastapi import HTTPException
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from pesquisa360 import schemas
from pesquisa360.db import models
from pesquisa360.services import acessos

TIPO_COLETA = "COLETA"
TIPO_TENTATIVA = "TENTATIVA"

# Default UNICO e documentado da distancia operacional recomendada entre
# abordagens (metros). Usado so quando a pesquisa nao configurou o parametro.
# Nao e a tolerancia de geofence (conceito diferente). Nao e bloqueante.
DISTANCIA_RECOMENDADA_PADRAO_METROS = 100


def _coordenada_valida(lat, lng) -> bool:
    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return False
    return -90 <= lat <= 90 and -180 <= lng <= 180 and lat == lat and lng == lng


def setores_do_agente(db: Session, pesquisa_id: int, current_user) -> list[models.Setor]:
    """Mesma regra de GET /agente/missao: N:N ativo ou agente_id legado,
    finalidade operacional."""
    return (
        db.query(models.Setor)
        .outerjoin(
            models.SetorAgente,
            and_(
                models.SetorAgente.setor_id == models.Setor.id,
                models.SetorAgente.agente_id == current_user.id,
                models.SetorAgente.ativo.is_(True),
            ),
        )
        .filter(
            models.Setor.pesquisa_id == pesquisa_id,
            or_(models.SetorAgente.id.is_not(None), models.Setor.agente_id == current_user.id),
            models.Setor.finalidade.in_([
                schemas.FinalidadeSetor.OPERACAO.value,
                schemas.FinalidadeSetor.AMBOS.value,
            ]),
        )
        .distinct()
        .order_by(models.Setor.id.asc())
        .all()
    )


def obter_configuracao_campo(db: Session, pesquisa_id: int) -> Optional[models.ConfiguracaoCampoPesquisa]:
    return (
        db.query(models.ConfiguracaoCampoPesquisa)
        .filter(models.ConfiguracaoCampoPesquisa.pesquisa_id == pesquisa_id)
        .first()
    )


def distancia_recomendada(db: Session, pesquisa_id: int) -> tuple[int, bool]:
    """(metros, configurada?)"""
    config = obter_configuracao_campo(db, pesquisa_id)
    if config is not None and config.distancia_recomendada_entre_abordagens_metros:
        return int(config.distancia_recomendada_entre_abordagens_metros), True
    return DISTANCIA_RECOMENDADA_PADRAO_METROS, False


def definir_configuracao_campo(
    db: Session, projeto_id: int, pesquisa_id: int, payload: schemas.ConfiguracaoCampoRequest, current_user
) -> models.ConfiguracaoCampoPesquisa:
    pesquisa = (
        db.query(models.Pesquisa)
        .join(models.Projeto, models.Projeto.id == models.Pesquisa.projeto_id)
        .filter(
            models.Pesquisa.id == pesquisa_id,
            models.Pesquisa.projeto_id == projeto_id,
            acessos.filtro_projeto_acessivel(current_user),
        )
        .first()
    )
    if pesquisa is None:
        raise HTTPException(status_code=404, detail="Pesquisa nao encontrada.")
    config = obter_configuracao_campo(db, pesquisa_id)
    if config is None:
        config = models.ConfiguracaoCampoPesquisa(pesquisa_id=pesquisa_id, company_id=current_user.company_id)
        db.add(config)
    config.distancia_recomendada_entre_abordagens_metros = payload.distancia_recomendada_entre_abordagens_metros
    db.commit()
    db.refresh(config)
    return config


def eventos_cobertura(
    db: Session,
    pesquisa_id: int,
    setores: Iterable[models.Setor],
    incluir_agente: bool = False,
) -> list[dict]:
    """Eventos dos setores informados, deduplicados e sem dados pessoais.

    `incluir_agente=True` (painel gerencial) acrescenta `agente_id` e
    `agente_nome`; o endpoint do agente nunca os expoe (response_model).
    """
    setor_ids = [s.id for s in setores]
    if not setor_ids:
        return []

    eventos: list[dict] = []

    # 1) Coletas concluidas com GPS inicial (ponto da abordagem).
    coletas = (
        db.query(
            models.Coleta.id,
            models.Coleta.setor_id,
            models.Coleta.data_inicio_coleta,
            func.ST_Y(models.Coleta.localizacao_inicio).label("lat"),
            func.ST_X(models.Coleta.localizacao_inicio).label("lng"),
            models.Coleta.agente_id,
            models.Usuario.nome.label("agente_nome"),
        )
        .outerjoin(models.Usuario, models.Usuario.id == models.Coleta.agente_id)
        .filter(
            models.Coleta.pesquisa_id == pesquisa_id,
            models.Coleta.setor_id.in_(setor_ids),
            models.Coleta.localizacao_inicio.is_not(None),
        )
        .all()
    )
    for coleta in coletas:
        if not _coordenada_valida(coleta.lat, coleta.lng):
            continue  # CB-B07: nunca gera evento invalido
        eventos.append(
            {
                "tipo": TIPO_COLETA,
                "server_id": coleta.id,
                "setor_id": coleta.setor_id,
                "lat": float(coleta.lat),
                "lng": float(coleta.lng),
                "accuracy": None,
                "ocorrido_em": coleta.data_inicio_coleta,
                "resultado": None,
                **({"agente_id": coleta.agente_id, "agente_nome": coleta.agente_nome} if incluir_agente else {}),
            }
        )

    # 2) Tentativas encerradas. CONCLUIDA vinculada a coleta -> ja representada
    # pela Coleta (dedup §4). EM_ANDAMENTO nunca aparece.
    nomes_agentes: dict = {}
    tentativas = (
        db.query(models.TentativaCampo)
        .filter(
            models.TentativaCampo.pesquisa_id == pesquisa_id,
            models.TentativaCampo.setor_id.in_(setor_ids),
            models.TentativaCampo.resultado != schemas.TentativaResultado.EM_ANDAMENTO.value,
        )
        .all()
    )
    if incluir_agente and tentativas:
        ids = {t.agente_id for t in tentativas}
        nomes_agentes = dict(
            db.query(models.Usuario.id, models.Usuario.nome).filter(models.Usuario.id.in_(ids)).all()
        )
    for t in tentativas:
        if t.resultado == schemas.TentativaResultado.CONCLUIDA.value and t.coleta_id is not None:
            continue
        if not _coordenada_valida(t.latitude, t.longitude):
            continue
        eventos.append(
            {
                "tipo": TIPO_TENTATIVA,
                "server_id": t.id,
                "setor_id": t.setor_id,
                "lat": float(t.latitude),
                "lng": float(t.longitude),
                "accuracy": t.precisao_metros,
                "ocorrido_em": t.capturada_em or t.iniciada_em,
                "resultado": t.resultado,
                **({"agente_id": t.agente_id, "agente_nome": nomes_agentes.get(t.agente_id)} if incluir_agente else {}),
            }
        )

    eventos.sort(key=lambda e: (e["setor_id"], e["ocorrido_em"] or datetime.min.replace(tzinfo=timezone.utc), e["tipo"], e["server_id"]))
    return eventos


def cobertura_para_agente(db: Session, pesquisa_id: int, current_user) -> dict:
    setores = setores_do_agente(db, pesquisa_id, current_user)
    distancia, configurada = distancia_recomendada(db, pesquisa_id)
    return {
        "pesquisa_id": pesquisa_id,
        "snapshot_em": datetime.now(timezone.utc),
        "distancia_recomendada_entre_abordagens_metros": distancia,
        "distancia_configurada": configurada,
        "setor_ids": [s.id for s in setores],
        "eventos": eventos_cobertura(db, pesquisa_id, setores),
    }
