"""Dry-run final contra IDs reais da Pesquisa 5 (demo AP). READ ONLY no banco.

Le o dataset aprovado (final_dry_run/records.jsonl) e o mapa real de perguntas
(prod_structure/questions-map.json), traduz rotulos em pergunta_id, valida cada
registro pela mesma camada usada pela importacao sintetica (sem persistir) e
grava o lote imutavel em artifacts/demo_ap/ready_to_apply/.
"""
from __future__ import annotations

import hashlib
import json
import os
import statistics
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text

from pesquisa360 import crud, schemas
from pesquisa360.core.rbac import Papel, papel_do_usuario
from pesquisa360.db import models
from pesquisa360.db.session import SessionLocal
from pesquisa360.services import acessos
from pesquisa360.services.synthetic_import import deterministic_client_uuid, validate_synthetic_answers


PROJECT_ID = 5
SURVEY_ID = 5
COMPANY_ID = 2
SECTOR_ID = 29
OPERATOR_ID = 1
RANDOM_SEED = 20260906
SYNTHETIC_SOURCE = "demo_ap_2026_seed"
FINAL_DIR = Path("artifacts/demo_ap/final_dry_run")
STRUCTURE_DIR = Path("artifacts/demo_ap/prod_structure")
SPATIAL_DIR = Path("artifacts/demo_ap/spatial")
OUT_DIR = Path("artifacts/demo_ap/ready_to_apply")
MASK_LABEL = "setor Macapa da pesquisa 5 - máscara operacional DEMO"

# Rotulos de valor do dataset aprovado -> texto oficial da opcao cadastrada.
# Somente Q5 difere; as contagens por categoria nao mudam.
VALUE_LABEL_TRANSLATIONS = {
    "Q5": {
        "Até 1 salário mínimo": "Até 1 Salário mínimo",
        "Acima de 1 até 2": "Acima de 1 até 2 salários mínimos",
        "Acima de 2 até 3": "Acima de 2 até 3 salários mínimos",
        "Acima de 3 até 4": "Acima de 3 até 4 salários mínimos",
        "Acima de 4 salários mínimos": "Acima de 4 salários mínimos",
    }
}

