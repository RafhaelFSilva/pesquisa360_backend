"""Importacao administrativa de coletas demonstrativas.

Esta camada nao e exposta por rota publica e nunca autentica ou representa o
agente creditado. O operador e um Super Admin real, persistido na Coleta e na
trilha de auditoria. Geocodificacao reversa e deliberadamente inexistente aqui.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from uuid import UUID, uuid5

from fastapi import HTTPException, status
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from pesquisa360 import crud, schemas
from pesquisa360.core.rbac import Papel, papel_do_usuario
from pesquisa360.db import models
from pesquisa360.question_types import normalize_question_type
from pesquisa360.services import acessos, auditoria
from pesquisa360.utils.response_normalization import normalizar_resposta_espontanea


SOURCE_MAX_LENGTH = 100


@dataclass(frozen=True)
class SyntheticRecord:
    credited_agent: models.Usuario
    record_key: str
    payload: schemas.ColetaCreate


def deterministic_client_uuid(seed_run_id: UUID, survey_id: int, record_key: str) -> UUID:
    key = str(record_key).strip()
    if not key:
        raise ValueError("record_key e obrigatorio")
    return uuid5(seed_run_id, f"pesquisa:{survey_id}:registro:{key}")


def _forbid(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message)


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


def _canonical_option(question: models.Pergunta, raw: str) -> str:
    key = normalizar_resposta_espontanea(raw)
    matches = [
        option.texto
        for option in question.opcoes
        if normalizar_resposta_espontanea(option.texto) == key
    ]
    if len(matches) != 1:
        raise _unprocessable(f"Resposta fora das opcoes da pergunta {question.id}.")
    return matches[0]


def _multiple_values(question: models.Pergunta, raw: str) -> list[str]:
    try:
        values = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _unprocessable(
            f"Pergunta {question.id} exige lista JSON de respostas."
        ) from exc
    if not isinstance(values, list) or not values:
        raise _unprocessable(f"Pergunta {question.id} exige lista nao vazia.")
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise _unprocessable(f"Pergunta {question.id} contem item invalido.")

    normalized = [normalizar_resposta_espontanea(value) for value in values]
    if len(normalized) != len(set(normalized)):
        raise _unprocessable(f"Pergunta {question.id} contem escolhas repetidas.")

    metadata = question.metadados_analiticos or {}
    minimum = int(metadata.get("min_selections", 1))
    maximum = metadata.get("max_selections")
    maximum = int(maximum) if maximum is not None else None
    if len(values) < minimum or (maximum is not None and len(values) > maximum):
        raise _unprocessable(f"Cardinalidade invalida na pergunta {question.id}.")

    if question.opcoes:
        return [_canonical_option(question, value) for value in values]
    if not question.eh_resposta_espontanea:
        raise _unprocessable(f"Pergunta {question.id} nao possui opcoes cadastradas.")
    return [value.strip() for value in values]


def _canonical_answer(question: models.Pergunta, raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise _unprocessable(f"Resposta vazia na pergunta {question.id}.")
    question_type = normalize_question_type(question.tipo_pergunta)

    if question_type == "ESCOLHA_SIMPLES":
        return _canonical_option(question, value)
    if question_type == "MULTIPLA_ESCOLHA":
        return json.dumps(
            _multiple_values(question, value), ensure_ascii=False, separators=(",", ":")
        )
    if question_type == "NUMERO":
        try:
            number = float(value.replace(",", "."))
        except ValueError as exc:
            raise _unprocessable(f"Numero invalido na pergunta {question.id}.") from exc
        if not math.isfinite(number):
            raise _unprocessable(f"Numero invalido na pergunta {question.id}.")
    elif question_type == "DATA":
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise _unprocessable(f"Data invalida na pergunta {question.id}.") from exc
    elif question_type not in {"TEXTO", "TEXTO_LONGO", "IMAGEM", "ESCALA"}:
        raise _unprocessable(f"Tipo nao suportado na pergunta {question.id}.")
    return value


def validate_synthetic_answers(
    db: Session, survey_id: int, answers: list[schemas.RespostaCreate]
) -> list[schemas.RespostaCreate]:
    questions = (
        db.query(models.Pergunta)
        .options(selectinload(models.Pergunta.opcoes))
        .filter(models.Pergunta.pesquisa_id == survey_id, models.Pergunta.ativo.is_(True))
        .order_by(models.Pergunta.ordem, models.Pergunta.id)
        .all()
    )
    if not questions:
        raise _unprocessable("A pesquisa nao possui questionario ativo.")
    by_id = {question.id: question for question in questions}
    answer_ids = [answer.pergunta_id for answer in answers]
    if len(answer_ids) != len(set(answer_ids)):
        raise _unprocessable("Pergunta duplicada no payload.")
    unknown = set(answer_ids) - set(by_id)
    if unknown:
        raise _unprocessable("Uma ou mais perguntas nao pertencem a pesquisa.")
    missing = [q.id for q in questions if q.eh_obrigatoria and q.id not in answer_ids]
    if missing:
        raise _unprocessable(f"Perguntas obrigatorias ausentes: {missing}.")

    return [
        schemas.RespostaCreate(
            pergunta_id=answer.pergunta_id,
            valor_resposta=_canonical_answer(by_id[answer.pergunta_id], answer.valor_resposta),
        )
        for answer in answers
    ]


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _same_point(element, point) -> bool:
    if element is None or point is None:
        return element is None and point is None
    stored = to_shape(element)
    return abs(stored.x - point.lon) <= 1e-9 and abs(stored.y - point.lat) <= 1e-9


def _same_payload(
    existing, payload, answers, seed_run_id, credited_agent, survey_id,
    synthetic_source, operator,
) -> bool:
    stored_answers = {item.pergunta_id: item.valor_resposta for item in existing.respostas}
    expected_answers = {item.pergunta_id: item.valor_resposta for item in answers}
    return all(
        (
            existing.is_synthetic is True,
            str(existing.seed_run_id) == str(seed_run_id),
            existing.agente_id == credited_agent.id,
            existing.synthetic_operator_id == operator.id,
            existing.synthetic_source == synthetic_source,
            existing.pesquisa_id == survey_id,
            existing.setor_id == payload.setor_id,
            _utc(existing.data_inicio_coleta) == _utc(payload.data_inicio_coleta),
            _utc(existing.data_fim_coleta) == _utc(payload.data_fim_coleta),
            existing.foi_offline is False,
            _same_point(existing.localizacao_inicio, payload.localizacao_inicio),
            _same_point(existing.localizacao_fim, payload.localizacao_fim),
            stored_answers == expected_answers,
        )
    )


def create_synthetic_coleta(
    db: Session,
    *,
    operator: models.Usuario,
    credited_agent: models.Usuario,
    survey_id: int,
    seed_run_id: UUID,
    record_key: str,
    payload: schemas.ColetaCreate,
    synthetic_source: str,
    _commit: bool = True,
    _audit: bool = True,
) -> models.Coleta:
    """Cria uma coleta sintetica validada, sem usar sessao do agente."""
    persisted_operator = db.get(models.Usuario, getattr(operator, "id", None))
    if (
        persisted_operator is None
        or papel_do_usuario(persisted_operator) != Papel.SUPERADMIN
        or persisted_operator.ativo is not True
    ):
        raise _forbid("Importacao sintetica restrita a Super Admin.")
    persisted_agent = db.get(models.Usuario, getattr(credited_agent, "id", None))
    if persisted_agent is None or persisted_agent.ativo is not True:
        raise _forbid("Agente creditado inexistente ou inativo.")
    operator = persisted_operator
    credited_agent = persisted_agent
    source = str(synthetic_source or "").strip()
    if not source or len(source) > SOURCE_MAX_LENGTH:
        raise _unprocessable("synthetic_source invalido.")
    expected_uuid = deterministic_client_uuid(seed_run_id, survey_id, record_key)
    if payload.client_uuid != expected_uuid:
        raise _unprocessable("client_uuid nao corresponde ao UUID deterministico esperado.")
    if payload.foi_offline:
        raise _unprocessable("Importacao administrativa nao pode simular envio offline.")
    if payload.localizacao_inicio is None:
        raise _unprocessable("Coleta sintetica exige localizacao inicial.")
    if payload.data_fim_coleta is None or payload.data_fim_coleta < payload.data_inicio_coleta:
        raise _unprocessable("Periodo da coleta invalido.")

    survey = (
        db.query(models.Pesquisa)
        .join(models.Projeto, models.Projeto.id == models.Pesquisa.projeto_id)
        .filter(models.Pesquisa.id == survey_id, models.Pesquisa.ativo.is_(True))
        .first()
    )
    if survey is None:
        raise HTTPException(status_code=404, detail="Pesquisa nao encontrada.")
    project = survey.projeto
    if not acessos.agente_pode_operar_projeto(db, credited_agent, project.id):
        raise _forbid("Agente creditado nao possui acesso ao projeto.")
    if payload.setor_id is not None:
        crud.validar_setor_da_coleta(db, payload.setor_id, survey_id, credited_agent)

    answers = validate_synthetic_answers(db, survey_id, payload.respostas)
    existing = (
        db.query(models.Coleta)
        .options(selectinload(models.Coleta.respostas))
        .filter(
            models.Coleta.company_id == project.company_id,
            models.Coleta.client_uuid == str(expected_uuid),
        )
        .first()
    )
    if existing is not None:
        if not _same_payload(
            existing, payload, answers, seed_run_id, credited_agent, survey_id,
            source, operator,
        ):
            raise HTTPException(status_code=409, detail="UUID sintetico em conflito.")
        if _audit:
            auditoria.registrar(
                auditoria.SYNTHETIC_COLLECTION_REPLAYED,
                user=operator,
                company_id=project.company_id,
                project_id=project.id,
                details={
                    "seed_run_id": str(seed_run_id),
                    "coleta_id": existing.id,
                    "agente_creditado_id": credited_agent.id,
                    "pesquisa_id": survey_id,
                    "quantidade": 1,
                    "resultado": "replayed",
                },
            )
        return existing

    start = payload.localizacao_inicio
    end = payload.localizacao_fim
    collection = models.Coleta(
        pesquisa_id=survey_id,
        agente_id=credited_agent.id,
        company_id=project.company_id,
        client_uuid=str(expected_uuid),
        setor_id=payload.setor_id,
        is_synthetic=True,
        seed_run_id=seed_run_id,
        synthetic_source=source,
        synthetic_operator_id=operator.id,
        foi_offline=False,
        endereco_estimado=None,
        status_sincronizacao="synthetic_admin_import",
        data_inicio_coleta=payload.data_inicio_coleta,
        data_fim_coleta=payload.data_fim_coleta,
        localizacao_inicio=from_shape(Point(start.lon, start.lat), srid=4326),
        localizacao_fim=(
            from_shape(Point(end.lon, end.lat), srid=4326) if end is not None else None
        ),
        inconformidade_localizacao=False,
    )
    db.add(collection)
    try:
        db.flush()
        for answer in answers:
            db.add(
                models.Resposta(
                    coleta_id=collection.id,
                    pergunta_id=answer.pergunta_id,
                    valor_resposta=answer.valor_resposta,
                )
            )
        db.flush()
        if _commit:
            db.commit()
    except IntegrityError:
        db.rollback()
        if not _commit:
            raise HTTPException(status_code=409, detail="UUID sintetico em conflito.")
        winner = (
            db.query(models.Coleta)
            .options(selectinload(models.Coleta.respostas))
            .filter(
                models.Coleta.company_id == project.company_id,
                models.Coleta.client_uuid == str(expected_uuid),
            )
            .first()
        )
        if winner is not None and _same_payload(
            winner, payload, answers, seed_run_id, credited_agent, survey_id,
            source, operator,
        ):
            return winner
        raise HTTPException(status_code=409, detail="UUID sintetico em conflito.")
    except Exception:
        db.rollback()
        raise
    db.refresh(collection)
    if _audit:
        if not _commit:
            raise RuntimeError("auditoria exige transacao confirmada")
        auditoria.registrar(
            auditoria.SYNTHETIC_COLLECTION_CREATED,
            user=operator,
            company_id=project.company_id,
            project_id=project.id,
            details={
                "seed_run_id": str(seed_run_id),
                "coleta_id": collection.id,
                "agente_creditado_id": credited_agent.id,
                "pesquisa_id": survey_id,
                "quantidade": 1,
                "resultado": "created",
            },
        )
    return collection


def create_synthetic_batch(
    db: Session,
    *,
    operator: models.Usuario,
    survey_id: int,
    seed_run_id: UUID,
    synthetic_source: str,
    records: list[SyntheticRecord],
) -> list[models.Coleta]:
    """Confirma o lote inteiro uma unica vez; qualquer falha remove todo o lote."""
    if not records:
        raise _unprocessable("Lote sintetico vazio.")
    created = []
    try:
        for record in records:
            created.append(create_synthetic_coleta(
                db, operator=operator, credited_agent=record.credited_agent,
                survey_id=survey_id, seed_run_id=seed_run_id,
                record_key=record.record_key, payload=record.payload,
                synthetic_source=synthetic_source, _commit=False, _audit=False,
            ))
        db.commit()
    except Exception:
        db.rollback()
        raise

    survey = db.get(models.Pesquisa, survey_id)
    project = survey.projeto
    agent_ids = sorted({item.agente_id for item in created})
    auditoria.registrar(
        auditoria.SYNTHETIC_COLLECTION_BATCH_COMPLETED,
        user=db.get(models.Usuario, operator.id),
        company_id=project.company_id,
        project_id=project.id,
        details={
            "seed_run_id": str(seed_run_id), "pesquisa_id": survey_id,
            "agentes_creditados_ids": agent_ids, "quantidade": len(created),
            "resultado": "completed",
        },
    )
    return created
