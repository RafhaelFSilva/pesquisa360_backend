"""Seed QA controlado e idempotente.

Uso esperado em PowerShell:

    $env:CONFIRM_QA_SEED="SEED_QA_DATASET"
    $env:QA_DEFAULT_PASSWORD="uma-senha-temporaria"
    docker compose exec `
      -e CONFIRM_QA_SEED="$env:CONFIRM_QA_SEED" `
      -e QA_DEFAULT_PASSWORD="$env:QA_DEFAULT_PASSWORD" `
      api python scripts/seed_qa_dataset.py
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import func


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from pesquisa360.core.security import get_password_hash  # noqa: E402
from pesquisa360.db import models  # noqa: E402


CONFIRM_VALUE = "SEED_QA_DATASET"
DEFAULT_PASSWORD = "Qa@123456"
ENVIRONMENT_KEYS = ("ENV", "APP_ENV", "ENVIRONMENT", "FASTAPI_ENV")
PRODUCTION_VALUES = {"production", "prod", "prd"}

COMPANIES = [
    {"name": "Empresa QA A", "cnpj": "11.111.111/0001-11"},
    {"name": "Empresa QA B", "cnpj": "22.222.222/0001-22"},
]

USERS = [
    {
        "email": "gerente.qa.a@pesquisa360.com",
        "name": "Gerente QA A",
        "profile": "Gerente",
        "company": "Empresa QA A",
    },
    {
        "email": "agente.qa.a1@pesquisa360.com",
        "name": "Agente QA A1",
        "profile": "Agente",
        "company": "Empresa QA A",
    },
    {
        "email": "agente.qa.a2@pesquisa360.com",
        "name": "Agente QA A2",
        "profile": "Agente",
        "company": "Empresa QA A",
    },
    {
        "email": "gerente.qa.b@pesquisa360.com",
        "name": "Gerente QA B",
        "profile": "Gerente",
        "company": "Empresa QA B",
    },
    {
        "email": "agente.qa.b1@pesquisa360.com",
        "name": "Agente QA B1",
        "profile": "Agente",
        "company": "Empresa QA B",
    },
]

PROJECT_NAME = "Projeto QA Mobile/Web"
SURVEY_TITLE = "Pesquisa QA Todos os Tipos"

QUESTIONS = [
    {
        "text": "QA 01 - Nome do entrevistado",
        "type": "TEXTO",
        "required": True,
        "options": [],
    },
    {
        "text": "QA 02 - Idade",
        "type": "NUMERO",
        "required": True,
        "options": [],
    },
    {
        "text": "QA 03 - Escolha simples",
        "type": "ESCOLHA_SIMPLES",
        "required": True,
        "options": ["Opcao A", "Opcao B", "Opcao C"],
    },
    {
        "text": "QA 04 - Multipla escolha",
        "type": "MULTIPLA_ESCOLHA",
        "required": False,
        "options": ["Alternativa 1", "Alternativa 2", "Alternativa 3"],
    },
    {
        "text": "QA 05 - Data de referencia",
        "type": "DATA",
        "required": False,
        "options": [],
    },
    {
        "text": "QA 06 - Registro fotografico",
        "type": "IMAGEM",
        "required": False,
        "options": [],
    },
    {
        "text": "QA 07 - Observacoes",
        "type": "TEXTO_LONGO",
        "required": False,
        "options": [],
    },
    {
        "text": "QA 08 - Satisfacao",
        "type": "ESCOLHA_SIMPLES",
        "required": False,
        "options": ["Satisfeito", "Neutro", "Insatisfeito"],
    },
]

GLOBAL_GEOFENCE_WKT = (
    "POLYGON(("
    "-51.1300 0.0450, "
    "-51.1200 0.0450, "
    "-51.1200 0.0350, "
    "-51.1300 0.0350, "
    "-51.1300 0.0450"
    "))"
)

SECTORS = [
    {
        "name": "Setor QA A1",
        "agent_email": "agente.qa.a1@pesquisa360.com",
        "meta": 5,
        "tolerancia": 50,
        "wkt": (
            "POLYGON(("
            "-51.1290 0.0440, "
            "-51.1250 0.0440, "
            "-51.1250 0.0400, "
            "-51.1290 0.0400, "
            "-51.1290 0.0440"
            "))"
        ),
    },
    {
        "name": "Setor QA A2",
        "agent_email": "agente.qa.a2@pesquisa360.com",
        "meta": 5,
        "tolerancia": 50,
        "wkt": (
            "POLYGON(("
            "-51.1245 0.0395, "
            "-51.1210 0.0395, "
            "-51.1210 0.0360, "
            "-51.1245 0.0360, "
            "-51.1245 0.0395"
            "))"
        ),
    },
]


def require_confirmation() -> None:
    if os.getenv("CONFIRM_QA_SEED") != CONFIRM_VALUE:
        raise RuntimeError(
            "Seed abortado: defina CONFIRM_QA_SEED=SEED_QA_DATASET "
            "para confirmar explicitamente esta operacao."
        )


def guard_environment() -> str:
    detected = []
    for key in ENVIRONMENT_KEYS:
        value = os.getenv(key)
        if value is None:
            continue

        normalized = value.strip().lower()
        detected.append(f"{key}={value}")
        if normalized in PRODUCTION_VALUES:
            raise RuntimeError(
                f"Seed abortado: {key}={value!r} indica ambiente de producao."
            )

    if detected:
        return ", ".join(detected)

    return "ambiente nao identificado; usar apenas em desenvolvimento/controlado"


def get_seed_password() -> str:
    password = os.getenv("QA_DEFAULT_PASSWORD")
    if password:
        return password

    print("QA_DEFAULT_PASSWORD ausente.")
    print("Usando senha temporaria de desenvolvimento: Qa@123456")
    print("Use apenas em ambiente local.")
    return DEFAULT_PASSWORD


def get_profile(db, name: str):
    profile = (
        db.query(models.Perfil)
        .filter(func.lower(models.Perfil.nome) == name.lower())
        .first()
    )
    if not profile:
        raise RuntimeError(f"Perfil obrigatorio nao encontrado: {name}")
    return profile


def upsert_company(db, data: dict):
    company = (
        db.query(models.Company)
        .filter(func.lower(models.Company.name) == data["name"].lower())
        .first()
    )
    if not company:
        company = models.Company(name=data["name"])
        db.add(company)

    company.cnpj = data["cnpj"]
    company.is_active = True
    db.flush()
    return company


def upsert_user(db, data: dict, companies: dict, profiles: dict, password: str):
    user = (
        db.query(models.Usuario)
        .filter(func.lower(models.Usuario.email) == data["email"].lower())
        .first()
    )
    if not user:
        user = models.Usuario(email=data["email"], senha_hash=get_password_hash(password))
        db.add(user)

    user.nome = data["name"]
    user.ativo = True
    user.perfil_id = profiles[data["profile"]].id
    user.company_id = companies[data["company"]].id
    user.senha_hash = get_password_hash(password)
    db.flush()
    return user


def upsert_project(db, company, coordinator):
    project = (
        db.query(models.Projeto)
        .filter(
            models.Projeto.company_id == company.id,
            models.Projeto.nome == PROJECT_NAME,
        )
        .first()
    )
    if not project:
        project = models.Projeto(nome=PROJECT_NAME, company_id=company.id)
        db.add(project)

    project.descricao = "Cenario QA para validacao web/mobile."
    project.status = "Ativo"
    project.data_inicio = date.today()
    project.data_fim = None
    project.coordenador_id = coordinator.id
    db.flush()
    return project


def upsert_survey(db, project):
    survey = (
        db.query(models.Pesquisa)
        .filter(
            models.Pesquisa.projeto_id == project.id,
            models.Pesquisa.titulo == SURVEY_TITLE,
        )
        .first()
    )
    if not survey:
        survey = models.Pesquisa(titulo=SURVEY_TITLE, projeto_id=project.id)
        db.add(survey)

    survey.tipo_pesquisa = "Quantitativa"
    survey.ativo = True
    survey.cerca_eletronica = func.ST_GeomFromText(GLOBAL_GEOFENCE_WKT, 4326)
    survey.tolerancia_metros = 50
    db.flush()
    return survey


def upsert_options(db, question, options: list[str]) -> None:
    for order, text in enumerate(options, start=1):
        option = (
            db.query(models.Opcao)
            .filter(
                models.Opcao.pergunta_id == question.id,
                models.Opcao.texto == text,
            )
            .first()
        )
        if not option:
            option = models.Opcao(pergunta_id=question.id, texto=text)
            db.add(option)

        option.ordem = order
        option.proxima_pergunta_id = None


def upsert_questions(db, survey):
    result = []
    for order, data in enumerate(QUESTIONS, start=1):
        question = (
            db.query(models.Pergunta)
            .filter(
                models.Pergunta.pesquisa_id == survey.id,
                models.Pergunta.texto_pergunta == data["text"],
            )
            .first()
        )
        if not question:
            question = models.Pergunta(
                pesquisa_id=survey.id,
                texto_pergunta=data["text"],
            )
            db.add(question)

        question.tipo_pergunta = data["type"]
        question.ordem = order
        question.eh_obrigatoria = data["required"]
        question.ativo = True
        db.flush()
        upsert_options(db, question, data["options"])
        result.append(question)

    db.flush()
    return result


def upsert_sectors(db, survey, users_by_email: dict, qa_company):
    result = []
    for data in SECTORS:
        agent = users_by_email[data["agent_email"]]
        if agent.company_id != qa_company.id:
            raise RuntimeError(
                f"Agente {agent.email} nao pertence a {qa_company.name}."
            )

        sector = (
            db.query(models.Setor)
            .filter(
                models.Setor.pesquisa_id == survey.id,
                models.Setor.nome == data["name"],
            )
            .first()
        )
        if not sector:
            sector = models.Setor(pesquisa_id=survey.id, nome=data["name"])
            db.add(sector)

        sector.meta = data["meta"]
        sector.tolerancia = data["tolerancia"]
        sector.agente_id = agent.id
        sector.agente = agent
        sector.geometria = func.ST_GeomFromText(data["wkt"], 4326)
        db.flush()

        if sector.agente_id != agent.id:
            raise RuntimeError(f"Setor {sector.nome} ficou sem agente QA esperado.")
        if sector.tolerancia != data["tolerancia"]:
            raise RuntimeError(f"Setor {sector.nome} ficou sem tolerancia esperada.")

        result.append(sector)

    return result


def main() -> int:
    try:
        require_confirmation()
        environment = guard_environment()

        if "DATABASE_URL" not in os.environ:
            raise RuntimeError("DATABASE_URL nao definido. Configure o ambiente.")

        from pesquisa360.db.session import SessionLocal  # noqa: WPS433

        db = SessionLocal()
        try:
            password = get_seed_password()
            profiles = {
                "Agente": get_profile(db, "Agente"),
                "Gerente": get_profile(db, "Gerente"),
            }
            companies = {data["name"]: upsert_company(db, data) for data in COMPANIES}
            users = [
                upsert_user(db, data, companies, profiles, password) for data in USERS
            ]
            users_by_email = {user.email: user for user in users}
            project = upsert_project(
                db,
                companies["Empresa QA A"],
                users_by_email["gerente.qa.a@pesquisa360.com"],
            )
            survey = upsert_survey(db, project)
            questions = upsert_questions(db, survey)
            sectors = upsert_sectors(
                db,
                survey,
                users_by_email,
                companies["Empresa QA A"],
            )

            db.commit()

            print("Seed QA concluido.")
            print(f"Ambiente: {environment}")
            print("Empresas:")
            for company in companies.values():
                print(f"- {company.name}: id {company.id}")
            print("Usuarios:")
            for user in users:
                print(f"- {user.email}")
            print("Projeto:")
            print(f"- {project.nome}")
            print("Pesquisa:")
            print(f"- {survey.titulo}")
            print("Perguntas criadas/atualizadas:")
            for question in questions:
                print(f"- {question.texto_pergunta}")
            print("Setores criados/atualizados:")
            for sector in sectors:
                agent_email = sector.agente.email if sector.agente else "N/A"
                print(
                    f"- {sector.nome}: agente {agent_email}, "
                    f"tolerancia {sector.tolerancia}m"
                )
            return 0
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
