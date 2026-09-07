"""Cadastro estrutural da Pesquisa 5 (demo AP) pelo contrato oficial da API.

Executa, in-process, as rotas reais de criacao de perguntas e de categorias
de resposta espontanea, com o Super Admin operador como usuario corrente.
Nao cria coletas nem respostas. Aborta se a pesquisa ja tiver estrutura.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import text

from pesquisa360.core.dependencies import get_current_user
from pesquisa360.db import models
from pesquisa360.db.session import SessionLocal
from pesquisa360.main import app


PROJECT_ID = 5
SURVEY_ID = 5
COMPANY_ID = 2
OPERATOR_EMAIL = "rafhael.ferreira.silva@gmail.com"
OUT_DIR = Path("artifacts/demo_ap/prod_structure")

GOV = ["Carlos Cley", "Clécio", "Delegado Marcos", "Dr. Furlan", "Jairo Palheta", "BRANCO NULO", "NS SR"]
SEN = ["Acácio Favacho", "Alliny Serrão", "Capi", "Lucas Barreto", "Randolfe", "Rayssa Furlan", "BRANCO NULO", "NS SR"]
PRES = ["Augusto Cury", "Flávio Bolsonaro", "Lula", "Pablo Marçal", "Renan Santos", "Romeu Zema", "Ronaldo Caiado", "BRANCO NULO", "NS SR"]
SIM_NAO = ["Sim", "Não", "NS/SR"]


def q(label, texto, ordem, opcoes=None, *, espontanea=False, multipla=False, metadados=None):
    return {
        "label": label,
        "payload": {
            "texto_pergunta": texto,
            "tipo_pergunta": "MULTIPLA_ESCOLHA" if multipla else ("TEXTO" if espontanea else "ESCOLHA_SIMPLES"),
            "ordem": ordem,
            "eh_obrigatoria": True,
            "eh_resposta_espontanea": espontanea,
            "metadados_analiticos": metadados or {},
            "opcoes": [{"texto": texto_opcao, "ordem": pos} for pos, texto_opcao in enumerate(opcoes or [], 1)],
            "aplicabilidade": "GLOBAL",
        },
    }


QUESTIONNAIRE = [
    q("Q2", "Sexo", 1, ["Masculino", "Feminino"]),
    q("Q3", "Idade", 2, ["16 a 24 anos", "25 a 34", "35 a 44 anos", "45 a 59 anos", "Acima 60 anos"]),
    q("Q4", "Grau de Instrução", 3, ["Analfabeto", "Ensino Fundamental Completo", "Ensino Fundamental Incompleto",
                                     "Ensino Médio Completo", "Ensino Médio Incompleto", "Lê e Escreve",
                                     "Superior completo", "Superior Incompleto"]),
    q("Q5", "Nível econômico", 4, ["Até 1 Salário mínimo", "Acima de 1 até 2 salários mínimos",
                                    "Acima de 2 até 3 salários mínimos", "Acima de 3 até 4 salários mínimos",
                                    "Acima de 4 salários mínimos"]),
    q("Q6", "Religião", 5, ["Católica", "Evangélica", "Sem religião", "Outra"]),
    q("Q7", "Presidente do Brasil — PROVOCADA", 6, PRES),
    q("Q10", "Governador do Amapá — PROVOCADA", 7, GOV),
    q("Q11", "Rejeição Governador", 8, GOV),
    q("Q12", "Independente da sua intenção de voto, quem você acha que será o próximo governador do Amapá?", 9,
      espontanea=True),
    q("Q13", "Primeiro voto Senado — ESPONTÂNEA", 10, espontanea=True),
    q("Q14", "Segundo voto Senado — ESPONTÂNEA", 11, espontanea=True),
    q("Q15", "Primeiro voto Senado — PROVOCADA", 12, SEN),
    q("Q16", "Segundo voto Senado — PROVOCADA", 13, SEN),
    q("Q17", "Rejeição Senado", 14, SEN),
    q("Q18", "Independente da sua intenção de voto, quem você acha que serão os dois Senadores eleitos pelo Amapá?", 15,
      espontanea=True, multipla=True, metadados={"min_selections": 2, "max_selections": 2}),
    q("Q24", "Pode mudar voto Governo", 16, SIM_NAO),
    q("Q25", "Pode mudar voto Senado", 17, SIM_NAO),
    q("Q26", "Você aprova ou desaprova a administração do atual Governo do Amapá?", 18,
      ["Aprova", "Desaprova", "NS/SR"]),
    q("Q27", "Na sua opinião, Clécio merece ou não merece ser reeleito governador do Amapá?", 19,
      ["Merece", "Não merece", "NS/SR"]),
    q("Q28", "Na sua opinião, o próximo governo deveria dar continuidade à administração atual, "
             "manter parte e mudar parte, ou mudar totalmente?", 20,
      ["Dar continuidade", "Manter parte e mudar parte", "Mudar totalmente", "NS/SR"]),
]

# Vocabulario das espontaneas = mesmo das estimuladas (Governo + Senado).
SPONTANEOUS_CATEGORIES = [
    *[name for name in GOV if name not in {"BRANCO NULO", "NS SR"}],
    *[name for name in SEN if name not in {"BRANCO NULO", "NS SR"}],
    "BRANCO NULO",
    "NS SR",
]


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scalar(db, sql: str, **params):
    return db.execute(text(sql), params).scalar()


def preflight(db) -> models.Usuario:
    counts = {
        "coletas": scalar(db, "SELECT COUNT(*) FROM coletas WHERE pesquisa_id=:s", s=SURVEY_ID),
        "respostas": scalar(db, "SELECT COUNT(*) FROM respostas r JOIN coletas c ON c.id=r.coleta_id WHERE c.pesquisa_id=:s", s=SURVEY_ID),
        "perguntas": scalar(db, "SELECT COUNT(*) FROM perguntas WHERE pesquisa_id=:s", s=SURVEY_ID),
        "categorias": scalar(db, "SELECT COUNT(*) FROM categorias_resposta_espontanea WHERE pesquisa_id=:s", s=SURVEY_ID),
    }
    if any(counts.values()):
        raise SystemExit(f"ABORT: pesquisa {SURVEY_ID} nao esta vazia: {counts}")
    project = db.get(models.Projeto, PROJECT_ID)
    survey = db.get(models.Pesquisa, SURVEY_ID)
    if project is None or survey is None or survey.projeto_id != PROJECT_ID or project.company_id != COMPANY_ID:
        raise SystemExit("ABORT: projeto/pesquisa divergem do esperado")
    operator = db.query(models.Usuario).filter(models.Usuario.email == OPERATOR_EMAIL).one()
    if operator.id != 1 or operator.ativo is not True or operator.perfil.nome != "Superadmin":
        raise SystemExit("ABORT: operador nao e o Super Admin esperado")
    return operator


def main() -> int:
    if not os.environ.get("DATABASE_URL", "").startswith("postgresql"):
        raise SystemExit("DATABASE_URL de producao (postgresql) e obrigatoria")
    db = SessionLocal()
    try:
        operator = preflight(db)
        operator_id = operator.id
    finally:
        db.close()

    def _operator_override():
        session = SessionLocal()
        try:
            yield session.get(models.Usuario, operator_id)
        finally:
            session.close()

    app.dependency_overrides[get_current_user] = _operator_override
    client = TestClient(app)

    created = []
    for item in QUESTIONNAIRE:
        response = client.post(f"/pesquisas/{SURVEY_ID}/perguntas/", json=item["payload"])
        if response.status_code != 200:
            raise SystemExit(f"FALHA ao criar {item['label']}: {response.status_code} {response.text}")
        body = response.json()
        created.append({"label": item["label"], "pergunta_id": body["id"], "ordem": body["ordem"]})
        print(f"created {item['label']} -> pergunta_id={body['id']} ordem={body['ordem']}")

    categories = []
    for name in SPONTANEOUS_CATEGORIES:
        response = client.post(f"/pesquisas/{SURVEY_ID}/apuracao-espontanea/categorias", json={"nome": name})
        if response.status_code != 201:
            raise SystemExit(f"FALHA ao criar categoria {name}: {response.status_code} {response.text}")
        body = response.json()
        categories.append({"categoria_id": body["id"], "nome": body["nome"], "nome_normalizado": body["nome_normalizado"]})
        print(f"created categoria {name} -> id={body['id']}")

    listed = client.get(f"/pesquisas/{SURVEY_ID}/perguntas/")
    if listed.status_code != 200:
        raise SystemExit(f"FALHA ao listar perguntas: {listed.status_code}")
    by_id = {p["id"]: p for p in listed.json()}
    label_by_id = {c["pergunta_id"]: c["label"] for c in created}
    questions_map = []
    for pergunta_id, pergunta in sorted(by_id.items(), key=lambda kv: kv[1]["ordem"]):
        questions_map.append({
            "label": label_by_id[pergunta_id],
            "pergunta_id": pergunta_id,
            "ordem": pergunta["ordem"],
            "tipo": pergunta["tipo_pergunta"].lower(),
            "obrigatoria": pergunta["eh_obrigatoria"],
            "espontanea": pergunta["eh_resposta_espontanea"],
            "metadados_analiticos": pergunta["metadados_analiticos"],
            "texto": pergunta["texto_pergunta"],
            "opcoes": [
                {"opcao_id": o["id"], "texto": o["texto"], "ordem": o["ordem"]}
                for o in sorted(pergunta["opcoes"], key=lambda o: (o["ordem"], o["id"]))
            ],
        })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_json(OUT_DIR / "questions-map.json", questions_map)
    write_json(OUT_DIR / "spontaneous-categories.json", {
        "survey_id": SURVEY_ID,
        "note": "vocabulario conhecido para Q12 (Governo) e Q13/Q14/Q18 (Senado); mapeamento de chaves so apos existirem respostas",
        "categories": categories,
    })
    write_json(OUT_DIR / "structure-manifest.json", {
        "project_id": PROJECT_ID,
        "survey_id": SURVEY_ID,
        "company_id": COMPANY_ID,
        "operator_id": operator_id,
        "created_at": datetime.now(ZoneInfo("America/Belem")).isoformat(),
        "questions_created": len(created),
        "categories_created": len(categories),
        "questions_map_sha256": sha256_file(OUT_DIR / "questions-map.json"),
        "coletas_created": 0,
        "respostas_created": 0,
    })
    print(json.dumps({"questions": len(created), "categories": len(categories),
                      "questions_map_sha256": sha256_file(OUT_DIR / "questions-map.json")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
