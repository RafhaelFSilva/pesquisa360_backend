"""Gera o dry-run logico, deterministico e offline da demo eleitoral do AP.

Nao importa configuracao da aplicacao, nao abre banco e nao gera coordenadas.
Nao existe modo de aplicacao neste comando.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5


# `principal_company_id`/`project_access_snapshot` documentam o estado real de
# producao (snapshot 2026-09-07, apos resolucao do vinculo da Carllyane); nao
# entram no dataset nem no seed_run_id.
AGENTS = [
    {"user_id": 7, "email": "carllyanesouza28@gmail.com", "quota": 250,
     "principal_company_id": 2, "project_access_snapshot": True},
    {"user_id": 4, "email": "geysesg@gmail.com", "quota": 250,
     "principal_company_id": 2, "project_access_snapshot": True},
    {"user_id": 5, "email": "vitoriamaia2203@gmail.com", "quota": 250,
     "principal_company_id": 2, "project_access_snapshot": True},
    {"user_id": 6, "email": "scmatos57@gmail.com", "quota": 250,
     "principal_company_id": 2, "project_access_snapshot": True},
]


def q(label, text, order, options=None, *, spontaneous=False, multiple=False):
    coded_options = [
        {"codigo": f"{position:02d}", "texto": option}
        for position, option in enumerate(options or [], 1)
    ]
    return {
        "label": label, "texto": text,
        "tipo": "MULTIPLA_ESCOLHA" if multiple else ("TEXTO" if spontaneous else "ESCOLHA_SIMPLES"),
        "ordem": order, "obrigatoria": True, "opcoes": coded_options,
        "semantica": {"resposta_espontanea": spontaneous,
                      "min_selections": 2 if multiple else 1,
                      "max_selections": 2 if multiple else 1},
        "origem": "formulario_usuario",
    }


GOV = ["Carlos Cley", "Clécio", "Delegado Marcos", "Dr. Furlan", "Jairo Palheta", "BRANCO NULO", "NS SR"]
SEN = ["Acácio Favacho", "Alliny Serrão", "Capi", "Lucas Barreto", "Randolfe", "Rayssa Furlan", "BRANCO NULO", "NS SR"]
PRES = ["Augusto Cury", "Flávio Bolsonaro", "Lula", "Pablo Marçal", "Renan Santos", "Romeu Zema", "Ronaldo Caiado", "BRANCO NULO", "NS SR"]

QUESTIONNAIRE = [
    q("Q2", "Sexo", 1, ["Masculino", "Feminino"]),
    q("Q3", "Idade", 2, ["16 a 24 anos", "25 a 34", "35 a 44 anos", "45 a 59 anos", "Acima 60 anos"]),
    q("Q4", "Grau de Instrução", 3, ["Analfabeto", "Ensino Fundamental Completo", "Ensino Fundamental Incompleto", "Ensino Médio Completo", "Ensino Médio Incompleto", "Lê e Escreve", "Superior completo", "Superior Incompleto"]),
    q("Q5", "Nível econômico", 4, ["Até 1 salário mínimo", "Acima de 1 até 2", "Acima de 2 até 3", "Acima de 3 até 4", "Acima de 4 salários mínimos"]),
    q("Q6", "Religião", 5, ["Católica", "Evangélica", "Sem religião", "Outra"]),
    q("Q7", "Presidente — PROVOCADA", 6, PRES),
    q("Q10", "Governador — PROVOCADA", 7, GOV),
    q("Q11", "Rejeição Governador — PROVOCADA", 8, GOV),
    q("Q12", "Expectativa de próximo governador — ESPONTÂNEA", 9, spontaneous=True),
    q("Q13", "Primeiro voto Senado — ESPONTÂNEA", 10, spontaneous=True),
    q("Q14", "Segundo voto Senado — ESPONTÂNEA", 11, spontaneous=True),
    q("Q15", "Primeiro voto Senado — PROVOCADA", 12, SEN),
    q("Q16", "Segundo voto Senado — PROVOCADA", 13, SEN),
    q("Q17", "Rejeição Senado — PROVOCADA", 14, SEN),
    q("Q18", "Quem serão os dois senadores eleitos — ESPONTÂNEA", 15, spontaneous=True, multiple=True),
    q("Q24", "Pode mudar voto para governador", 16, ["Sim", "Não", "NS/SR"]),
    q("Q25", "Pode mudar voto para senador", 17, ["Sim", "Não", "NS/SR"]),
    q("Q26", "Aprovação", 18, ["Aprova", "Desaprova", "NS/SR"]),
    q("Q27", "Recondução", 19, ["Merece", "Não merece", "NS/SR"]),
    q("Q28", "Continuidade", 20, ["Dar continuidade", "Manter parte e mudar parte", "Mudar totalmente", "NS/SR"]),
]

DISTRIBUTIONS = {
    "Q2": {"Masculino": 480, "Feminino": 520},
    "Q3": {"16 a 24 anos": 200, "25 a 34": 240, "35 a 44 anos": 220, "45 a 59 anos": 220, "Acima 60 anos": 120},
    "Q4": {"Analfabeto": 30, "Ensino Fundamental Completo": 150, "Ensino Fundamental Incompleto": 160, "Ensino Médio Completo": 280, "Ensino Médio Incompleto": 140, "Lê e Escreve": 40, "Superior completo": 120, "Superior Incompleto": 80},
    "Q5": {"Até 1 salário mínimo": 300, "Acima de 1 até 2": 300, "Acima de 2 até 3": 200, "Acima de 3 até 4": 120, "Acima de 4 salários mínimos": 80},
    "Q6": {"Católica": 430, "Evangélica": 410, "Sem religião": 100, "Outra": 60},
    "Q7": {"Augusto Cury": 20, "Flávio Bolsonaro": 240, "Lula": 360, "Pablo Marçal": 120, "Renan Santos": 30, "Romeu Zema": 60, "Ronaldo Caiado": 40, "BRANCO NULO": 70, "NS SR": 60},
    "Q10": {"Dr. Furlan": 594, "Clécio": 321, "Delegado Marcos": 6, "Carlos Cley": 4, "Jairo Palheta": 1, "BRANCO NULO": 39, "NS SR": 35},
    "Q11": {"Dr. Furlan": 190, "Clécio": 250, "Delegado Marcos": 160, "Carlos Cley": 100, "Jairo Palheta": 60, "BRANCO NULO": 80, "NS SR": 160},
    "Q12": {"Dr. Furlan": 430, "Clécio": 240, "Delegado Marcos": 40, "Carlos Cley": 30, "Jairo Palheta": 10, "BRANCO NULO": 60, "NS SR": 190},
    "Q13": {"Rayssa Furlan": 200, "Lucas Barreto": 150, "Randolfe": 130, "Alliny Serrão": 60, "Acácio Favacho": 50, "Capi": 20, "BRANCO NULO": 100, "NS SR": 290},
    "Q14": {"Rayssa Furlan": 180, "Lucas Barreto": 145, "Randolfe": 125, "Alliny Serrão": 55, "Acácio Favacho": 45, "Capi": 20, "BRANCO NULO": 100, "NS SR": 330},
    "Q15": {"Rayssa Furlan": 280, "Lucas Barreto": 191, "Randolfe": 183, "Alliny Serrão": 92, "Acácio Favacho": 81, "Capi": 32, "BRANCO NULO": 80, "NS SR": 61},
    "Q16": {"Rayssa Furlan": 280, "Lucas Barreto": 190, "Randolfe": 183, "Alliny Serrão": 92, "Acácio Favacho": 81, "Capi": 31, "BRANCO NULO": 80, "NS SR": 63},
    "Q17": {"Rayssa Furlan": 100, "Lucas Barreto": 140, "Randolfe": 190, "Alliny Serrão": 100, "Acácio Favacho": 110, "Capi": 100, "BRANCO NULO": 100, "NS SR": 160},
    "Q24": {"Sim": 330, "Não": 580, "NS/SR": 90},
    "Q25": {"Sim": 370, "Não": 530, "NS/SR": 100},
    "Q26": {"Aprova": 520, "Desaprova": 390, "NS/SR": 90},
    "Q27": {"Merece": 460, "Não merece": 450, "NS/SR": 90},
    "Q28": {"Dar continuidade": 260, "Manter parte e mudar parte": 490, "Mudar totalmente": 180, "NS/SR": 70},
}

SENATE_PRESENCE_TARGETS = {
    "Rayssa Furlan": 560, "Lucas Barreto": 381, "Randolfe": 366,
    "Alliny Serrão": 184, "Acácio Favacho": 162, "Capi": 63,
}


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def expanded(counts, rng):
    values = [value for value, count in counts.items() for _ in range(count)]
    if len(values) != 1000:
        raise ValueError(f"distribuicao soma {len(values)}, esperado 1000")
    rng.shuffle(values)
    return values


def senate_pair(q15, q16_counts, rng):
    """Preserva marginais exatas e impede o mesmo candidato nos dois votos."""
    q16 = expanded(q16_counts, rng)
    candidates = set(SEN) - {"BRANCO NULO", "NS SR"}
    for _ in range(10000):
        conflicts = [i for i, (a, b) in enumerate(zip(q15, q16)) if a == b and a in candidates]
        if not conflicts:
            return q16
        i = conflicts[0]
        swaps = [j for j in range(1000) if j != i and
                 not (q15[i] == q16[j] and q15[i] in candidates) and
                 not (q15[j] == q16[i] and q15[j] in candidates)]
        if not swaps:
            break
        j = rng.choice(swaps)
        q16[i], q16[j] = q16[j], q16[i]
    raise RuntimeError("nao foi possivel montar pares de Senado sem repeticao")


def correlated(counts, government, rng, weights):
    """Aloca marginais exatas com vies probabilistico, sem relacao deterministica."""
    available = set(range(1000)); result = [None] * 1000
    items = list(counts.items())
    for value, count in items[:-1]:
        pool = list(available)
        chosen = rng.choices(pool, weights=[weights(value, government[i]) for i in pool], k=count * 3)
        unique = list(dict.fromkeys(chosen))
        if len(unique) < count:
            rest = list(available - set(unique)); rng.shuffle(rest); unique.extend(rest)
        for i in unique[:count]:
            result[i] = value; available.remove(i)
    for i in available:
        result[i] = items[-1][0]
    return result


def cross_matrix(records, row_label, column_label):
    matrix = defaultdict(Counter)
    for record in records:
        answers = record["respostas"]; matrix[answers[row_label]][answers[column_label]] += 1
    return {row: dict(sorted(values.items())) for row, values in sorted(matrix.items())}


def generate(seed, project_id, survey_id):
    rng = random.Random(seed)
    columns = {label: expanded(counts, rng) for label, counts in DISTRIBUTIONS.items()
               if label not in {"Q16", "Q26", "Q27", "Q28"}}
    columns["Q16"] = senate_pair(columns["Q15"], DISTRIBUTIONS["Q16"], rng)
    columns["Q26"] = correlated(DISTRIBUTIONS["Q26"], columns["Q10"], rng,
        lambda answer, gov: 3.0 if (answer == "Aprova" and gov == "Clécio") or (answer == "Desaprova" and gov == "Dr. Furlan") else 1.0)
    columns["Q27"] = correlated(DISTRIBUTIONS["Q27"], columns["Q10"], rng,
        lambda answer, gov: 3.0 if (answer == "Merece" and gov == "Clécio") or (answer == "Não merece" and gov == "Dr. Furlan") else 1.0)
    columns["Q28"] = correlated(DISTRIBUTIONS["Q28"], columns["Q10"], rng,
        lambda answer, gov: 2.5 if (answer == "Dar continuidade" and gov == "Clécio") or (answer != "Dar continuidade" and gov == "Dr. Furlan") else 1.0)

    q18_first = expanded(DISTRIBUTIONS["Q13"], rng)
    q18_second = expanded(DISTRIBUTIONS["Q14"], rng)
    for i in range(1000):
        if q18_first[i] == q18_second[i]:
            for j in range(i + 1, 1000):
                if q18_first[i] != q18_second[j] and q18_first[j] != q18_second[i]:
                    q18_second[i], q18_second[j] = q18_second[j], q18_second[i]; break
    if any(a == b for a, b in zip(q18_first, q18_second)):
        raise RuntimeError("Q18 contem nomes repetidos")

    run_id = uuid5(NAMESPACE_URL, f"pesquisa360-demo-logical:{project_id}:{survey_id}:{seed}:{digest(DISTRIBUTIONS)}")
    base = datetime(2026, 8, 24, 11, 0, tzinfo=timezone.utc); records = []
    for i in range(1000):
        start = base + timedelta(minutes=i * 9 + (i % 7)); duration = 360 + ((i * 37) % 421)
        answers = {item["label"]: columns[item["label"]][i] for item in QUESTIONNAIRE if item["label"] != "Q18"}
        answers["Q18"] = [q18_first[i], q18_second[i]]
        key = f"AP-DEMO-{i + 1:04d}"
        records.append({
            "record_key": key,
            "client_uuid": str(uuid5(run_id, f"pesquisa:{survey_id}:registro:{key}")),
            "agente": AGENTS[i // 250]["email"],
            "data_inicio_coleta": start.isoformat(),
            "data_fim_coleta": (start + timedelta(seconds=duration)).isoformat(),
            "duracao_segundos": duration, "localizacao_inicio": None, "localizacao_fim": None,
            "respostas": answers,
        })
    return run_id, records


def validate(records):
    errors = []
    if len(records) != 1000 or len({r["client_uuid"] for r in records}) != 1000:
        errors.append("quantidade ou UUIDs invalidos")
    if Counter(r["agente"] for r in records) != Counter({a["email"]: 250 for a in AGENTS}):
        errors.append("cotas de agentes invalidas")
    for label, expected in DISTRIBUTIONS.items():
        if Counter(r["respostas"][label] for r in records) != Counter(expected):
            errors.append(f"marginal divergente: {label}")
    candidate_set = set(SEN) - {"BRANCO NULO", "NS SR"}
    if any(r["respostas"]["Q15"] == r["respostas"]["Q16"] in candidate_set for r in records):
        errors.append("candidato repetido em Q15/Q16")
    presence = Counter()
    for record in records:
        presence.update({record["respostas"]["Q15"], record["respostas"]["Q16"]})
    for candidate, expected in SENATE_PRESENCE_TARGETS.items():
        if presence[candidate] != expected:
            errors.append(f"presenca Senado divergente: {candidate}")
    if any(len(r["respostas"]) != 20 or len(set(r["respostas"]["Q18"])) != 2 for r in records):
        errors.append("questionario incompleto ou Q18 invalida")
    required = [
        ("Q26", "Aprova", "Clécio"), ("Q26", "Aprova", "Dr. Furlan"),
        ("Q27", "Não merece", "Clécio"), ("Q28", "Manter parte e mudar parte", "Clécio"),
        ("Q26", "Desaprova", "Clécio"), ("Q26", "Desaprova", "Dr. Furlan"),
        ("Q27", "Merece", "Clécio"),
    ]
    for label, value, gov in required:
        if not any(r["respostas"][label] == value and r["respostas"]["Q10"] == gov for r in records):
            errors.append(f"grupo diagnostico ausente: {label}/{value}/{gov}")
    if not any(r["respostas"]["Q27"] == "Merece" and r["respostas"]["Q10"] not in {"Clécio", "BRANCO NULO", "NS SR"} for r in records):
        errors.append("grupo Merece + outro candidato ausente")
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", type=int, default=5)
    parser.add_argument("--survey-id", type=int, default=5)
    parser.add_argument("--count", type=int, default=1000, choices=[1000])
    parser.add_argument("--seed", type=int, default=20260906)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/demo_ap/logical"))
    parser.add_argument("--dry-run", action="store_true", required=True)
    args = parser.parse_args()
    run_id, records = generate(args.seed, args.project_id, args.survey_id); errors = validate(records)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.output_dir.parent / "questionnaire-spec.json", QUESTIONNAIRE)
    write_json(args.output_dir / "questionnaire-spec.json", QUESTIONNAIRE)
    write_json(args.output_dir / "distributions.json", {
        "disclaimer": "DADOS SINTÉTICOS DEMO; não representam pesquisa realizada.",
        "metodo": "marginais exatas; Q26/Q27/Q28 correlacionadas probabilisticamente apenas com Q10",
        "distributions": DISTRIBUTIONS,
        "senate_presence_q15_or_q16": SENATE_PRESENCE_TARGETS})
    write_json(args.output_dir / "agents-plan.json", {
        "source": "snapshot_read_only_2026-09-06", "agents": AGENTS, "acl_changes_performed": False})
    full_hash = digest(records)
    write_json(args.output_dir / "records-preview.json", {
        "total_records": 1000, "dataset_sha256": full_hash,
        "sample": records[:3] + records[249:251] + records[499:501] + records[749:751] + records[-3:]})
    with (args.output_dir / "records.jsonl").open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    matrices = {label: cross_matrix(records, label, "Q10") for label in ("Q26", "Q27", "Q28")}
    manifest = {
        "status": "PASS" if not errors else "FAIL", "mode": "LOGICAL_DRY_RUN_OFFLINE",
        "seed_run_id": str(run_id), "random_seed": args.seed,
        "project_id": args.project_id, "survey_id": args.survey_id,
        "records_validated": len(records), "answers_per_record": 20,
        "dataset_sha256": full_hash, "coordinates": None,
        "database_access": False, "database_writes": 0, "reverse_geocoding_calls": 0,
        "spatial_status": "BLOCKED", "spatial_reason": "máscara urbana de Macapá não disponível",
        "questionnaire_decisions": {"Q11": "ESCOLHA_SIMPLES", "Q17": "ESCOLHA_SIMPLES", "Q18": "MULTIPLA_ESCOLHA_EXATAMENTE_2"},
        "module_compatibility_q18": {"relatorio_simples": "NAO_SUPORTADO_CORRETAMENTE", "crosstab": "NAO_SUPORTADO_CORRETAMENTE", "inteligencia_multidimensional": "SUPORTADO", "mapa": "NAO_SUPORTADO_422"},
        "errors": errors}
    write_json(args.output_dir / "manifest.json", manifest)
    report = [
        "DRY-RUN LÓGICO PESQUISA360 DEMO AP", f"status={manifest['status']}",
        f"records={len(records)}/1000", "answers_per_record=20", f"dataset_sha256={full_hash}",
        "coordinates=NULL", "database_access=false", "database_writes=0", "reverse_geocoding_calls=0",
        "spatial_status=BLOCKED", 'spatial_reason="máscara urbana de Macapá não disponível"', "",
        "MATRIZES Q26/Q27/Q28 x Q10:", json.dumps(matrices, ensure_ascii=False, indent=2), "",
        "ERRORS:", *(errors or ["- none"])]
    (args.output_dir / "validation-report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "records": len(records), "output_dir": str(args.output_dir)}, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
