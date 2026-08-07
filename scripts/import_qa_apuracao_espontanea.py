from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import uuid
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import func
from sqlalchemy.orm import Session

from pesquisa360.db import models
from pesquisa360.db.session import SessionLocal
from pesquisa360.question_types import normalize_question_type


EXPECTED_SHA256 = "C6461082B3903AFEB1717E9ACEEBABD0A0F941D99DE7F7A981D5719846A2EA2C"
EXPECTED_HEADERS = (
    "coleta_qa_id",
    "pergunta_origem_id",
    "ordem",
    "tipo_pergunta",
    "texto_pergunta",
    "valor_resposta",
)
EXPECTED_PER_QUESTION_COUNTS = {
    33: 257,
    34: 257,
    35: 257,
    36: 257,
    41: 152,
    43: 257,
    44: 257,
    45: 257,
    49: 257,
    50: 257,
}
SPONTANEOUS_QUESTION_IDS = {41, 43, 44, 45, 49, 50}
ALLOWED_COORDINATOR_PROFILES = {"gerente", "coordenador", "superadmin", "supervisor"}
AGENT_PROFILE_NAME = "agente"
DEFAULT_PROJECT_NAME = "QA - Apuracao de Respostas Espontaneas"
DEFAULT_SURVEY_TITLE = "QA - Intencao de Votos Cutias 2026"
DEFAULT_PROJECT_DESCRIPTION = "Projeto QA isolado para validacao de apuracao espontanea."
DEFAULT_SURVEY_TYPE = "Quantitativa"
SYNC_STATUS = "sincronizado"
BASE_COLLECTION_TIME = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class CsvProfile:
    expected_hash: str
    headers: tuple[str, ...]
    row_count: int
    collection_count: int
    per_question_counts: dict[int, int]
    spontaneous_question_ids: set[int]

    @property
    def allowed_question_ids(self) -> set[int]:
        return set(self.per_question_counts)


@dataclass(frozen=True)
class CsvRow:
    coleta_qa_id: str
    pergunta_origem_id: int
    ordem: int
    tipo_pergunta: str
    texto_pergunta: str
    valor_resposta: str


DEFAULT_PROFILE = CsvProfile(
    expected_hash=EXPECTED_SHA256,
    headers=EXPECTED_HEADERS,
    row_count=2465,
    collection_count=257,
    per_question_counts=EXPECTED_PER_QUESTION_COUNTS,
    spontaneous_question_ids=SPONTANEOUS_QUESTION_IDS,
)


def compute_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _ordered_unique(values: list[str]) -> list[str]:
    return list(OrderedDict.fromkeys(values))


def validate_and_load_csv(csv_path: Path, profile: CsvProfile = DEFAULT_PROFILE) -> tuple[list[CsvRow], dict]:
    if not csv_path.exists() or not csv_path.is_file():
        raise ValueError(f"Arquivo CSV nao encontrado: {csv_path}")

    file_hash = compute_sha256(csv_path)
    if file_hash != profile.expected_hash.upper():
        raise ValueError(
            f"Hash SHA-256 divergente. Esperado {profile.expected_hash}, encontrado {file_hash}."
        )

    with csv_path.open("r", encoding="utf-8", newline="") as file_handle:
        reader = csv.DictReader(file_handle)
        header = tuple(reader.fieldnames or ())
        if header != profile.headers:
            raise ValueError(
                f"Cabecalho incorreto. Esperado {list(profile.headers)}, encontrado {list(header)}."
            )

        rows = [
            CsvRow(
                coleta_qa_id=row["coleta_qa_id"],
                pergunta_origem_id=int(row["pergunta_origem_id"]),
                ordem=int(row["ordem"]),
                tipo_pergunta=normalize_question_type(row["tipo_pergunta"]),
                texto_pergunta=row["texto_pergunta"],
                valor_resposta=row["valor_resposta"],
            )
            for row in reader
        ]

    if len(rows) != profile.row_count:
        raise ValueError(f"Quantidade de linhas divergente. Esperado {profile.row_count}, encontrado {len(rows)}.")

    collections = {row.coleta_qa_id for row in rows}
    if len(collections) != profile.collection_count:
        raise ValueError(
            f"Quantidade de coletas divergente. Esperado {profile.collection_count}, encontrado {len(collections)}."
        )

    duplicates = len(rows) - len({(row.coleta_qa_id, row.pergunta_origem_id) for row in rows})
    if duplicates:
        raise ValueError("Duplicidade detectada em coleta_qa_id + pergunta_origem_id.")

    found_question_ids = {row.pergunta_origem_id for row in rows}
    if found_question_ids != profile.allowed_question_ids:
        raise ValueError(
            f"Conjunto de pergunta_origem_id divergente. Esperado {sorted(profile.allowed_question_ids)}, "
            f"encontrado {sorted(found_question_ids)}."
        )

    forbidden = sorted(found_question_ids.intersection({30, 31, 32}))
    if forbidden:
        raise ValueError(f"Perguntas proibidas encontradas no CSV: {forbidden}.")

    per_question_counts = Counter(row.pergunta_origem_id for row in rows)
    if dict(per_question_counts) != profile.per_question_counts:
        raise ValueError(
            f"Quantidades por pergunta divergentes. Esperado {profile.per_question_counts}, "
            f"encontrado {dict(sorted(per_question_counts.items()))}."
        )

    question_shapes: dict[int, tuple[int, str, str]] = {}
    for row in rows:
        current = (row.ordem, row.tipo_pergunta, row.texto_pergunta)
        previous = question_shapes.setdefault(row.pergunta_origem_id, current)
        if previous != current:
            raise ValueError(f"Metadados inconsistentes para pergunta {row.pergunta_origem_id}.")

    summary = {
        "csv_path": str(csv_path),
        "csv_hash": file_hash,
        "header": list(header),
        "total_rows": len(rows),
        "total_collections": len(collections),
        "per_question_counts": dict(sorted(per_question_counts.items())),
        "question_ids": sorted(found_question_ids),
    }
    return rows, summary


