"""Bootstrap seguro do primeiro Superadmin.

Uso esperado em PowerShell, apos aplicar a migration:

    alembic upgrade head
    $env:CONFIRM_BOOTSTRAP_SUPERADMIN="YES"
    $env:SUPERADMIN_EMAIL="admin@pesquisa360.com"
    $env:SUPERADMIN_NAME="Superadmin Pesquisa360"
    $env:SUPERADMIN_PASSWORD="trocar-esta-senha"
    python scripts/bootstrap_superadmin.py

SUPERADMIN_COMPANY_NAME e opcional. Padrao: "Pesquisa360 Admin".
Tambem e possivel informar --email, --name, --password e --company-name.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from pesquisa360.core.security import get_password_hash  # noqa: E402
from pesquisa360.db import models  # noqa: E402


SUPERADMIN_PROFILE_NAME = "Superadmin"
SUPERADMIN_PROFILE_DESCRIPTION = "Administracao global da plataforma SaaS"
DEFAULT_ADMIN_COMPANY_NAME = "Pesquisa360 Admin"
PRODUCTION_VALUES = {"production", "prod"}
ENVIRONMENT_KEYS = ("ENV", "APP_ENV", "ENVIRONMENT", "FASTAPI_ENV")


def _env_or_arg(value: Optional[str], env_name: str) -> Optional[str]:
    candidate = value if value is not None else os.getenv(env_name)
    if candidate is None:
        return None
    candidate = candidate.strip()
    return candidate or None


def _require_confirmation() -> None:
    if os.getenv("CONFIRM_BOOTSTRAP_SUPERADMIN") != "YES":
        raise RuntimeError(
            "Bootstrap abortado: defina CONFIRM_BOOTSTRAP_SUPERADMIN=YES "
            "para confirmar explicitamente esta operacao."
        )


def _guard_production_environment() -> None:
    found_environment = False
    for key in ENVIRONMENT_KEYS:
        value = os.getenv(key)
        if value is None:
            continue
        found_environment = True
        if value.strip().lower() in PRODUCTION_VALUES:
            raise RuntimeError(
                f"Bootstrap abortado: {key}={value!r} indica ambiente de producao."
            )

    if not found_environment:
        print(
            "Aviso: nenhuma variavel ENV/APP_ENV/ENVIRONMENT/FASTAPI_ENV foi "
            "detectada. Use este script apenas em desenvolvimento/controlado."
        )


def _get_or_create_superadmin_profile(db):
    profile = (
        db.query(models.Perfil)
        .filter(func.lower(models.Perfil.nome) == SUPERADMIN_PROFILE_NAME.lower())
        .first()
    )
    if profile:
        return profile

    profile = models.Perfil(
        nome=SUPERADMIN_PROFILE_NAME,
        descricao=SUPERADMIN_PROFILE_DESCRIPTION,
    )
    db.add(profile)
    db.flush()
    return profile


def _get_or_create_admin_company(db, company_name: str):
    company = (
        db.query(models.Company)
        .filter(func.lower(models.Company.name) == company_name.lower())
        .first()
    )
    if company:
        if not company.is_active:
            company.is_active = True
            db.add(company)
            db.flush()
        return company

    company = models.Company(name=company_name, is_active=True)
    db.add(company)
    db.flush()
    return company


def _get_user_by_email(db, email: str):
    return (
        db.query(models.Usuario)
        .filter(func.lower(models.Usuario.email) == email.lower())
        .first()
    )


def _company_exists(db, company_id: Optional[int]) -> bool:
    if company_id is None:
        return False
    return (
        db.query(models.Company.id)
        .filter(models.Company.id == company_id)
        .first()
        is not None
    )


def bootstrap_superadmin(email: str, name: str, password: Optional[str], company_name: str) -> str:
    if "DATABASE_URL" not in os.environ:
        raise RuntimeError("DATABASE_URL nao definido. Configure o ambiente do backend.")

    from pesquisa360.db.session import SessionLocal  # noqa: WPS433

    db = SessionLocal()
    try:
        profile = _get_or_create_superadmin_profile(db)
        admin_company = _get_or_create_admin_company(db, company_name)
        user = _get_user_by_email(db, email)

        if user:
            user.perfil_id = profile.id
            user.ativo = True
            user.nome = name
            if not _company_exists(db, user.company_id):
                user.company_id = admin_company.id
            if password:
                user.senha_hash = get_password_hash(password)
            db.add(user)
            action = "Usuario existente promovido/atualizado para Superadmin."
        else:
            if not password:
                raise RuntimeError(
                    "SUPERADMIN_PASSWORD e obrigatorio para criar um novo usuario."
                )
            user = models.Usuario(
                email=email,
                nome=name,
                senha_hash=get_password_hash(password),
                ativo=True,
                perfil_id=profile.id,
                company_id=admin_company.id,
            )
            db.add(user)
            action = "Novo usuario Superadmin criado."

        db.commit()
        return action
    except SQLAlchemyError as exc:
        db.rollback()
        raise RuntimeError(
            "Falha ao acessar as tabelas esperadas. Verifique a conexao e aplique "
            "as migrations antes de rodar este script: alembic upgrade head."
        ) from exc
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cria ou promove um usuario para o perfil Superadmin."
    )
    parser.add_argument("--email", help="E-mail do Superadmin.")
    parser.add_argument("--name", help="Nome do Superadmin.")
    parser.add_argument("--password", help="Senha inicial ou nova senha.")
    parser.add_argument("--company-name", help="Nome da empresa administrativa.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        _require_confirmation()
        _guard_production_environment()

        email = _env_or_arg(args.email, "SUPERADMIN_EMAIL")
        name = _env_or_arg(args.name, "SUPERADMIN_NAME")
        password = _env_or_arg(args.password, "SUPERADMIN_PASSWORD")
        company_name = (
            _env_or_arg(args.company_name, "SUPERADMIN_COMPANY_NAME")
            or DEFAULT_ADMIN_COMPANY_NAME
        )

        if not email:
            raise RuntimeError("Informe SUPERADMIN_EMAIL ou --email.")
        if not name:
            raise RuntimeError("Informe SUPERADMIN_NAME ou --name.")

        action = bootstrap_superadmin(email, name, password, company_name)
        print(action)
        print(f"E-mail: {email}")
        print(f"Empresa administrativa: {company_name}")
        print("Senha: nao exibida.")
        return 0
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
