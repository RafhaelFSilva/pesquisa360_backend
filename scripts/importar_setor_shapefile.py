"""Importacao administrativa de setor a partir de Shapefile."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, load_only


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from pesquisa360.db import models  # noqa: E402
from pesquisa360.services.setor_shapefile_import import (  # noqa: E402
    ImportedSectorGeometry,
    MultipleFeaturesError,
    ShapefileImportError,
    load_sector_geometry,
)
from pesquisa360.schemas import FinalidadeSetor  # noqa: E402


IMPORT_DIR = ROOT_DIR / "data" / "imports" / "setores"
ADMIN_PROFILES = ("gerente", "superadmin")
AGENT_PROFILE = "agente"


class ImportCancelled(Exception):
    """Cancelamento solicitado pelo operador."""


@dataclass(frozen=True)
class ImportContext:
    current_user: models.Usuario
    project: models.Projeto
    survey: models.Pesquisa
    agent: models.Usuario | None


def normalize_sector_name(value: str) -> str:
    """Remove espacos externos, colapsa espacos internos e ignora caixa."""
    return " ".join(value.split()).casefold()


def validate_meta(value: object) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("Cota/meta deve ser um numero inteiro positivo.") from exc
    if parsed <= 0:
        raise ValueError("Cota/meta deve ser um numero inteiro positivo.")
    return parsed


def validate_tolerance(value: object) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("Tolerancia deve ser um numero inteiro maior ou igual a zero.") from exc
    if parsed < 0:
        raise ValueError("Tolerancia deve ser um numero inteiro maior ou igual a zero.")
    return parsed


def normalize_finalidade(value: object) -> FinalidadeSetor:
    normalized = str(value).strip().upper()
    try:
        return FinalidadeSetor(normalized)
    except ValueError as exc:
        raise ValueError("Finalidade invalida. Use OPERACAO, RELATORIO ou AMBOS.") from exc


def finalidade_is_operational(finalidade: FinalidadeSetor) -> bool:
    return finalidade in {FinalidadeSetor.OPERACAO, FinalidadeSetor.AMBOS}


def build_summary(
    context: ImportContext,
    geometry: ImportedSectorGeometry,
    *,
    file_path: Path,
    name: str,
    finalidade: FinalidadeSetor,
    meta: int | None,
    tolerance: int | None,
) -> str:
    min_lon, min_lat, max_lon, max_lat = geometry.polygon.bounds
    lines = [
        "Resumo da importacao",
        f"Tenant: {context.current_user.company_id}",
        f"Usuario administrativo: {context.current_user.nome} (ID {context.current_user.id})",
        f"Projeto: {context.project.nome} (ID {context.project.id})",
        f"Pesquisa: {context.survey.titulo} (ID {context.survey.id})",
        f"Finalidade: {finalidade.value}",
    ]
    if context.agent is not None:
        lines.append(f"Agente: {context.agent.nome} (ID {context.agent.id})")
    lines.extend(
        [
            f"Arquivo: {file_path}",
            f"Setor: {name}",
        ]
    )
    if meta is not None:
        lines.append(f"Cota/meta: {meta}")
    if tolerance is not None:
        lines.append(f"Tolerancia: {tolerance} m")
    lines.extend(
        [
            f"CRS de origem: {geometry.source_crs}",
            f"Feicoes: {geometry.feature_count}",
            "Feicoes rejeitadas: 0",
            f"Feicoes unidas: {'sim' if geometry.features_merged else 'nao'}",
            "Geometria final: Polygon EPSG:4326",
            f"Limites (lon/lat): {min_lon:.6f}, {min_lat:.6f}, {max_lon:.6f}, {max_lat:.6f}",
        ]
    )
    return "\n".join(lines)


def eligible_admins(db: Session) -> list[models.Usuario]:
    return (
        db.query(models.Usuario)
        .join(models.Perfil)
        .join(models.Company)
        .options(joinedload(models.Usuario.perfil))
        .filter(
            models.Usuario.ativo.is_(True),
            models.Usuario.company_id.is_not(None),
            models.Company.is_active.is_(True),
            func.lower(models.Perfil.nome).in_(ADMIN_PROFILES),
        )
        .order_by(models.Usuario.nome, models.Usuario.id)
        .all()
    )


def tenant_projects(db: Session, company_id: int) -> list[models.Projeto]:
    return (
        db.query(models.Projeto)
        .filter(models.Projeto.company_id == company_id)
        .order_by(models.Projeto.nome, models.Projeto.id)
        .all()
    )


def active_surveys(db: Session, project_id: int) -> list[models.Pesquisa]:
    return (
        db.query(models.Pesquisa)
        .options(
            load_only(
                models.Pesquisa.id,
                models.Pesquisa.titulo,
                models.Pesquisa.ativo,
                models.Pesquisa.projeto_id,
            )
        )
        .filter(
            models.Pesquisa.projeto_id == project_id,
            models.Pesquisa.ativo.is_(True),
        )
        .order_by(models.Pesquisa.titulo, models.Pesquisa.id)
        .all()
    )


def tenant_agents(db: Session, company_id: int) -> list[models.Usuario]:
    return (
        db.query(models.Usuario)
        .join(models.Perfil)
        .filter(
            models.Usuario.company_id == company_id,
            models.Usuario.ativo.is_(True),
            func.lower(models.Perfil.nome) == AGENT_PROFILE,
        )
        .order_by(models.Usuario.nome, models.Usuario.id)
        .all()
    )


def validate_import_context(
    db: Session,
    *,
    user_id: int,
    project_id: int,
    survey_id: int,
    agent_id: int | None,
    finalidade: FinalidadeSetor = FinalidadeSetor.OPERACAO,
) -> ImportContext:
    current_user = (
        db.query(models.Usuario)
        .join(models.Perfil)
        .join(models.Company)
        .options(joinedload(models.Usuario.perfil))
        .filter(
            models.Usuario.id == user_id,
            models.Usuario.ativo.is_(True),
            models.Usuario.company_id.is_not(None),
            models.Company.is_active.is_(True),
            func.lower(models.Perfil.nome).in_(ADMIN_PROFILES),
        )
        .first()
    )
    if current_user is None:
        raise ValueError("Usuario administrativo inelegivel, inativo ou sem tenant ativo.")

    project = (
        db.query(models.Projeto)
        .filter(
            models.Projeto.id == project_id,
            models.Projeto.company_id == current_user.company_id,
        )
        .first()
    )
    if project is None:
        raise ValueError("Projeto inexistente ou fora do tenant selecionado.")

    survey = (
        db.query(models.Pesquisa)
        .options(
            load_only(
                models.Pesquisa.id,
                models.Pesquisa.titulo,
                models.Pesquisa.ativo,
                models.Pesquisa.projeto_id,
            )
        )
        .filter(
            models.Pesquisa.id == survey_id,
            models.Pesquisa.projeto_id == project.id,
            models.Pesquisa.ativo.is_(True),
        )
        .first()
    )
    if survey is None:
        raise ValueError("Pesquisa inexistente, inativa ou fora do projeto selecionado.")

    agent = None
    if agent_id is not None:
        agent = (
            db.query(models.Usuario)
            .join(models.Perfil)
            .filter(
                models.Usuario.id == agent_id,
                models.Usuario.company_id == current_user.company_id,
                models.Usuario.ativo.is_(True),
                func.lower(models.Perfil.nome) == AGENT_PROFILE,
            )
            .first()
        )
    if finalidade_is_operational(finalidade) and agent is None:
        raise ValueError("Agente inexistente, inativo ou fora do tenant selecionado.")
    return ImportContext(current_user=current_user, project=project, survey=survey, agent=agent)


def ensure_name_available(
    db: Session,
    *,
    survey_id: int,
    name: str,
    allow_duplicate: bool,
) -> None:
    if allow_duplicate:
        return
    normalized = normalize_sector_name(name)
    existing_names = db.query(models.Setor.nome).filter(models.Setor.pesquisa_id == survey_id).all()
    if any(normalize_sector_name(row[0]) == normalized for row in existing_names):
        raise ValueError(
            "Ja existe um setor com o mesmo nome normalizado nesta pesquisa. "
            "Use --permitir-nome-duplicado somente se isso for intencional."
        )


def _select(items: Sequence[object], label: str, display: Callable[[object], str]) -> object:
    if not items:
        raise ValueError(f"Nenhuma opcao disponivel para {label}.")
    while True:
        print(f"\nSelecione {label} (0 para cancelar):")
        for index, item in enumerate(items, start=1):
            print(f"{index}. {display(item)}")
        raw = input("> ").strip()
        if raw.casefold() in {"0", "cancelar"}:
            raise ImportCancelled
        try:
            return items[int(raw) - 1]
        except (ValueError, IndexError):
            print("Opcao invalida. Tente novamente.")


def _required_text(label: str) -> str:
    while True:
        raw = input(f"{label} (ou CANCELAR): ").strip()
        if raw.casefold() == "cancelar":
            raise ImportCancelled
        if raw:
            return raw
        print("Este valor e obrigatorio.")


def _validated_number(label: str, validator: Callable[[object], int]) -> int:
    while True:
        raw = _required_text(label)
        try:
            return validator(raw)
        except ValueError as exc:
            print(exc)


def _select_finalidade() -> FinalidadeSetor:
    options = (
        ("1", "Operacao de Campo", FinalidadeSetor.OPERACAO),
        ("2", "Relatorios", FinalidadeSetor.RELATORIO),
        ("3", "Operacao + Relatorios", FinalidadeSetor.AMBOS),
    )
    while True:
        print("\nFinalidade dos setores (0 para cancelar):")
        for key, label, _ in options:
            print(f"{key} - {label}")
        raw = input("> ").strip().casefold()
        if raw in {"0", "cancelar"}:
            raise ImportCancelled
        for key, _, finalidade in options:
            if raw == key:
                return finalidade
        try:
            return normalize_finalidade(raw)
        except ValueError:
            print("Opcao invalida. Tente novamente.")


def _select_file() -> Path:
    available = sorted(IMPORT_DIR.glob("*.shp"), key=lambda item: item.name.casefold())
    while True:
        print("\nSelecione o Shapefile (0 para cancelar):")
        for index, item in enumerate(available, start=1):
            print(f"{index}. {item.name}")
        print(f"{len(available) + 1}. Informar caminho absoluto")
        raw = input("> ").strip()
        if raw.casefold() in {"0", "cancelar"}:
            raise ImportCancelled
        try:
            selected = int(raw)
        except ValueError:
            print("Opcao invalida. Tente novamente.")
            continue
        if 1 <= selected <= len(available):
            return available[selected - 1]
        if selected == len(available) + 1:
            path = Path(_required_text("Caminho absoluto do arquivo .shp")).expanduser()
            if not path.is_absolute():
                print("Informe um caminho absoluto.")
                continue
            return path
        print("Opcao invalida. Tente novamente.")


def _resolve_geometry(path: Path, merge_features: bool) -> ImportedSectorGeometry:
    try:
        return load_sector_geometry(path, merge_features=merge_features)
    except MultipleFeaturesError as exc:
        print(f"O arquivo possui {exc.feature_count} feicoes.")
        while True:
            print("1. Unir as feicoes")
            print("2. Cancelar")
            choice = input("> ").strip().casefold()
            if choice == "1":
                return load_sector_geometry(path, merge_features=True)
            if choice in {"2", "cancelar"}:
                raise ImportCancelled
            print("Opcao invalida. Tente novamente.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Importa um Shapefile como setor existente.")
    parser.add_argument("--usuario-id", type=int)
    parser.add_argument("--projeto-id", type=int)
    parser.add_argument("--pesquisa-id", type=int)
    parser.add_argument("--agente-id", type=int)
    parser.add_argument("--arquivo", type=Path)
    parser.add_argument("--nome")
    parser.add_argument("--meta", "--cota", dest="meta")
    parser.add_argument("--tolerancia", "--tolerancia-metros", dest="tolerancia")
    parser.add_argument("--finalidade")
    parser.add_argument("--unir-feicoes", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--permitir-nome-duplicado", action="store_true")
    parser.add_argument("--sim", action="store_true")
    return parser


def persist_sector(
    db: Session,
    context: ImportContext,
    geometry: ImportedSectorGeometry,
    *,
    name: str,
    finalidade: FinalidadeSetor,
    meta: int,
    tolerance: int,
):
    # Importacao tardia: --help e --dry-run nao dependem da configuracao JWT.
    from pesquisa360 import crud, schemas

    setor_in = schemas.SetorCreate(
        nome=name,
        meta=meta,
        tolerancia=tolerance,
        agente_id=context.agent.id if context.agent is not None else None,
        finalidade=finalidade,
        geometria_coords=geometry.coordinates_lat_lon,
    )
    return crud.create_setor(db, setor_in, context.survey.id, context.current_user)


def run(args: argparse.Namespace, db: Session) -> int:
    admins = eligible_admins(db)
    current_user = next((item for item in admins if item.id == args.usuario_id), None)
    if current_user is None and args.usuario_id is None:
        current_user = _select(admins, "o gerente ou superadmin", lambda item: f"{item.nome} (ID {item.id}, tenant {item.company_id})")
    if current_user is None:
        raise ValueError("Usuario administrativo inelegivel, inativo ou sem tenant ativo.")

    projects = tenant_projects(db, current_user.company_id)
    project = next((item for item in projects if item.id == args.projeto_id), None)
    if project is None and args.projeto_id is None:
        project = _select(projects, "o projeto", lambda item: f"{item.nome} (ID {item.id})")
    if project is None:
        raise ValueError("Projeto inexistente ou fora do tenant selecionado.")

    surveys = active_surveys(db, project.id)
    survey = next((item for item in surveys if item.id == args.pesquisa_id), None)
    if survey is None and args.pesquisa_id is None:
        survey = _select(surveys, "a pesquisa ativa", lambda item: f"{item.titulo} (ID {item.id})")
    if survey is None:
        raise ValueError("Pesquisa inexistente, inativa ou fora do projeto selecionado.")

    finalidade = normalize_finalidade(args.finalidade) if args.finalidade is not None else _select_finalidade()

    agent = None
    if finalidade_is_operational(finalidade):
        agents = tenant_agents(db, current_user.company_id)
        agent = next((item for item in agents if item.id == args.agente_id), None)
        if agent is None and args.agente_id is None:
            agent = _select(agents, "o agente ativo", lambda item: f"{item.nome} (ID {item.id})")
        if agent is None:
            raise ValueError("Agente inexistente, inativo ou fora do tenant selecionado.")
    elif args.agente_id is not None:
        agents = tenant_agents(db, current_user.company_id)
        agent = next((item for item in agents if item.id == args.agente_id), None)
        if agent is None:
            raise ValueError("Agente inexistente, inativo ou fora do tenant selecionado.")

    file_path = args.arquivo.expanduser() if args.arquivo else _select_file()
    name = args.nome.strip() if args.nome else _required_text("Nome do setor")
    if not name:
        raise ValueError("Nome do setor e obrigatorio.")
    if finalidade_is_operational(finalidade):
        meta_input = validate_meta(args.meta) if args.meta is not None else _validated_number("Cota/meta", validate_meta)
        tolerance_input = validate_tolerance(args.tolerancia) if args.tolerancia is not None else _validated_number("Tolerancia", validate_tolerance)
    else:
        meta_input = validate_meta(args.meta) if args.meta is not None else None
        tolerance_input = validate_tolerance(args.tolerancia) if args.tolerancia is not None else None
    meta_to_persist = meta_input if meta_input is not None else 0
    tolerance_to_persist = tolerance_input if tolerance_input is not None else 0
    geometry = _resolve_geometry(file_path, args.unir_feicoes)

    # Revalida toda a cadeia imediatamente antes do dry-run ou da gravacao.
    context = validate_import_context(
        db,
        user_id=current_user.id,
        project_id=project.id,
        survey_id=survey.id,
        agent_id=agent.id if agent is not None else None,
        finalidade=finalidade,
    )
    ensure_name_available(
        db,
        survey_id=context.survey.id,
        name=name,
        allow_duplicate=args.permitir_nome_duplicado,
    )
    print(
        build_summary(
            context,
            geometry,
            file_path=file_path.resolve(),
            name=name,
            finalidade=finalidade,
            meta=meta_input,
            tolerance=tolerance_input,
        )
    )

    if args.dry_run:
        print("Dry-run concluido: nenhuma gravacao foi executada.")
        return 0
    if not args.sim and input("Digite IMPORTAR para confirmar: ").strip() != "IMPORTAR":
        raise ImportCancelled

    created = persist_sector(
        db,
        context,
        geometry,
        name=name,
        finalidade=finalidade,
        meta=meta_to_persist,
        tolerance=tolerance_to_persist,
    )
    print(f"Setor importado com sucesso. ID criado: {created.id}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db = None
    try:
        from pesquisa360.db.session import SessionLocal

        db = SessionLocal()
        return run(args, db)
    except ImportCancelled:
        if db is not None:
            db.rollback()
        print("Importacao cancelada. Nenhuma alteracao foi realizada.")
        return 0
    except (ShapefileImportError, ValueError) as exc:
        if db is not None:
            db.rollback()
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        if db is not None:
            db.rollback()
        print(f"Falha inesperada na importacao: {exc}", file=sys.stderr)
        return 1
    finally:
        if db is not None:
            db.close()


if __name__ == "__main__":
    raise SystemExit(main())
