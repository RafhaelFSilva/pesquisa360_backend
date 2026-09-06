"""Seed QA controlado, deterministico e idempotente.

Uso esperado em PowerShell (procedimento completo em doc/22-qa-local.md):

    $env:CONFIRM_QA_SEED="SEED_QA_DATASET"
    $env:QA_DEFAULT_PASSWORD="<senha temporaria escolhida por voce>"
    docker compose exec `
      -e CONFIRM_QA_SEED="$env:CONFIRM_QA_SEED" `
      -e QA_DEFAULT_PASSWORD="$env:QA_DEFAULT_PASSWORD" `
      api python scripts/seed_qa_dataset.py

Regras:
- Sem senha embutida: QA_DEFAULT_PASSWORD e obrigatoria (nunca e impressa).
- Dados 100% sinteticos (empresas, usuarios, setores, abordagens).
- Reexecutar nao duplica nada: cada entidade e localizada pela sua chave
  natural (nome, e-mail, titulo, client_uuid) e atualizada no lugar.
- Nenhum atalho de multitenancy: usuarios, projeto e setores respeitam as
  mesmas regras de company_id/RBAC que a API aplica.

QA-BE-001: o seed gravava CNPJ COM MASCARA (18 caracteres) em
`companies.cnpj`, que e VARCHAR(14) desde a migration e7f9a2b3c4d5 e cujo
conteudo passa pelo validador `schemas.normalize_cnpj` (14 digitos + digitos
verificadores). Os valores abaixo sao sinteticos, sem mascara e validados pelo
MESMO normalizador da API antes de persistir.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from pesquisa360 import schemas  # noqa: E402
from pesquisa360.core.security import get_password_hash  # noqa: E402
from pesquisa360.db import models  # noqa: E402


CONFIRM_VALUE = "SEED_QA_DATASET"
PASSWORD_ENV = "QA_DEFAULT_PASSWORD"
ENVIRONMENT_KEYS = ("ENV", "APP_ENV", "ENVIRONMENT", "FASTAPI_ENV")
PRODUCTION_VALUES = {"production", "prod", "prd"}

# Datas fixas: rodar o seed hoje ou daqui a um mes produz o mesmo estado.
SEED_REFERENCE_DATE = date(2026, 9, 1)
SEED_REFERENCE_DATETIME = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

# CNPJs SINTETICOS de QA: sequencias didaticas com digitos verificadores
# validos, sem mascara (o schema real e VARCHAR(14) e o validador da API so
# devolve digitos). Nao representam nenhum cadastro real.
COMPANIES = [
    {"name": "Empresa QA A", "cnpj": "12345678000195"},
    {"name": "Empresa QA B", "cnpj": "98765432000198"},
]

# Perfis canonicos do RBAC (core/rbac.py). Agente e Gerente nao sao semeados
# por migration -- um banco recem-migrado so tem Superadmin, Coordenador,
# Supervisor e Cliente --, entao o seed os cria se faltarem, pelo mesmo
# padrao get-or-create do bootstrap do Superadmin. Nenhum papel novo.
PROFILES = {
    "Gerente": "Dono do tenant: gestao completa, usuarios e projetos",
    "Agente": "Campo: missao e sincronizacao (Mobile)",
}

# Gerente possui CAMPO_MONITORAR (painel do Supervisor); Agente NAO possui.
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
    {
        "text": "QA 09 - Avaliacao geral",
        "type": "ESCALA",
        "required": False,
        "options": ["0", "1", "2", "3", "4", "5"],
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

# Abordagens de campo (tentativas_campo) para o painel de controle de campo
# ter eventos sem depender de coleta real. client_uuid deterministico
# (uuid5 sobre um namespace fixo): a chave (company_id, client_uuid) garante
# idempotencia. Pontos dentro dos poligonos dos setores acima.
TENTATIVAS_NAMESPACE = uuid.UUID("6f2c0d1e-9a7b-4c3d-8e5f-0a1b2c3d4e5f")
TENTATIVAS = [
    {
        "key": "tentativa-a1-01",
        "sector": "Setor QA A1",
        "agent_email": "agente.qa.a1@pesquisa360.com",
        "resultado": schemas.TentativaResultado.RECUSA.value,
        "motivo": "NAO_QUIS_PARTICIPAR",
        "lat": 0.0420,
        "lng": -51.1270,
        "offset_minutes": 0,
    },
    {
        "key": "tentativa-a1-02",
        "sector": "Setor QA A1",
        "agent_email": "agente.qa.a1@pesquisa360.com",
        "resultado": schemas.TentativaResultado.NAO_ELEGIVEL.value,
        "motivo": "FORA_DO_PERFIL",
        "lat": 0.0415,
        "lng": -51.1265,
        "offset_minutes": 30,
    },
    {
        "key": "tentativa-a2-01",
        "sector": "Setor QA A2",
        "agent_email": "agente.qa.a2@pesquisa360.com",
        "resultado": schemas.TentativaResultado.RECUSA.value,
        "motivo": None,
        "lat": 0.0378,
        "lng": -51.1228,
        "offset_minutes": 60,
    },
]


def tentativa_client_uuid(key: str) -> str:
    return str(uuid.uuid5(TENTATIVAS_NAMESPACE, f"pesquisa360-qa/{key}"))


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
    password = (os.getenv(PASSWORD_ENV) or "").strip()
    if not password:
        raise RuntimeError(
            f"{PASSWORD_ENV} ausente. Informe a senha temporaria dos usuarios QA "
            "por variavel de ambiente; o seed nao possui senha embutida."
        )
    return password


def get_or_create_profile(db, name: str, description: str):
    profile = (
        db.query(models.Perfil)
        .filter(func.lower(models.Perfil.nome) == name.lower())
        .first()
    )
    if not profile:
        profile = models.Perfil(nome=name, descricao=description)
        db.add(profile)
        db.flush()
    return profile


def seed_cnpj(value: str | None) -> str | None:
    """Mesma normalizacao/validacao da API; garante que cabe na coluna real."""
    cnpj = schemas.normalize_cnpj(value)
    limit = models.Company.cnpj.type.length
    if cnpj is not None and limit is not None and len(cnpj) > limit:
        raise RuntimeError(
            f"CNPJ do seed excede companies.cnpj VARCHAR({limit}): {len(cnpj)} chars."
        )
    return cnpj


def upsert_company(db, data: dict):
    cnpj = seed_cnpj(data["cnpj"])
    company = (
        db.query(models.Company)
        .filter(func.lower(models.Company.name) == data["name"].lower())
        .first()
    )
    if cnpj is not None:
        conflict = (
            db.query(models.Company).filter(models.Company.cnpj == cnpj).first()
        )
        if conflict is not None and (company is None or conflict.id != company.id):
            raise RuntimeError(
                f"CNPJ QA de {data['name']!r} ja pertence a outra empresa "
                f"(id {conflict.id}). Ajuste o banco antes de continuar."
            )

    if not company:
        company = models.Company(name=data["name"])
        db.add(company)

    company.cnpj = cnpj
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
    project.data_inicio = SEED_REFERENCE_DATE
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


def upsert_sector_agent(db, sector, agent) -> None:
    """Atribuicao N:N (setor_agentes), a forma atual; agente_id fica como legado."""
    link = (
        db.query(models.SetorAgente)
        .filter(
            models.SetorAgente.setor_id == sector.id,
            models.SetorAgente.agente_id == agent.id,
        )
        .first()
    )
    if not link:
        link = models.SetorAgente(setor_id=sector.id, agente_id=agent.id)
        db.add(link)
    link.ativo = True
    db.flush()


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
        upsert_sector_agent(db, sector, agent)

        if sector.agente_id != agent.id:
            raise RuntimeError(f"Setor {sector.nome} ficou sem agente QA esperado.")
        if sector.tolerancia != data["tolerancia"]:
            raise RuntimeError(f"Setor {sector.nome} ficou sem tolerancia esperada.")

        result.append(sector)

    return result


def upsert_tentativas(db, survey, sectors_by_name: dict, users_by_email: dict, qa_company):
    result = []
    for data in TENTATIVAS:
        agent = users_by_email[data["agent_email"]]
        sector = sectors_by_name[data["sector"]]
        client_uuid = tentativa_client_uuid(data["key"])
        tentativa = (
            db.query(models.TentativaCampo)
            .filter(
                models.TentativaCampo.company_id == qa_company.id,
                models.TentativaCampo.client_uuid == client_uuid,
            )
            .first()
        )
        if not tentativa:
            tentativa = models.TentativaCampo(
                client_uuid=client_uuid, company_id=qa_company.id
            )
            db.add(tentativa)

        started = SEED_REFERENCE_DATETIME + timedelta(minutes=data["offset_minutes"])
        tentativa.pesquisa_id = survey.id
        tentativa.setor_id = sector.id
        tentativa.agente_id = agent.id
        tentativa.iniciada_em = started
        tentativa.encerrada_em = started + timedelta(minutes=5)
        tentativa.latitude = data["lat"]
        tentativa.longitude = data["lng"]
        tentativa.precisao_metros = 8.0
        tentativa.capturada_em = started
        tentativa.resultado = data["resultado"]
        tentativa.motivo = data["motivo"]
        tentativa.observacao = "Abordagem sintetica de QA."
        tentativa.coleta_id = None
        db.flush()
        result.append(tentativa)

    return result


def run_seed(db, password: str) -> dict:
    """Aplica o cenario QA na sessao informada e faz commit. Reexecutavel."""
    profiles = {
        name: get_or_create_profile(db, name, description)
        for name, description in PROFILES.items()
    }
    companies = {data["name"]: upsert_company(db, data) for data in COMPANIES}
    users = [upsert_user(db, data, companies, profiles, password) for data in USERS]
    users_by_email = {user.email: user for user in users}
    qa_company = companies["Empresa QA A"]
    project = upsert_project(
        db, qa_company, users_by_email["gerente.qa.a@pesquisa360.com"]
    )
    survey = upsert_survey(db, project)
    questions = upsert_questions(db, survey)
    sectors = upsert_sectors(db, survey, users_by_email, qa_company)
    sectors_by_name = {sector.nome: sector for sector in sectors}
    tentativas = upsert_tentativas(db, survey, sectors_by_name, users_by_email, qa_company)

    db.commit()
    return {
        "companies": list(companies.values()),
        "users": users,
        "project": project,
        "survey": survey,
        "questions": questions,
        "sectors": sectors,
        "tentativas": tentativas,
    }


def print_summary(environment: str, summary: dict) -> None:
    print("Seed QA concluido.")
    print(f"Ambiente: {environment}")
    print("Empresas:")
    for company in summary["companies"]:
        print(f"- {company.name}: id {company.id}")
    print("Usuarios (senha: nao exibida):")
    for user in summary["users"]:
        print(f"- {user.email} ({user.perfil.nome})")
    print("Projeto:")
    print(f"- {summary['project'].nome}: id {summary['project'].id}")
    print("Pesquisa:")
    print(f"- {summary['survey'].titulo}: id {summary['survey'].id}")
    print("Perguntas criadas/atualizadas:")
    for question in summary["questions"]:
        print(f"- {question.texto_pergunta}")
    print("Setores criados/atualizados:")
    for sector in summary["sectors"]:
        agent_email = sector.agente.email if sector.agente else "N/A"
        print(
            f"- {sector.nome}: agente {agent_email}, "
            f"meta {sector.meta}, tolerancia {sector.tolerancia}m"
        )
    print(f"Abordagens de campo (tentativas): {len(summary['tentativas'])}")


def main() -> int:
    try:
        require_confirmation()
        environment = guard_environment()
        password = get_seed_password()

        if "DATABASE_URL" not in os.environ:
            raise RuntimeError("DATABASE_URL nao definido. Configure o ambiente.")

        from pesquisa360.db.session import SessionLocal  # noqa: WPS433

        db = SessionLocal()
        try:
            summary = run_seed(db, password)
            print_summary(environment, summary)
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