def _build_question_specs(rows: list[CsvRow], spontaneous_question_ids: set[int]) -> list[dict]:
    grouped: dict[int, list[CsvRow]] = {}
    for row in rows:
        grouped.setdefault(row.pergunta_origem_id, []).append(row)

    question_specs = []
    for question_id in sorted(grouped, key=lambda value: grouped[value][0].ordem):
        records = grouped[question_id]
        first = records[0]
        options = []
        if question_id not in spontaneous_question_ids:
            options = _ordered_unique([record.valor_resposta for record in records])
        question_specs.append(
            {
                "source_question_id": question_id,
                "ordem": first.ordem,
                "tipo_pergunta": first.tipo_pergunta,
                "texto_pergunta": first.texto_pergunta,
                "eh_resposta_espontanea": question_id in spontaneous_question_ids,
                "eh_obrigatoria": True,
                "options": options,
            }
        )
    return question_specs


def _validate_selected_entities(
    db: Session,
    company_id: int,
    coordinator_id: int,
    agent_id: int,
) -> dict:
    company = db.query(models.Company).filter(models.Company.id == company_id).first()
    if not company:
        raise ValueError("Empresa nao encontrada.")
    if not company.is_active:
        raise ValueError("Empresa selecionada esta inativa.")

    coordinator = (
        db.query(models.Usuario)
        .join(models.Perfil, models.Perfil.id == models.Usuario.perfil_id)
        .filter(models.Usuario.id == coordinator_id)
        .first()
    )
    if not coordinator:
        raise ValueError("Coordenador nao encontrado.")
    if not coordinator.ativo or coordinator.company_id != company.id:
        raise ValueError("Coordenador invalido para a empresa selecionada.")
    coordinator_profile = ((coordinator.perfil.nome if coordinator.perfil else "") or "").strip().casefold()
    if coordinator_profile not in ALLOWED_COORDINATOR_PROFILES:
        raise ValueError("Perfil do coordenador nao permitido.")

    agent = (
        db.query(models.Usuario)
        .join(models.Perfil, models.Perfil.id == models.Usuario.perfil_id)
        .filter(models.Usuario.id == agent_id)
        .first()
    )
    if not agent:
        raise ValueError("Agente nao encontrado.")
    if not agent.ativo or agent.company_id != company.id:
        raise ValueError("Agente invalido para a empresa selecionada.")
    agent_profile = ((agent.perfil.nome if agent.perfil else "") or "").strip().casefold()
    if agent_profile != AGENT_PROFILE_NAME:
        raise ValueError("Perfil do agente selecionado deve ser Agente.")

    return {
        "company": company,
        "coordinator": coordinator,
        "agent": agent,
    }


