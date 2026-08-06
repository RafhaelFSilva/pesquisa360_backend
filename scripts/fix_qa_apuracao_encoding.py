from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import joinedload


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from pesquisa360 import crud
from pesquisa360.db import models
from pesquisa360.db.session import SessionLocal


EXPECTED_PESQUISA_ID = 9
EXPECTED_COMPANY_ID = 7
EXPECTED_ADMIN_ID = 11
DEV_CONFIRMATION = "DEV_LOCAL_OK"


@dataclass(frozen=True)
class ExpectedCategory:
    category_id: int
    nome: str
    nome_normalizado: str


CANONICAL_CLECIO = ExpectedCategory(3, "Clécio Luís", "clecio luis")
CANONICAL_NAO_SABE = ExpectedCategory(4, "Não sabe/Indeciso", "nao sabe indeciso")


class FixError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Saneia as categorias UTF-8 da apuração espontânea QA no DEV local."
    )
    parser.add_argument("--pesquisa-id", type=int, required=True)
    parser.add_argument("--apply", action="store_true", help="Aplica as alterações.")
    parser.add_argument(
        "--confirm-dev",
        help=f"Obrigatório com --apply. Use exatamente {DEV_CONFIRMATION}.",
    )
    parser.add_argument("--format", choices=("json", "text"), default="json")
    return parser.parse_args()


def assert_expected_environment(args: argparse.Namespace) -> None:
    if args.pesquisa_id != EXPECTED_PESQUISA_ID:
        raise FixError(f"Este script aceita somente --pesquisa-id {EXPECTED_PESQUISA_ID}.")
    if args.apply and args.confirm_dev != DEV_CONFIRMATION:
        raise FixError(f"Execução com escrita exige --confirm-dev {DEV_CONFIRMATION}.")
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise FixError("DATABASE_URL não configurada.")
    if "pesquisa360_db" not in database_url:
        raise FixError("DATABASE_URL inesperada para o DEV local.")


def get_required_entities(session, pesquisa_id: int):
    pesquisa = (
        session.query(models.Pesquisa)
        .options(joinedload(models.Pesquisa.projeto))
        .filter(models.Pesquisa.id == pesquisa_id)
        .first()
    )
    if not pesquisa:
        raise FixError(f"Pesquisa {pesquisa_id} não encontrada.")
    if not pesquisa.projeto:
        raise FixError(f"Pesquisa {pesquisa_id} sem projeto associado.")
    if pesquisa.projeto.company_id != EXPECTED_COMPANY_ID:
        raise FixError(f"Pesquisa {pesquisa_id} não pertence à empresa {EXPECTED_COMPANY_ID}.")

    admin = session.query(models.Usuario).filter(models.Usuario.id == EXPECTED_ADMIN_ID).first()
    if not admin:
        raise FixError(f"Usuário administrativo {EXPECTED_ADMIN_ID} não encontrado.")
    if admin.company_id != EXPECTED_COMPANY_ID:
        raise FixError(
            f"Usuário administrativo {EXPECTED_ADMIN_ID} não pertence à empresa {EXPECTED_COMPANY_ID}."
        )
    return pesquisa, admin


def fetch_categories(session, pesquisa_id: int):
    categorias = (
        session.query(models.CategoriaRespostaEspontanea)
        .filter(models.CategoriaRespostaEspontanea.pesquisa_id == pesquisa_id)
        .order_by(models.CategoriaRespostaEspontanea.id)
        .all()
    )
    return {categoria.id: categoria for categoria in categorias}


def fetch_mappings(session, pesquisa_id: int):
    return (
        session.query(models.MapeamentoRespostaEspontanea)
        .filter(models.MapeamentoRespostaEspontanea.pesquisa_id == pesquisa_id)
        .order_by(models.MapeamentoRespostaEspontanea.id)
        .all()
    )


