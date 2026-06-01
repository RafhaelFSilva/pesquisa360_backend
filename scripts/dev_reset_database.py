"""Reset controlado da base de desenvolvimento.

Dry-run em PowerShell:

    $env:CONFIRM_DEV_RESET="RESET_DEV_DATABASE"
    $env:DEV_RESET_DRY_RUN="true"
    docker compose exec `
      -e CONFIRM_DEV_RESET="$env:CONFIRM_DEV_RESET" `
      -e DEV_RESET_DRY_RUN="$env:DEV_RESET_DRY_RUN" `
      api python scripts/dev_reset_database.py

Execucao real em PowerShell:

    $env:CONFIRM_DEV_RESET="RESET_DEV_DATABASE"
    $env:DEV_RESET_DRY_RUN="false"
    docker compose exec `
      -e CONFIRM_DEV_RESET="$env:CONFIRM_DEV_RESET" `
      -e DEV_RESET_DRY_RUN="$env:DEV_RESET_DRY_RUN" `
      api python scripts/dev_reset_database.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import func, or_


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from pesquisa360.db import models  # noqa: E402


CONFIRM_VALUE = "RESET_DEV_DATABASE"
ADMIN_COMPANY_NAME = "Pesquisa360 Admin"
ADMIN_EMAIL = "admin@pesquisa360.com"
SUPERADMIN_PROFILE_NAME = "Superadmin"
ENVIRONMENT_KEYS = ("ENV", "APP_ENV", "ENVIRONMENT", "FASTAPI_ENV")
PRODUCTION_VALUES = {"production", "prod", "prd"}


def require_confirmation() -> None:
    if os.getenv("CONFIRM_DEV_RESET") != CONFIRM_VALUE:
        raise RuntimeError(
            "Reset abortado: defina CONFIRM_DEV_RESET=RESET_DEV_DATABASE "
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
                f"Reset abortado: {key}={value!r} indica ambiente de producao."
            )

    if detected:
        return ", ".join(detected)

    return (
        "ambiente nao identificado; usar apenas em desenvolvimento/controlado"
    )


def is_dry_run() -> bool:
    return os.getenv("DEV_RESET_DRY_RUN", "true").strip().lower() != "false"


def count_query(query) -> int:
    return int(query.scalar() or 0)


def count_all(db, model) -> int:
    return count_query(db.query(func.count(model.id)))


def count_filtered(db, model, condition) -> int:
    return count_query(db.query(func.count(model.id)).filter(condition))


def fetch_context(db) -> dict:
    superadmin_profile = (
        db.query(models.Perfil)
        .filter(func.lower(models.Perfil.nome) == SUPERADMIN_PROFILE_NAME.lower())
        .first()
    )
    if not superadmin_profile:
        raise RuntimeError(
            "Perfil Superadmin nao encontrado. Aplique as migrations antes do reset."
        )

    admin_companies = (
        db.query(models.Company)
        .filter(func.lower(models.Company.name) == ADMIN_COMPANY_NAME.lower())
        .all()
    )
    if not admin_companies:
        raise RuntimeError(
            "Empresa Pesquisa360 Admin nao encontrada. Execute o bootstrap "
            "Superadmin antes do reset."
        )

    admin_company_ids = {company.id for company in admin_companies}
    admin_company_id = min(admin_company_ids)

    preserved_users = (
        db.query(models.Usuario)
        .join(models.Perfil)
        .filter(
            or_(
                func.lower(models.Perfil.nome) == SUPERADMIN_PROFILE_NAME.lower(),
                func.lower(models.Usuario.email) == ADMIN_EMAIL.lower(),
            )
        )
        .all()
    )
    if not preserved_users:
        raise RuntimeError(
            "Nenhum usuario Superadmin/admin encontrado. Reset abortado para "
            "evitar perda de acesso administrativo."
        )

    preserved_user_ids = {user.id for user in preserved_users}
    non_admin_company_ids = {
        row[0]
        for row in db.query(models.Company.id)
        .filter(~models.Company.id.in_(admin_company_ids))
        .all()
    }
    users_to_reassign = [
        user for user in preserved_users if user.company_id not in admin_company_ids
    ]

    return {
        "admin_company_id": admin_company_id,
        "admin_company_ids": admin_company_ids,
        "non_admin_company_ids": non_admin_company_ids,
        "preserved_user_ids": preserved_user_ids,
        "users_to_reassign": users_to_reassign,
    }


def build_counts(db, context: dict) -> list[tuple[str, int]]:
    non_admin_company_ids = context["non_admin_company_ids"]
    preserved_user_ids = context["preserved_user_ids"]
    admin_company_ids = context["admin_company_ids"]

    counts = [
        ("respostas", count_all(db, models.Resposta)),
        ("coletas", count_all(db, models.Coleta)),
        ("analises_salvas", count_all(db, models.AnaliseSalva)),
        ("apuracoes", count_all(db, models.Apuracao)),
        ("opcoes", count_all(db, models.Opcao)),
        ("perguntas", count_all(db, models.Pergunta)),
        ("setores", count_all(db, models.Setor)),
        ("pesquisas", count_all(db, models.Pesquisa)),
        ("projetos", count_all(db, models.Projeto)),
    ]

    if non_admin_company_ids:
        counts.extend(
            [
                (
                    "locais_votacao de empresas nao administrativas",
                    count_filtered(
                        db,
                        models.LocalVotacao,
                        models.LocalVotacao.company_id.in_(non_admin_company_ids),
                    ),
                ),
                (
                    "bairros de empresas nao administrativas",
                    count_filtered(
                        db,
                        models.Bairro,
                        models.Bairro.company_id.in_(non_admin_company_ids),
                    ),
                ),
            ]
        )
    else:
        counts.extend(
            [
                ("locais_votacao de empresas nao administrativas", 0),
                ("bairros de empresas nao administrativas", 0),
            ]
        )

    counts.extend(
        [
            (
                "usuarios nao-superadmin",
                count_filtered(
                    db,
                    models.Usuario,
                    ~models.Usuario.id.in_(preserved_user_ids),
                ),
            ),
            (
                "empresas nao administrativas",
                count_filtered(
                    db,
                    models.Company,
                    ~models.Company.id.in_(admin_company_ids),
                ),
            ),
        ]
    )
    return counts


def delete_filtered(db, model, condition) -> int:
    return db.query(model).filter(condition).delete(synchronize_session=False)


def execute_reset(db, context: dict) -> list[tuple[str, int]]:
    admin_company_id = context["admin_company_id"]
    non_admin_company_ids = context["non_admin_company_ids"]
    preserved_user_ids = context["preserved_user_ids"]
    admin_company_ids = context["admin_company_ids"]

    for user in context["users_to_reassign"]:
        user.company_id = admin_company_id
        db.add(user)

    deleted = [
        ("respostas", db.query(models.Resposta).delete(synchronize_session=False)),
        ("coletas", db.query(models.Coleta).delete(synchronize_session=False)),
        (
            "analises_salvas",
            db.query(models.AnaliseSalva).delete(synchronize_session=False),
        ),
        ("apuracoes", db.query(models.Apuracao).delete(synchronize_session=False)),
        ("opcoes", db.query(models.Opcao).delete(synchronize_session=False)),
        ("perguntas", db.query(models.Pergunta).delete(synchronize_session=False)),
        ("setores", db.query(models.Setor).delete(synchronize_session=False)),
        ("pesquisas", db.query(models.Pesquisa).delete(synchronize_session=False)),
        ("projetos", db.query(models.Projeto).delete(synchronize_session=False)),
    ]

    if non_admin_company_ids:
        deleted.extend(
            [
                (
                    "locais_votacao de empresas nao administrativas",
                    delete_filtered(
                        db,
                        models.LocalVotacao,
                        models.LocalVotacao.company_id.in_(non_admin_company_ids),
                    ),
                ),
                (
                    "bairros de empresas nao administrativas",
                    delete_filtered(
                        db,
                        models.Bairro,
                        models.Bairro.company_id.in_(non_admin_company_ids),
                    ),
                ),
            ]
        )
    else:
        deleted.extend(
            [
                ("locais_votacao de empresas nao administrativas", 0),
                ("bairros de empresas nao administrativas", 0),
            ]
        )

    deleted.extend(
        [
            (
                "usuarios nao-superadmin",
                delete_filtered(
                    db,
                    models.Usuario,
                    ~models.Usuario.id.in_(preserved_user_ids),
                ),
            ),
            (
                "empresas nao administrativas",
                delete_filtered(
                    db,
                    models.Company,
                    ~models.Company.id.in_(admin_company_ids),
                ),
            ),
        ]
    )

    db.commit()
    return deleted


def print_counts(title: str, items: list[tuple[str, int]]) -> None:
    print(title)
    for label, total in items:
        print(f"- {label}: {total}")


def main() -> int:
    try:
        require_confirmation()
        environment = guard_environment()
        dry_run = is_dry_run()

        if "DATABASE_URL" not in os.environ:
            raise RuntimeError("DATABASE_URL nao definido. Configure o ambiente.")

        from pesquisa360.db.session import SessionLocal  # noqa: WPS433

        db = SessionLocal()
        try:
            context = fetch_context(db)
            counts = build_counts(db, context)

            print("DEV RESET - DRY RUN" if dry_run else "DEV RESET - EXECUCAO REAL")
            print(f"Ambiente: {environment}")
            print("Preservando:")
            print("- Tabela perfis")
            print("- Perfil Superadmin")
            print("- Empresa Pesquisa360 Admin")
            print("- Usuario admin@pesquisa360.com")
            print("- Usuarios com perfil Superadmin")

            if context["users_to_reassign"]:
                print(
                    "- Usuarios preservados fora da empresa admin serao "
                    "vinculados a Pesquisa360 Admin na execucao real"
                )

            print_counts("Registros encontrados:", counts)

            if dry_run:
                db.rollback()
                print("Nenhuma alteracao foi aplicada.")
                print("Para executar de verdade:")
                print("CONFIRM_DEV_RESET=RESET_DEV_DATABASE")
                print("DEV_RESET_DRY_RUN=false")
                print("python scripts/dev_reset_database.py")
                return 0

            deleted = execute_reset(db, context)
            print_counts("Registros removidos:", deleted)
            print("Reset de desenvolvimento concluido.")
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