def _ensure_target_absent(db: Session, company_id: int, project_name: str, survey_title: str) -> None:
    existing_project = (
        db.query(models.Projeto.id)
        .filter(
            models.Projeto.company_id == company_id,
            models.Projeto.nome == project_name,
        )
        .first()
    )
    if existing_project:
        raise ValueError(
            f"Projeto QA ja existente para a empresa selecionada: id {existing_project.id}."
        )

    existing_survey = (
        db.query(models.Pesquisa.id)
        .join(models.Projeto, models.Projeto.id == models.Pesquisa.projeto_id)
        .filter(
            models.Projeto.company_id == company_id,
            models.Pesquisa.titulo == survey_title,
        )
        .first()
    )
    if existing_survey:
        raise ValueError(
            f"Pesquisa QA ja existente para a empresa selecionada: id {existing_survey.id}."
        )


def _build_collection_client_uuid(company_id: int, coleta_qa_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"qa-apuracao:{company_id}:{coleta_qa_id}"))


def _create_project(
    db: Session,
    company_id: int,
    coordinator_id: int,
    project_name: str,
) -> models.Projeto:
    project = models.Projeto(
        nome=project_name,
        descricao=DEFAULT_PROJECT_DESCRIPTION,
        status="Ativo",
        data_inicio=date(2026, 8, 6),
        data_fim=None,
        coordenador_id=coordinator_id,
        company_id=company_id,
    )
    db.add(project)
    db.flush()
    return project


def _create_survey(db: Session, project_id: int, survey_title: str) -> models.Pesquisa:
    survey = models.Pesquisa(
        titulo=survey_title,
        tipo_pesquisa=DEFAULT_SURVEY_TYPE,
        ativo=True,
        projeto_id=project_id,
        cerca_eletronica=None,
        tolerancia_metros=None,
    )
    db.add(survey)
    db.flush()
    return survey


def _insert_questions(
    db: Session,
    survey_id: int,
    question_specs: list[dict],
) -> dict[int, int]:
    source_to_new_id: dict[int, int] = {}
    for spec in question_specs:
        question = models.Pergunta(
            pesquisa_id=survey_id,
            texto_pergunta=spec["texto_pergunta"],
            tipo_pergunta=spec["tipo_pergunta"],
            ordem=spec["ordem"],
            eh_obrigatoria=spec["eh_obrigatoria"],
            eh_resposta_espontanea=spec["eh_resposta_espontanea"],
            ativo=True,
        )
        db.add(question)
        db.flush()
        source_to_new_id[spec["source_question_id"]] = question.id

        for option_order, option_text in enumerate(spec["options"], start=1):
            db.add(
                models.Opcao(
                    pergunta_id=question.id,
                    texto=option_text,
                    ordem=option_order,
                    proxima_pergunta_id=None,
                )
            )
    db.flush()
    return source_to_new_id


def _insert_collections(
    db: Session,
    survey_id: int,
    company_id: int,
    agent_id: int,
    collection_ids: list[str],
) -> dict[str, int]:
    collection_map: dict[str, int] = {}
    for index, coleta_qa_id in enumerate(collection_ids):
        start_time = BASE_COLLECTION_TIME + timedelta(minutes=index * 10)
        end_time = start_time + timedelta(minutes=5)
        collection = models.Coleta(
            pesquisa_id=survey_id,
            agente_id=agent_id,
            company_id=company_id,
            client_uuid=_build_collection_client_uuid(company_id, coleta_qa_id),
            foi_offline=False,
            endereco_estimado=None,
            status_sincronizacao=SYNC_STATUS,
            data_inicio_coleta=start_time,
            data_fim_coleta=end_time,
            localizacao_inicio=None,
            localizacao_fim=None,
            inconformidade_localizacao=False,
        )
        db.add(collection)
        db.flush()
        collection_map[coleta_qa_id] = collection.id
    return collection_map


def _insert_respostas(
    db: Session,
    rows: list[CsvRow],
    collection_map: dict[str, int],
    question_map: dict[int, int],
) -> int:
    inserted = 0
    for row in rows:
        db.add(
            models.Resposta(
                coleta_id=collection_map[row.coleta_qa_id],
                pergunta_id=question_map[row.pergunta_origem_id],
                valor_resposta=row.valor_resposta,
            )
        )
        inserted += 1
    db.flush()
    return inserted