def analytics_summary(session, pesquisa_id: int, current_user: models.Usuario) -> dict:
    resumo = crud.get_respostas_espontaneas_resumo(
        db=session,
        pesquisa_id=pesquisa_id,
        current_user=current_user,
        pagina=1,
        por_pagina=100,
    )
    distribuicao = {}
    for item in resumo["itens"]:
        categoria = item["categoria"]
        if categoria is None:
            continue
        distribuicao[categoria["nome"]] = distribuicao.get(categoria["nome"], 0) + item["quantidade_total"]
    return {
        "total_chaves": resumo["total_chaves"],
        "total_respostas": resumo["total_respostas"],
        "respostas_categorizadas": resumo["respostas_categorizadas"],
        "respostas_pendentes": resumo["respostas_pendentes"],
        "percentual_categorizado": resumo["percentual_categorizado"],
        "distribuicao_por_categoria": distribuicao,
    }


def count_respostas(session, pesquisa_id: int) -> int:
    return (
        session.query(models.Resposta)
        .join(models.Coleta, models.Coleta.id == models.Resposta.coleta_id)
        .filter(models.Coleta.pesquisa_id == pesquisa_id)
        .count()
    )


def snapshot_categories(session, pesquisa_id: int) -> list[dict]:
    categorias = fetch_categories(session, pesquisa_id)
    by_category = defaultdict(list)
    for mapping in fetch_mappings(session, pesquisa_id):
        by_category[mapping.categoria_id].append(mapping)

    snapshot = []
    for categoria in categorias.values():
        mappings = by_category.get(categoria.id, [])
        snapshot.append(
            {
                "id": categoria.id,
                "nome": categoria.nome,
                "nome_normalizado": categoria.nome_normalizado,
                "ativo": bool(categoria.ativo),
                "mapeamentos_ativos": sum(1 for item in mappings if item.ativo),
                "mapeamentos_inativos": sum(1 for item in mappings if not item.ativo),
                "chaves": [
                    {
                        "id": item.id,
                        "chave_normalizada": item.chave_normalizada,
                        "texto_referencia": item.texto_referencia,
                        "ativo": bool(item.ativo),
                    }
                    for item in mappings
                ],
            }
        )
    return snapshot


def plan_operations(session, pesquisa_id: int) -> dict:
    categorias = fetch_categories(session, pesquisa_id)
    mapeamentos = fetch_mappings(session, pesquisa_id)
    mapeamentos_por_categoria = defaultdict(list)
    ativos_por_chave = {}
    for mapping in mapeamentos:
        mapeamentos_por_categoria[mapping.categoria_id].append(mapping)
        if mapping.ativo:
            ativos_por_chave[mapping.chave_normalizada] = mapping

    categoria_3 = categorias.get(3)
    categoria_4 = categorias.get(4)
    categoria_5 = categorias.get(5)
    categoria_6 = categorias.get(6)
    if categoria_3 is None or categoria_4 is None or categoria_5 is None:
        raise FixError("Categorias 3, 4 e 5 devem existir para o saneamento QA.")

    operations: list[dict] = []
    conflicts: list[str] = []

    for mapping in mapeamentos_por_categoria.get(5, []):
        if not mapping.ativo:
            continue
        same_key = ativos_por_chave.get(mapping.chave_normalizada)
        if same_key and same_key.id != mapping.id and same_key.categoria_id == 3:
            operations.append(
                {
                    "action": "deactivate_redundant_mapping",
                    "mapping_id": mapping.id,
                    "from_category_id": 5,
                    "to_category_id": 3,
                    "chave_normalizada": mapping.chave_normalizada,
                }
            )
            continue
        if same_key and same_key.id != mapping.id and same_key.categoria_id != 3:
            conflicts.append(
                f"Chave {mapping.chave_normalizada} ativa em categoria inesperada {same_key.categoria_id}."
            )
            continue
        operations.append(
            {
                "action": "move_mapping_to_category_3",
                "mapping_id": mapping.id,
                "from_category_id": 5,
                "to_category_id": 3,
                "chave_normalizada": mapping.chave_normalizada,
            }
        )

    if categoria_5.ativo:
        operations.append({"action": "deactivate_category_5", "category_id": 5})

    if categoria_3.nome != CANONICAL_CLECIO.nome or categoria_3.nome_normalizado != CANONICAL_CLECIO.nome_normalizado:
        operations.append(
            {
                "action": "rename_category_3",
                "category_id": 3,
                "nome": CANONICAL_CLECIO.nome,
                "nome_normalizado": CANONICAL_CLECIO.nome_normalizado,
            }
        )

    if categoria_4.nome != CANONICAL_NAO_SABE.nome or categoria_4.nome_normalizado != CANONICAL_NAO_SABE.nome_normalizado:
        operations.append(
            {
                "action": "rename_category_4",
                "category_id": 4,
                "nome": CANONICAL_NAO_SABE.nome,
                "nome_normalizado": CANONICAL_NAO_SABE.nome_normalizado,
            }
        )

    return {
        "categories_before": snapshot_categories(session, pesquisa_id),
        "operations": operations,
        "conflicts": conflicts,
        "category_3_active_mappings": [
            mapping.chave_normalizada
            for mapping in mapeamentos_por_categoria.get(3, [])
            if mapping.ativo
        ],
        "category_5_active_mappings": [
            mapping.chave_normalizada
            for mapping in mapeamentos_por_categoria.get(5, [])
            if mapping.ativo
        ],
        "category_6_state": None if categoria_6 is None else {
            "id": categoria_6.id,
            "nome": categoria_6.nome,
            "ativo": bool(categoria_6.ativo),
        },
    }