SCOPE_FILES = [
    "migrations/versions/c6d7e8f9a0b1_add_synthetic_collection_metadata.py",
    "pesquisa360/db/models.py", "pesquisa360/schemas.py", "pesquisa360/api/endpoints/coletas.py",
    "pesquisa360/services/synthetic_import.py", "pesquisa360/services/auditoria.py",
    "tests/test_migration_chain.py", "tests/test_synthetic_import.py",
    "scripts/seed_demo_eleicoes_ap.py", "scripts/spatial_dry_run_demo_ap.py",
    "scripts/prod_structure_demo_ap.py", "scripts/ready_to_apply_demo_ap.py",
]


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def git_sha() -> str:
    if os.environ.get("GIT_SHA"):
        return os.environ["GIT_SHA"]
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def main() -> int:
    if not os.environ.get("DATABASE_URL", "").startswith("postgresql"):
        raise SystemExit("DATABASE_URL de producao (postgresql) e obrigatoria")
    manifest_in = json.loads((FINAL_DIR / "manifest.json").read_text(encoding="utf-8"))
    seed_run_id = UUID(manifest_in["seed_run_id"])
    records = [json.loads(line) for line in (FINAL_DIR / "records.jsonl").read_text(encoding="utf-8").splitlines()]
    questions = json.loads((STRUCTURE_DIR / "questions-map.json").read_text(encoding="utf-8"))
    by_label = {item["label"]: item for item in questions}
    if len(questions) != 20 or sorted(item["ordem"] for item in questions) != list(range(1, 21)):
        raise SystemExit("questions-map invalido")

    errors: list[str] = []
    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        pre = {
            "coletas": db.execute(text("SELECT COUNT(*) FROM coletas WHERE pesquisa_id=:s"), {"s": SURVEY_ID}).scalar(),
            "respostas": db.execute(text("SELECT COUNT(*) FROM respostas r JOIN coletas c ON c.id=r.coleta_id WHERE c.pesquisa_id=:s"), {"s": SURVEY_ID}).scalar(),
        }
        if pre["coletas"] or pre["respostas"]:
            raise SystemExit(f"ABORT: pesquisa {SURVEY_ID} nao esta vazia: {pre}")

        operator = db.get(models.Usuario, OPERATOR_ID)
        if operator is None or operator.ativo is not True or papel_do_usuario(operator) != Papel.SUPERADMIN:
            raise SystemExit("ABORT: operador invalido")
        survey = db.get(models.Pesquisa, SURVEY_ID)
        project = survey.projeto
        if project.id != PROJECT_ID or project.company_id != COMPANY_ID or survey.ativo is not True:
            raise SystemExit("ABORT: projeto/pesquisa divergem")
        sector = db.execute(text(
            "SELECT id, nome, meta, tolerancia, pesquisa_id, ST_IsValid(geometria) AS valid, "
            "encode(sha256(ST_AsEWKB(geometria)),'hex') AS digest FROM setores WHERE id=:id"), {"id": SECTOR_ID}).mappings().one()
        if sector["pesquisa_id"] != SURVEY_ID or sector["nome"] != "Macapa":
            raise SystemExit("ABORT: setor 29 divergente")

        agents_by_email = {}
        for email in sorted({r["agente"] for r in records}):
            agent = db.query(models.Usuario).filter(models.Usuario.email == email).one()
            checks = {
                "user_id": agent.id,
                "ativo": agent.ativo is True,
                "perfil": agent.perfil.nome,
                "pode_operar_projeto": bool(acessos.agente_pode_operar_projeto(db, agent, PROJECT_ID)),
                "setor_ok": True,
            }
            try:
                crud.validar_setor_da_coleta(db, SECTOR_ID, SURVEY_ID, agent)
            except Exception as exc:  # noqa: BLE001
                checks["setor_ok"] = False
                errors.append(f"setor invalido para {email}: {exc}")
            if not (checks["ativo"] and checks["pode_operar_projeto"]):
                errors.append(f"agente sem acesso: {email}")
            agents_by_email[email] = {"email": email, **checks}

        # Cada registro: rotulo -> pergunta_id, traducao de valor, validacao oficial.
        out_records = []
        canonical_counts: dict[str, Counter] = {label: Counter() for label in by_label}
        for record in records:
            expected_uuid = deterministic_client_uuid(seed_run_id, SURVEY_ID, record["record_key"])
            if str(expected_uuid) != record["client_uuid"]:
                errors.append(f"client_uuid divergente em {record['record_key']}")
            answers = []
            for label, value in record["respostas"].items():
                question = by_label[label]
                if isinstance(value, list):
                    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                else:
                    raw = VALUE_LABEL_TRANSLATIONS.get(label, {}).get(value, value)
                answers.append(schemas.RespostaCreate(pergunta_id=question["pergunta_id"], valor_resposta=raw))
            try:
                canonical = validate_synthetic_answers(db, SURVEY_ID, answers)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{record['record_key']}: {getattr(exc, 'detail', exc)}")
                continue
            label_by_id = {q["pergunta_id"]: q["label"] for q in questions}
            for item in canonical:
                canonical_counts[label_by_id[item.pergunta_id]][item.valor_resposta] += 1
            payload = schemas.ColetaCreate(
                client_uuid=expected_uuid,
                setor_id=SECTOR_ID,
                data_inicio_coleta=record["data_inicio_coleta"],
                data_fim_coleta=record["data_fim_coleta"],
                localizacao_inicio=record["localizacao_inicio"],
                localizacao_fim=record["localizacao_fim"],
                foi_offline=False,
                respostas=canonical,
            )
            out_records.append({
                "record_key": record["record_key"],
                "client_uuid": str(expected_uuid),
                "agente_email": record["agente"],
                "agente_id": agents_by_email[record["agente"]]["user_id"],
                "setor_id": SECTOR_ID,
                "data_inicio_coleta": record["data_inicio_coleta"],
                "data_fim_coleta": record["data_fim_coleta"],
                "duracao_segundos": record["duracao_segundos"],
                "localizacao_inicio": record["localizacao_inicio"],
                "localizacao_fim": record["localizacao_fim"],
                "respostas": [{"pergunta_id": a.pergunta_id, "valor_resposta": a.valor_resposta}
                              for a in payload.respostas],
            })

        # Espacial: ST_Covers + vizinho mais proximo em geography (READ ONLY).
        values = ",".join(
            f"({i},ST_SetSRID(ST_MakePoint({r['localizacao_inicio']['lon']!r},{r['localizacao_inicio']['lat']!r}),4326))"
            for i, r in enumerate(records)
        )
        spatial = db.execute(text(f"""
            WITH pts(seq, g) AS (VALUES {values}),
            mask AS (SELECT geometria FROM setores WHERE id=:sid),
            nn AS (SELECT p1.seq, MIN(ST_Distance(p1.g::geography, p2.g::geography)) AS d
                     FROM pts p1 JOIN pts p2 ON p1.seq <> p2.seq GROUP BY p1.seq)
            SELECT (SELECT COUNT(*) FROM pts, mask WHERE ST_Covers(mask.geometria, pts.g)) AS inside,
                   (SELECT COUNT(*) FROM pts, mask WHERE NOT ST_Covers(mask.geometria, pts.g)) AS outside,
                   (SELECT MIN(d) FROM nn) AS min_m,
                   (SELECT percentile_cont(0.05) WITHIN GROUP (ORDER BY d) FROM nn) AS p05_m,
                   (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY d) FROM nn) AS median_m,
                   (SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY d) FROM nn) AS p95_m,
                   (SELECT MAX(d) FROM nn) AS max_m,
                   (SELECT COUNT(DISTINCT ST_AsText(g)) FROM pts) AS unique_points
        """), {"sid": SECTOR_ID}).mappings().one()
        db.rollback()
    finally:
        db.close()

    spatial = {k: (float(v) if v is not None else None) for k, v in spatial.items()}
    if spatial["inside"] != 1000 or spatial["outside"] != 0 or spatial["min_m"] < 50 or spatial["unique_points"] != 1000:
        errors.append(f"validacao espacial falhou: {spatial}")
    per_agent = Counter(r["agente_email"] for r in out_records)
    if len(out_records) != 1000 or any(len(r["respostas"]) != 20 for r in out_records):
        errors.append("registros ou respostas incompletos")
    if any(count != 250 for count in per_agent.values()) or len(per_agent) != 4:
        errors.append(f"cotas por agente divergentes: {dict(per_agent)}")
    if len({r["client_uuid"] for r in out_records}) != len(out_records):
        errors.append("client_uuid duplicado")

    # Distribuicoes exatas: compara contagens canonicas com as aprovadas.
    approved = json.loads((FINAL_DIR / "distributions.json").read_text(encoding="utf-8"))["distributions"]
    for label, expected in approved.items():
        translated = Counter({VALUE_LABEL_TRANSLATIONS.get(label, {}).get(k, k): v for k, v in expected.items()})
        if canonical_counts[label] != translated:
            errors.append(f"distribuicao divergente em {label}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with (OUT_DIR / "records.jsonl").open("w", encoding="utf-8") as fh:
        for record in out_records:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    write_json(OUT_DIR / "records-preview.json", {"total": len(out_records), "first": out_records[:3],
                                                    "representative": out_records[249:251] + out_records[499:501] + out_records[749:751] + out_records[-2:]})
    write_json(OUT_DIR / "questions-map.json", questions)
    write_json(OUT_DIR / "agents-map.json", {"agents": list(agents_by_email.values()), "quota_per_agent": 250,
                                              "planned": dict(per_agent)})
    write_json(OUT_DIR / "spatial-preview.geojson", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"record_key": r["record_key"], "agente_id": r["agente_id"]},
         "geometry": {"type": "Point", "coordinates": [r["localizacao_inicio"]["lon"], r["localizacao_inicio"]["lat"]]}}
        for r in out_records]})
    dataset_digest = sha256_file(OUT_DIR / "records.jsonl")
    spatial_digest = sha256_file(OUT_DIR / "spatial-preview.geojson")
    questionnaire_digest = sha256_file(STRUCTURE_DIR / "questions-map.json")
    code_digest = sha256_text("".join(f"{p}:{sha256_file(Path(p))}\n" for p in SCOPE_FILES if Path(p).exists()))
    manifest = {
        "status": "PASS" if not errors else "FAIL",
        "mode": "READY_TO_APPLY_DRY_RUN_READ_ONLY",
        "project_id": PROJECT_ID, "survey_id": SURVEY_ID, "company_id": COMPANY_ID,
        "operator_id": OPERATOR_ID,
        "seed_run_id": str(seed_run_id), "random_seed": RANDOM_SEED,
        "synthetic_source": SYNTHETIC_SOURCE,
        "source_mask": MASK_LABEL, "source_sector_id": SECTOR_ID, "source_sector_digest": sector["digest"],
        "source_sector_meta": sector["meta"], "source_sector_tolerancia": sector["tolerancia"],
        "questionnaire_digest": questionnaire_digest,
        "dataset_digest": dataset_digest,
        "spatial_digest": spatial_digest,
        "logical_dataset_digest_input": manifest_in["dataset_digest"],
        "application_git_sha": git_sha(),
        "application_scope_files_digest": code_digest,
        "total_expected": 1000, "responses_expected": 20000,
        "records_validated": len(out_records), "responses_planned": sum(len(r["respostas"]) for r in out_records),
        "per_agent": dict(per_agent),
        "spatial": spatial,
        "value_label_translations": VALUE_LABEL_TRANSLATIONS,
        "database_access": "READ_ONLY", "database_writes": 0, "reverse_geocoding_calls": 0, "notifications": 0,
        "created_at": datetime.now(ZoneInfo("America/Belem")).isoformat(),
        "immutable_after": "PROMPT 04",
        "errors": errors,
    }
    write_json(OUT_DIR / "manifest.json", manifest)
    report = [f"READY-TO-APPLY DRY-RUN: {manifest['status']}",
              f"records={len(out_records)}/1000 responses_planned={manifest['responses_planned']}/20000",
              f"per_agent={dict(per_agent)}",
              f"spatial inside={spatial['inside']:.0f} outside={spatial['outside']:.0f} min_m={spatial['min_m']:.8f} "
              f"p05={spatial['p05_m']:.3f} median={spatial['median_m']:.3f} p95={spatial['p95_m']:.3f} max={spatial['max_m']:.3f}",
              f"dataset_digest={dataset_digest}", f"spatial_digest={spatial_digest}",
              f"questionnaire_digest={questionnaire_digest}", f"application_git_sha={manifest['application_git_sha']}",
              "database_writes=0 geocoding=0 notifications=0",
              "ERRORS:", *(errors or ["- none"])]
    (OUT_DIR / "validation-report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
