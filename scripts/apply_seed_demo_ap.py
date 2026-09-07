"""Aplica o lote sintetico imutavel da demo AP na Pesquisa 5 (PROD).

Unico caminho de escrita: services.synthetic_import.create_synthetic_batch,
com o Super Admin operador real, uma transacao e um evento de auditoria de
lote. Recusa executar se o dataset nao bater com o digest do manifest ou se a
pesquisa ja tiver coletas.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text

from pesquisa360 import schemas
from pesquisa360.core.rbac import Papel, papel_do_usuario
from pesquisa360.db import models
from pesquisa360.db.session import SessionLocal
from pesquisa360.services.synthetic_import import SyntheticRecord, create_synthetic_batch


READY_DIR = Path("artifacts/demo_ap/ready_to_apply")
OUT_DIR = Path("artifacts/demo_ap/applied")


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    if not os.environ.get("DATABASE_URL", "").startswith("postgresql"):
        raise SystemExit("DATABASE_URL de producao (postgresql) e obrigatoria")
    manifest = json.loads((READY_DIR / "manifest.json").read_text(encoding="utf-8"))
    dataset_bytes = (READY_DIR / "records.jsonl").read_bytes()
    dataset_digest = hashlib.sha256(dataset_bytes).hexdigest()
    if manifest["status"] != "PASS" or dataset_digest != manifest["dataset_digest"]:
        raise SystemExit("ABORT: dataset nao corresponde ao manifest imutavel")
    survey_id = manifest["survey_id"]
    seed_run_id = UUID(manifest["seed_run_id"])
    records_in = [json.loads(line) for line in dataset_bytes.decode("utf-8").splitlines()]
    if len(records_in) != manifest["total_expected"]:
        raise SystemExit("ABORT: quantidade de registros divergente")

    db = SessionLocal()
    try:
        pre = {
            "coletas": db.execute(text("SELECT COUNT(*) FROM coletas WHERE pesquisa_id=:s"), {"s": survey_id}).scalar(),
            "respostas": db.execute(text("SELECT COUNT(*) FROM respostas r JOIN coletas c ON c.id=r.coleta_id WHERE c.pesquisa_id=:s"), {"s": survey_id}).scalar(),
            "perguntas_ativas": db.execute(text("SELECT COUNT(*) FROM perguntas WHERE pesquisa_id=:s AND ativo"), {"s": survey_id}).scalar(),
            "alembic": db.execute(text("SELECT version_num FROM alembic_version")).scalar(),
        }
        if pre["coletas"] or pre["respostas"] or pre["perguntas_ativas"] != 20 or pre["alembic"] != "c6d7e8f9a0b1":
            raise SystemExit(f"ABORT: pre-condicoes falharam: {pre}")
        operator = db.get(models.Usuario, manifest["operator_id"])
        if operator is None or operator.ativo is not True or papel_do_usuario(operator) != Papel.SUPERADMIN:
            raise SystemExit("ABORT: operador invalido")
        agents = {}
        for agent_id in sorted({r["agente_id"] for r in records_in}):
            agent = db.get(models.Usuario, agent_id)
            if agent is None or agent.ativo is not True:
                raise SystemExit(f"ABORT: agente {agent_id} invalido")
            agents[agent_id] = agent

        batch = [
            SyntheticRecord(
                credited_agent=agents[r["agente_id"]],
                record_key=r["record_key"],
                payload=schemas.ColetaCreate(
                    client_uuid=UUID(r["client_uuid"]),
                    setor_id=r["setor_id"],
                    data_inicio_coleta=r["data_inicio_coleta"],
                    data_fim_coleta=r["data_fim_coleta"],
                    localizacao_inicio=r["localizacao_inicio"],
                    localizacao_fim=r["localizacao_fim"],
                    foi_offline=False,
                    respostas=[schemas.RespostaCreate(**a) for a in r["respostas"]],
                ),
            )
            for r in records_in
        ]
        started_at = datetime.now(ZoneInfo("America/Belem"))
        created = create_synthetic_batch(
            db, operator=operator, survey_id=survey_id, seed_run_id=seed_run_id,
            synthetic_source=manifest["synthetic_source"], records=batch,
        )
        finished_at = datetime.now(ZoneInfo("America/Belem"))
        created_ids = sorted(c.id for c in created)

        post = {
            "coletas": db.execute(text("SELECT COUNT(*) FROM coletas WHERE pesquisa_id=:s"), {"s": survey_id}).scalar(),
            "coletas_sinteticas_run": db.execute(text(
                "SELECT COUNT(*) FROM coletas WHERE pesquisa_id=:s AND is_synthetic AND seed_run_id=:r "
                "AND synthetic_source=:src AND synthetic_operator_id=:op AND setor_id=:sec AND foi_offline IS FALSE"),
                {"s": survey_id, "r": str(seed_run_id), "src": manifest["synthetic_source"],
                 "op": manifest["operator_id"], "sec": manifest["source_sector_id"]}).scalar(),
            "respostas": db.execute(text("SELECT COUNT(*) FROM respostas r JOIN coletas c ON c.id=r.coleta_id WHERE c.pesquisa_id=:s"), {"s": survey_id}).scalar(),
            "per_agent": {int(k): v for k, v in db.execute(text(
                "SELECT agente_id, COUNT(*) FROM coletas WHERE pesquisa_id=:s GROUP BY agente_id"), {"s": survey_id}).all()},
            "client_uuid_distintos": db.execute(text("SELECT COUNT(DISTINCT client_uuid) FROM coletas WHERE pesquisa_id=:s"), {"s": survey_id}).scalar(),
            "inside_mask": db.execute(text(
                "SELECT COUNT(*) FROM coletas c JOIN setores s ON s.id=c.setor_id WHERE c.pesquisa_id=:s AND ST_Covers(s.geometria, c.localizacao_inicio)"),
                {"s": survey_id}).scalar(),
            "endereco_estimado_preenchido": db.execute(text("SELECT COUNT(*) FROM coletas WHERE pesquisa_id=:s AND endereco_estimado IS NOT NULL"), {"s": survey_id}).scalar(),
            "historico_nao_sintetico_alterado": db.execute(text("SELECT COUNT(*) FROM coletas WHERE pesquisa_id<>:s AND is_synthetic"), {"s": survey_id}).scalar(),
            "audit_batch_events": db.execute(text(
                "SELECT COUNT(*) FROM audit_events WHERE event_type='SYNTHETIC_COLLECTION_BATCH_COMPLETED' AND details->>'seed_run_id'=:r"),
                {"r": str(seed_run_id)}).scalar(),
        }
    finally:
        db.close()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    result = {
        "status": "APPLIED",
        "survey_id": survey_id, "project_id": manifest["project_id"], "company_id": manifest["company_id"],
        "operator_id": manifest["operator_id"], "seed_run_id": str(seed_run_id),
        "synthetic_source": manifest["synthetic_source"],
        "dataset_digest": dataset_digest, "questionnaire_digest": manifest["questionnaire_digest"],
        "spatial_digest": manifest["spatial_digest"], "application_git_sha": manifest["application_git_sha"],
        "started_at": started_at.isoformat(), "finished_at": finished_at.isoformat(),
        "created_count": len(created), "coleta_id_min": created_ids[0], "coleta_id_max": created_ids[-1],
        "pre": pre, "post": post,
    }
    write_json(OUT_DIR / "applied-manifest.json", result)
    (OUT_DIR / "coleta-ids.json").write_text(json.dumps(created_ids) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