def apply_operations(session, pesquisa_id: int, admin_id: int, operations: list[dict]) -> None:
    categorias = fetch_categories(session, pesquisa_id)
    mapeamentos = {mapping.id: mapping for mapping in fetch_mappings(session, pesquisa_id)}

    for operation in operations:
        action = operation["action"]
        if action == "deactivate_redundant_mapping":
            mapping = mapeamentos[operation["mapping_id"]]
            if mapping.ativo:
                mapping.ativo = False
                mapping.atualizado_por_id = admin_id
        elif action == "move_mapping_to_category_3":
            mapping = mapeamentos[operation["mapping_id"]]
            if mapping.ativo and mapping.categoria_id != 3:
                mapping.categoria_id = 3
                mapping.atualizado_por_id = admin_id
        elif action == "deactivate_category_5":
            category = categorias[5]
            if category.ativo:
                category.ativo = False
                category.atualizado_por_id = admin_id
                session.flush()
        elif action == "rename_category_3":
            category = categorias[3]
            category.nome = operation["nome"]
            category.nome_normalizado = operation["nome_normalizado"]
            category.atualizado_por_id = admin_id
        elif action == "rename_category_4":
            category = categorias[4]
            category.nome = operation["nome"]
            category.nome_normalizado = operation["nome_normalizado"]
            category.atualizado_por_id = admin_id
        else:
            raise FixError(f"Ação não suportada: {action}")


def build_report(mode: str, pesquisa, tenant_name: str, plan: dict, analytics_before: dict, responses_count: int):
    return {
        "mode": mode,
        "pesquisa": {
            "id": pesquisa.id,
            "titulo": pesquisa.titulo,
            "projeto_id": pesquisa.projeto_id,
            "company_id": pesquisa.projeto.company_id,
            "company_name": tenant_name,
        },
        "categories_before": plan["categories_before"],
        "category_3_active_mappings": plan["category_3_active_mappings"],
        "category_5_active_mappings": plan["category_5_active_mappings"],
        "category_6_state": plan["category_6_state"],
        "operations": plan["operations"],
        "conflicts": plan["conflicts"],
        "analytics_before": analytics_before,
        "respostas_total_pesquisa": responses_count,
    }