def execute_import(
    session_factory: Callable[[], Session],
    csv_path: Path,
    company_id: int,
    coordinator_id: int,
    agent_id: int,
    *,
    dry_run: bool,
    apply: bool,
    project_name: str = DEFAULT_PROJECT_NAME,
    survey_title: str = DEFAULT_SURVEY_TITLE,
    profile: CsvProfile = DEFAULT_PROFILE,
) -> dict:
    if dry_run == apply:
        raise ValueError("Informe exatamente uma das flags: --dry-run ou --apply.")

    rows, csv_summary = validate_and_load_csv(csv_path=csv_path, profile=profile)
    question_specs = _build_question_specs(rows, profile.spontaneous_question_ids)
    collection_ids = sorted({row.coleta_qa_id for row in rows}, key=lambda value: int(value))
    options_total = sum(len(spec["options"]) for spec in question_specs)

    db = session_factory()
    try:
        selected = _validate_selected_entities(
            db=db,
            company_id=company_id,
            coordinator_id=coordinator_id,
            agent_id=agent_id,
        )
        _ensure_target_absent(
            db=db,
            company_id=company_id,
            project_name=project_name,
            survey_title=survey_title,
        )

        summary = {
            "mode": "dry-run" if dry_run else "apply",
            "csv_hash": csv_summary["csv_hash"],
            "csv_path": str(csv_path),
            "total_rows": csv_summary["total_rows"],
            "total_collections": csv_summary["total_collections"],
            "total_questions": len(question_specs),
            "per_question_counts": csv_summary["per_question_counts"],
            "spontaneous_question_count": sum(
                1 for spec in question_specs if spec["eh_resposta_espontanea"]
            ),
            "categorical_question_count": sum(
                1 for spec in question_specs if not spec["eh_resposta_espontanea"]
            ),
            "selected_company": {
                "id": selected["company"].id,
                "name": selected["company"].name,
            },
            "selected_coordinator": {
                "id": selected["coordinator"].id,
                "name": selected["coordinator"].nome,
                "email": selected["coordinator"].email,
                "profile": selected["coordinator"].perfil.nome if selected["coordinator"].perfil else None,
            },
            "selected_agent": {
                "id": selected["agent"].id,
                "name": selected["agent"].nome,
                "email": selected["agent"].email,
                "profile": selected["agent"].perfil.nome if selected["agent"].perfil else None,
            },
            "project_name": project_name,
            "survey_title": survey_title,
            "planned_inserts": {
                "projects": 1,
                "surveys": 1,
                "questions": len(question_specs),
                "options": options_total,
                "collections": len(collection_ids),
                "responses": len(rows),
                "categories": 0,
                "mappings": 0,
            },
        }

        if dry_run:
            return summary

        try:
            project = _create_project(
                db=db,
                company_id=company_id,
                coordinator_id=coordinator_id,
                project_name=project_name,
            )
            survey = _create_survey(db=db, project_id=project.id, survey_title=survey_title)
            question_map = _insert_questions(db=db, survey_id=survey.id, question_specs=question_specs)
            collection_map = _insert_collections(
                db=db,
                survey_id=survey.id,
                company_id=company_id,
                agent_id=agent_id,
                collection_ids=collection_ids,
            )
            responses_inserted = _insert_respostas(
                db=db,
                rows=rows,
                collection_map=collection_map,
                question_map=question_map,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise

        summary.update(
            {
                "project_id": project.id,
                "survey_id": survey.id,
                "question_id_map": dict(sorted(question_map.items())),
                "collections_created": len(collection_map),
                "responses_created": responses_inserted,
            }
        )
        return summary
    finally:
        db.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Importador controlado da estrutura QA de apuracao espontanea.")
    parser.add_argument("--csv", required=True, help="Caminho do CSV sanitizado.")
    parser.add_argument("--company-id", required=True, type=int)
    parser.add_argument("--coordinator-id", required=True, type=int)
    parser.add_argument("--agent-id", required=True, type=int)
    parser.add_argument("--project-name", default=DEFAULT_PROJECT_NAME)
    parser.add_argument("--survey-title", default=DEFAULT_SURVEY_TITLE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = execute_import(
            session_factory=SessionLocal,
            csv_path=Path(args.csv),
            company_id=args.company_id,
            coordinator_id=args.coordinator_id,
            agent_id=args.agent_id,
            dry_run=args.dry_run,
            apply=args.apply,
            project_name=args.project_name,
            survey_title=args.survey_title,
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