def ensure_post_conditions(session, pesquisa_id: int) -> dict:
    categorias = fetch_categories(session, pesquisa_id)
    active_categories = [item for item in categorias.values() if item.ativo]

    active_names_by_normalized = defaultdict(list)
    active_mapping_keys = defaultdict(list)
    active_mapping_count_by_category = defaultdict(int)
    for categoria in active_categories:
        active_names_by_normalized[categoria.nome_normalizado].append(categoria.id)
    for mapping in fetch_mappings(session, pesquisa_id):
        if mapping.ativo:
            active_mapping_keys[mapping.chave_normalizada].append(mapping.id)
            active_mapping_count_by_category[mapping.categoria_id] += 1
            categoria = categorias.get(mapping.categoria_id)
            if categoria and categoria.pesquisa_id != mapping.pesquisa_id:
                raise FixError(
                    f"Mapeamento {mapping.id} possui pesquisa_id divergente da categoria {categoria.id}."
                )

    return {
        "active_category_names": sorted(categoria.nome for categoria in active_categories),
        "active_mapping_count_by_category": dict(sorted(active_mapping_count_by_category.items())),
        "duplicated_active_categories": {
            nome_normalizado: ids
            for nome_normalizado, ids in active_names_by_normalized.items()
            if len(ids) > 1
        },
        "duplicated_active_mappings": {
            chave: ids
            for chave, ids in active_mapping_keys.items()
            if len(ids) > 1
        },
        "category_5_active_mappings": active_mapping_count_by_category.get(5, 0),
        "category_6_active_mappings": active_mapping_count_by_category.get(6, 0),
    }


def emit(payload: dict, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(payload)


def main() -> int:
    args = parse_args()
    try:
        assert_expected_environment(args)
        read_session = SessionLocal()
        try:
            pesquisa, admin = get_required_entities(read_session, args.pesquisa_id)
            tenant_name = (
                read_session.query(models.Company.name)
                .filter(models.Company.id == pesquisa.projeto.company_id)
                .scalar()
            ) or ""
            plan = plan_operations(read_session, args.pesquisa_id)
            analytics_before = analytics_summary(read_session, args.pesquisa_id, admin)
            responses_count = count_respostas(read_session, args.pesquisa_id)
        finally:
            read_session.close()

        payload = build_report(
            mode="apply" if args.apply else "dry-run",
            pesquisa=pesquisa,
            tenant_name=tenant_name,
            plan=plan,
            analytics_before=analytics_before,
            responses_count=responses_count,
        )
        if plan["conflicts"]:
            payload["status"] = "blocked"
            emit(payload, args.format)
            return 2

        if not args.apply:
            payload["status"] = "dry-run-ok"
            emit(payload, args.format)
            return 0

        write_session = SessionLocal()
        try:
            get_required_entities(write_session, args.pesquisa_id)
            apply_operations(write_session, args.pesquisa_id, EXPECTED_ADMIN_ID, plan["operations"])
            write_session.commit()

            admin = write_session.query(models.Usuario).filter(models.Usuario.id == EXPECTED_ADMIN_ID).first()
            analytics_after = analytics_summary(write_session, args.pesquisa_id, admin)
            post_conditions = ensure_post_conditions(write_session, args.pesquisa_id)
            payload["status"] = "applied"
            payload["categories_after"] = snapshot_categories(write_session, args.pesquisa_id)
            payload["analytics_after"] = analytics_after
            payload["post_conditions"] = post_conditions
            if post_conditions["duplicated_active_categories"] or post_conditions["duplicated_active_mappings"]:
                raise FixError("Ainda existem duplicidades ativas após a aplicação.")
            emit(payload, args.format)
            return 0
        except Exception:
            write_session.rollback()
            raise
        finally:
            write_session.close()
    except FixError as exc:
        emit({"status": "error", "detail": str(exc)}, args.format if "args" in locals() else "json")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
