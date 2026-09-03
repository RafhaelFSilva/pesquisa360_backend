# -*- coding: utf-8 -*-
"""QA independente do MVP Potencial de Crescimento — oráculos via HTTP REAL (sem mocks).

REGRA CENTRAL: os valores esperados vêm do quadro oráculo (growth_qa_seed.py) e
de cálculo manual/independente — nunca de helpers do produto. O Wilson de
referência é implementado AQUI, de forma independente de statistics.py.

Pré-requisitos: banco QA DESCARTÁVEL semeado por growth_qa_seed.py e a API
rodando contra ele. P360_QA_DATABASE_URL é OBRIGATÓRIA (sem default) e
P360_QA_API_URL aponta para a API QA (default http://127.0.0.1:8055).
Dados e credenciais são 100% sintéticos. Roteiro completo: doc/20.
"""
import copy
import json
import math
import os
import sys
import time

import httpx
from sqlalchemy import create_engine, text

BASE = os.environ.get("P360_QA_API_URL", "http://127.0.0.1:8055")
_qa_db_url = os.environ.get("P360_QA_DATABASE_URL")
if not _qa_db_url:
    sys.exit(
        "Defina P360_QA_DATABASE_URL apontando para o banco QA DESCARTÁVEL "
        "(ex.: postgresql://postgres:qa@localhost:55432/p360qa). Abortado."
    )
DB = create_engine(_qa_db_url)
RESULTS = []
TIMINGS = []


def check(case, condition, detail=""):
    RESULTS.append((case, bool(condition), detail))
    if not condition:
        print(f"FAIL {case}: {detail}")


def login(email, senha="QA-senha-123!"):
    response = httpx.post(f"{BASE}/login/token", data={"username": email, "password": senha}, timeout=30)
    assert response.status_code == 200, f"login {email}: {response.status_code} {response.text[:200]}"
    return response.json()["access_token"]


def client(token):
    return httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {token}"}, timeout=180)


def growth_path(leaf, projeto=101, pesquisa=1001):
    return f"/projetos/{projeto}/pesquisas/{pesquisa}/inteligencia-eleitoral/potencial-crescimento/{leaf}"


# --- Wilson INDEPENDENTE (formulado aqui; nunca importa o produto) -----------
Z95 = 1.959963984540054  # quantil bilateral 95% da Normal padrão (literatura)


def wilson_qa(x, n, z=Z95):
    if n <= 0:
        return None
    p = x / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    low = 0.0 if x == 0 else max(0.0, center - half)
    high = 1.0 if x == n else min(1.0, center + half)
    return low, high


# Ancoras de literatura (Wilson 95%): 5/10 ~ (0.2366, 0.7634); 0/10 high ~ 0.2775.
lo, hi = wilson_qa(5, 10)
check("W-ANCHOR-5/10", abs(lo - 0.2366) < 2e-3 and abs(hi - 0.7634) < 2e-3, f"{lo},{hi}")
lo, hi = wilson_qa(0, 10)
check("W-ANCHOR-0/10", lo == 0.0 and abs(hi - 0.2775) < 2e-3, f"{lo},{hi}")
lo, hi = wilson_qa(10, 10)
check("W-ANCHOR-10/10", hi == 1.0 and abs(lo - (1 - 0.2775)) < 2e-3, f"{lo},{hi}")


def approx(a, b, tol=1e-9):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= tol


# --- configuração canônica base (pesquisa 1001) ------------------------------
def base_config(**overrides):
    payload = {
        "schema_version": 1,
        "pesquisa_id": 1001,
        "target": {
            "label": "Candidato A", "cargo": "Senador",
            "bindings": [
                {"question_id": 1101, "values": ["Candidato A"]},
                {"question_id": 1102, "values": ["Fulano A"]},
                {"question_id": 1103, "values": ["A. da Silva"]},
            ],
        },
        "scenario": {"label": "QA SINGLE", "ballot_selection_mode": "SINGLE",
                     "intention_questions": [{"question_id": 1101, "slot": "VOTO"}]},
        "intention_taxonomy": {
            "indeciso_declarado": ["Indeciso"], "branco_nulo": ["Branco/Nulo"],
            "ns_nr": ["NS/NR"], "nao_pretende_votar": ["Não pretende votar"],
        },
        "eligibility": {"include_indeciso": True, "include_branco_nulo": True,
                        "include_ns_nr": False, "include_nao_pretende_votar": False},
        "signals": [
            {"type": "REJECTION", "question_id": 1102},
            {"type": "SECOND_OPTION", "question_id": 1103},
            {"type": "VOTE_DECISION", "question_id": 1104,
             "decision_groups": {"mobile": ["Pode mudar"], "crystallized": ["Definitivo"]}},
        ],
        "profile_dimensions": [
            {"question_id": 1105, "label": "Sexo", "mode": "CATEGORICAL",
             "groups": [{"key": "FEM", "label": "Mulheres", "values": ["Feminino"]},
                        {"key": "MASC", "label": "Homens", "values": ["Masculino"]}]},
        ],
        "territory": {"level": "NONE"},
        "weighting": {"mode": "NAO_PONDERADO"},
        "minimum_base": {"suppress_below_n": 1, "warn_below_n": 2},  # SINTETICO
        "reference": {"type": "ELIGIBLE_UNIVERSE"},
        "uncertainty": {"method": "WILSON_AAS_APPROX", "confidence_level": 0.95},
    }
    payload.update(copy.deepcopy(overrides))
    return payload


def analisar(api, payload, projeto=101, pesquisa=1001, label=""):
    started = time.perf_counter()
    response = api.post(growth_path("analisar", projeto, pesquisa), json=payload)
    elapsed = time.perf_counter() - started
    if label:
        TIMINGS.append((label, round(elapsed * 1000), response.status_code))
    return response


def finding(body, key):
    matches = [f for f in body["findings"] if f["segment_key"] == key]
    assert matches, f"finding {key} ausente: {[f['segment_key'] for f in body['findings']]}"
    return matches[0]


def evidence(f, signal):
    matches = [e for e in f["evidences"] if e["signal_type"] == signal]
    assert matches, f"evidencia {signal} ausente"
    return matches[0]


# ============================================================================
# FASE A — feature gate real (inativa -> ativa sem entitlement -> concedida)
# ============================================================================
# idempotencia: recomeca o ciclo do gate do zero a cada execucao
with DB.begin() as conn:
    conn.execute(text("DELETE FROM modulo_entitlement_funcionalidades"))
    conn.execute(text("DELETE FROM modulo_entitlements"))
    conn.execute(text("UPDATE modulo_funcionalidades SET ativo=false WHERE chave='potencial_crescimento'"))

token_a = login("gerente.a@qa-p360.com.br")
token_b = login("gerente.b@qa-p360.com.br")
token_ag = login("agente.a@qa-p360.com.br")
A = client(token_a)
B = client(token_b)
AG = client(token_ag)

r = A.get(growth_path("opcoes-configuracao"))
check("A-GATE-1 feature inativa -> 404", r.status_code == 404 and "Capacidade" in r.json()["detail"], r.text[:120])

with DB.begin() as conn:
    conn.execute(text("UPDATE modulo_funcionalidades SET ativo=true WHERE chave='potencial_crescimento'"))
r = A.get(growth_path("opcoes-configuracao"))
check("A-GATE-2 ativa sem entitlement -> 403", r.status_code == 403, r.text[:120])

with DB.begin() as conn:
    conn.execute(text(
        "INSERT INTO modulo_entitlements (company_id, modulo_id, status, criado_em, atualizado_em) "
        "SELECT 11, id, 'ATIVO', now(), now() FROM modulos WHERE chave='inteligencia_eleitoral'"))
    conn.execute(text(
        "INSERT INTO modulo_entitlement_funcionalidades (entitlement_id, funcionalidade_id, criado_em) "
        "SELECT e.id, f.id, now() FROM modulo_entitlements e, modulo_funcionalidades f "
        "WHERE e.company_id=11 AND f.chave='potencial_crescimento'"))
r = A.get(growth_path("opcoes-configuracao"))
check("A-GATE-3 entitlement -> 200", r.status_code == 200, r.text[:120])

check("A-RBAC agente sem INTELIGENCIA_VER -> 403",
      AG.get(growth_path("opcoes-configuracao")).status_code == 403)
check("A-404<403 B pede recurso de A -> 404",
      B.get(growth_path("opcoes-configuracao")).status_code == 404)
check("A-403 B na propria pesquisa sem entitlement",
      B.get(growth_path("opcoes-configuracao", 201, 2001)).status_code == 403)
check("A-404 pesquisa inexistente", A.get(growth_path("opcoes-configuracao", 101, 99999)).status_code == 404)
check("A-404 pesquisa fora do projeto", A.get(growth_path("opcoes-configuracao", 201, 1001)).status_code == 404)
check("A-404 id negativo", A.get(growth_path("opcoes-configuracao", -1, 1001)).status_code == 404)

# ============================================================================
# FASE B — options (sem heurística; setores; municípios; constraints)
# ============================================================================
options = A.get(growth_path("opcoes-configuracao")).json()
q = {item["id"]: item for item in options["questions"]}
check("B-HEURISTICA texto enganoso sem compatibilidade",
      q[1110]["compatible_as"] == [], str(q[1110]["compatible_as"]))
check("B-INTENT 1101 compatível como INTENTION", "INTENTION" in q[1101]["compatible_as"])
check("B-ESPONTANEA valores = categorias ativas", q[1107]["values"] == ["Candidato A"], str(q[1107]["values"]))
setores = {s["id"]: s for s in options["territory"]["setores"]}
check("B-SETOR OPERACAO nao-analitico", setores[5004]["analytically_eligible"] is False)
check("B-SETOR analitico", setores[5001]["analytically_eligible"] is True)
check("B-MUNICIPIOS oficiais", sorted(m["id"] for m in options["territory"]["municipios"]) == [900, 901])
raw = json.dumps(options)
check("B-SEM-DEFAULTS", "suppress_below_n" not in raw and "max_uncategorized_rate" not in raw)

# ============================================================================
# FASE C — ORÁCULO PRINCIPAL (valores calculados manualmente no qa_seed)
# ============================================================================
r = A.post(growth_path("validar-configuracao"), json=base_config())
check("C-VALIDA 200 valid=true", r.status_code == 200 and r.json()["valid"] is True, r.text[:200])

r = analisar(A, base_config(), label="oraculo-20")
check("C-ANALISA 200", r.status_code == 200, r.text[:200])
body = r.json()
u = body["universe"]
check("C-U survey=20", u["survey_n"] == 20, str(u))
check("C-U analytical=20", u["analytical_n"] == 20)
check("C-U supporters=2", u["current_target_supporters_n"] == 2)
check("C-U missing=2 (sem resposta NAO vira indeciso)", u["technical_missing_intention_n"] == 2)
check("C-U excluded_special=2", u["excluded_special_n"] == 2)
check("C-U eligible=14", u["eligible_n"] == 14)
counts = u["intention_classification_counts"]
check("C-U counts", counts.get("REGULAR_ELIGIBLE") == 11 and counts.get("INDECISO") == 2
      and counts.get("BRANCO_NULO") == 1 and counts.get("NS_NR") == 1
      and counts.get("NAO_PRETENDE_VOTAR") == 1, str(counts))

fem = finding(body, "profile:1105=FEM")
masc = finding(body, "profile:1105=MASC")
check("C-FEM n=8", fem["n_bruto"] == 8)
check("C-MASC n=6", masc["n_bruto"] == 6)
check("C-PARTICIPACAO usa eligible_n", approx(fem["participation_rate"], 8 / 14)
      and not approx(fem["participation_rate"], 8 / 20), str(fem["participation_rate"]))
check("C-WEIGHTED null", fem["weighted_base"] is None and masc["weighted_base"] is None)

# SEGUNDA OPCAO (recontagem manual corrigida — 9010 responde 'B. de Souza'):
# ref = {3,4,5,7,8,9,10,12,13} = 9, favoraveis {3,5,7,9,12} = 5 -> 5/9
# FEM {3,4,5,12}=4 fav 3 -> .75; MASC {7,8,9,10,13}=5 fav 2 -> .4
so_f, so_m = evidence(fem, "SECOND_OPTION"), evidence(masc, "SECOND_OPTION")
check("C-2A ref base=9 num=5", so_f["reference_base_n"] == 9 and so_f["reference_numerator"] == 5, str(so_f))
check("C-2A FEM 3/4 rate .75", so_f["segment_numerator"] == 3 and so_f["segment_base_n"] == 4
      and approx(so_f["segment_rate"], 0.75))
check("C-2A FEM delta +19.44pp", approx(so_f["delta_pp"], (0.75 - 5 / 9) * 100, 1e-6))
check("C-2A FEM lift 1.35", approx(so_f["lift"], 0.75 / (5 / 9), 1e-9))
check("C-2A FEM FAVORABLE", so_f["observed_direction"] == "FAVORABLE")
check("C-2A MASC 2/5 delta -15.56 UNFAVORABLE", so_m["segment_numerator"] == 2
      and so_m["segment_base_n"] == 5 and approx(so_m["delta_pp"], (0.4 - 5 / 9) * 100, 1e-6)
      and so_m["observed_direction"] == "UNFAVORABLE")

# REJEICAO: multipla conta ENTREVISTA. ref 3/7; FEM 2/4; MASC 1/3
rj_f, rj_m = evidence(fem, "REJECTION"), evidence(masc, "REJECTION")
check("C-REJ multipla: ref base=7 (nao 10 respostas)", rj_f["reference_base_n"] == 7, str(rj_f))
check("C-REJ ref num=3 (9005 triplo conta 1)", rj_f["reference_numerator"] == 3)
check("C-REJ FEM 2/4 rate .5", rj_f["segment_numerator"] == 2 and rj_f["segment_base_n"] == 4
      and approx(rj_f["segment_rate"], 0.5))
check("C-REJ FEM delta +50/7pp UNFAVORABLE", approx(rj_f["delta_pp"], (0.5 - 3 / 7) * 100, 1e-6)
      and rj_f["observed_direction"] == "UNFAVORABLE")
check("C-REJ MASC 1/3 (dup 9008 conta 1 na base)", rj_m["segment_numerator"] == 1 and rj_m["segment_base_n"] == 3)
check("C-REJ MASC FAVORABLE delta negativo cru", rj_m["observed_direction"] == "FAVORABLE"
      and approx(rj_m["delta_pp"], (1 / 3 - 3 / 7) * 100, 1e-6) and rj_m["delta_pp"] < 0)
check("C-REJ taxa nao invertida (nunca 1-x)", approx(rj_m["segment_rate"], 1 / 3, 1e-9))

# DECISAO: 'Talvez' fora da base valida. ref 3/6; FEM 2/4; MASC 1/2
vd_f, vd_m = evidence(fem, "VOTE_DECISION"), evidence(masc, "VOTE_DECISION")
check("C-VD ref base=6 (Talvez fora)", vd_f["reference_base_n"] == 6 and vd_f["reference_numerator"] == 3, str(vd_f))
check("C-VD FEM 2/4 NEUTRAL", vd_f["segment_numerator"] == 2 and vd_f["segment_base_n"] == 4
      and vd_f["observed_direction"] == "NEUTRAL" and approx(vd_f["delta_pp"], 0.0))
check("C-VD MASC 1/2", vd_m["segment_numerator"] == 1 and vd_m["segment_base_n"] == 2)

# WILSON independente em TODAS as evidencias disponiveis
for name, ev in (("2A-FEM", so_f), ("2A-MASC", so_m), ("REJ-FEM", rj_f), ("REJ-MASC", rj_m),
                 ("VD-FEM", vd_f), ("VD-MASC", vd_m)):
    for side, x_key, n_key, i_key in (("seg", "segment_numerator", "segment_base_n", "segment_interval"),
                                      ("ref", "reference_numerator", "reference_base_n", "reference_interval")):
        expected = wilson_qa(ev[x_key], ev[n_key])
        got = ev[i_key]
        check(f"C-WILSON {name} {side}", got is not None and approx(got["low"], expected[0], 1e-9)
              and approx(got["high"], expected[1], 1e-9), f"{got} vs {expected}")

warn_codes = {w["code"] for w in body["warnings"]}
check("C-WARN globais", {"UNWEIGHTED_ANALYSIS", "SRS_ASSUMPTION", "REFERENCE_INCLUDES_SEGMENT",
                          "MULTIPLE_COMPARISONS_EXPLORATORY"} <= warn_codes, str(warn_codes))
raw_body = json.dumps(body, ensure_ascii=False).lower()
for term in ["significan", "p-value", "p_valor", "score", "ranking", "projected", "potential_votes",
             "vai votar", "chance de convers", "95% de certeza"]:
    check(f"C-LINGUAGEM sem '{term}'", term not in raw_body)

# referencia INCLUSIVA: base ref = elegiveis com resposta (contem o segmento)
check("C-REF inclui segmento", so_f["reference_base_n"] == so_f["segment_base_n"] + so_m["segment_base_n"])

# ============================================================================
# FASE D — zero real vs ausencia; lift nulo; espontanea; binding removido
# ============================================================================
cfg = base_config()
cfg["target"]["bindings"][1] = {"question_id": 1102, "values": ["Fulano C"]}
body_d = analisar(A, cfg).json()
rj_m0 = evidence(finding(body_d, "profile:1105=MASC"), "REJECTION")
check("D-ZERO-REAL rate=0.0 (nao null)", rj_m0["status"] == "AVAILABLE"
      and rj_m0["segment_numerator"] == 0 and approx(rj_m0["segment_rate"], 0.0)
      and rj_m0["segment_rate"] is not None, str(rj_m0))

cfg = base_config(signals=[{"type": "SECOND_OPTION", "question_id": 1114}])
cfg["target"]["bindings"] = [
    {"question_id": 1101, "values": ["Candidato A"]},
    {"question_id": 1114, "values": ["A. da Silva"]},
]
body_d = analisar(A, cfg).json()
so_un = evidence(finding(body_d, "profile:1105=FEM"), "SECOND_OPTION")
check("D-AUSENCIA base=0 -> SIGNAL_UNAVAILABLE rate null", so_un["status"] == "SIGNAL_UNAVAILABLE"
      and so_un["reference_base_n"] == 0 and so_un["segment_rate"] is None
      and so_un["delta_pp"] is None and so_un["lift"] is None, str(so_un))

cfg = base_config(signals=[{"type": "SECOND_OPTION", "question_id": 1103}])
cfg["target"]["bindings"] = [
    {"question_id": 1101, "values": ["Candidato A"]},
    {"question_id": 1103, "values": ["NS/NR"]},
]
body_d = analisar(A, cfg).json()
so_l = evidence(finding(body_d, "profile:1105=FEM"), "SECOND_OPTION")
lift_warns = {w["code"] for w in so_l["warnings"]}
check("D-LIFT ref=0 -> null + warning", approx(so_l["reference_rate"], 0.0) and so_l["lift"] is None
      and "LIFT_UNDEFINED_REFERENCE_ZERO" in lift_warns, str(so_l))

def spont_cfg(threshold):
    cfg = base_config(signals=[{"type": "SECOND_OPTION", "question_id": 1107}])
    cfg["target"]["bindings"] = [
        {"question_id": 1101, "values": ["Candidato A"]},
        {"question_id": 1107, "values": ["Candidato A"]},
    ]
    cfg["spontaneous_quality"] = {"max_uncategorized_rate": threshold}
    return cfg

body_d = analisar(A, spont_cfg(0.5)).json()
so_sp = evidence(finding(body_d, "profile:1105=FEM"), "SECOND_OPTION")
check("D-ESPONTANEA abaixo do limiar utilizavel (ref 3/4)", so_sp["status"] == "AVAILABLE"
      and so_sp["reference_base_n"] == 4 and so_sp["reference_numerator"] == 3, str(so_sp))
body_d = analisar(A, spont_cfg(0.25)).json()
so_sp = evidence(finding(body_d, "profile:1105=FEM"), "SECOND_OPTION")
check("D-ESPONTANEA borda rate==threshold utilizavel", so_sp["status"] == "AVAILABLE", str(so_sp["status"]))
body_d = analisar(A, spont_cfg(0.2)).json()
so_sp = evidence(finding(body_d, "profile:1105=FEM"), "SECOND_OPTION")
check("D-ESPONTANEA acima -> UNAVAILABLE_QUALITY", so_sp["status"] == "SIGNAL_UNAVAILABLE_QUALITY"
      and "HIGH_UNCATEGORIZED_RATE" in {w["code"] for w in body_d["warnings"]}, str(so_sp["status"]))

cfg = base_config()
cfg["target"]["bindings"] = [b for b in cfg["target"]["bindings"] if b["question_id"] != 1102]
r = A.post(growth_path("validar-configuracao"), json=cfg)
codes = {e["code"] for e in r.json()["errors"]}
check("D-BINDING removido -> MISSING_TARGET_BINDING (sem fallback por nome)",
      r.status_code == 200 and r.json()["valid"] is False and "MISSING_TARGET_BINDING" in codes, str(codes))
r = analisar(A, cfg)
check("D-BINDING analisar -> 422 tipado", r.status_code == 422
      and r.json()["detail"]["code"] == "GROWTH_CONFIGURATION_INVALID", r.text[:150])

# ============================================================================
# FASE E — território PostGIS real (borda, sobreposição, sem coordenada)
# ============================================================================
with DB.connect() as conn:
    covers = conn.execute(text(
        "SELECT ST_Covers(geometria, ST_SetSRID(ST_Point(0,0.5),4326)), "
        "ST_Contains(geometria, ST_SetSRID(ST_Point(0,0.5),4326)) FROM setores WHERE id=5001"
    )).fetchone()
check("E-BORDA ST_Covers=true (ST_Contains seria false)", covers[0] is True and covers[1] is False, str(covers))

body_e = analisar(A, base_config(territory={"level": "SETOR"}), label="territorio-setor").json()
keys = {f["segment_key"]: f["n_bruto"] for f in body_e["findings"]}
check("E-SETOR findings esperados", keys == {
    "profile:1105=FEM|territory:setor=5001": 6,
    "profile:1105=MASC|territory:setor=5001": 1,
    "profile:1105=MASC|territory:setor=5002": 3,
    "profile:1105=FEM|territory:setor=5003": 1,
}, str(keys))
diag = body_e["territory_diagnostics"]
check("E-DIAG sobreposto/sem_setor/sem_coord = 1/1/1",
      diag["conflito_setor_n"] == 1 and diag["sem_setor_n"] == 1 and diag["sem_coordenada_n"] == 1, str(diag))
check("E-COVERAGE 3 fora dos segmentos", body_e["coverage"]["not_segmented_n"] == 3)
check("E-BORDA 9006 dentro de FEM|5001 (n=6 inclui a borda)",
      keys["profile:1105=FEM|territory:setor=5001"] == 6)

body_e = analisar(A, base_config(territory={"level": "MUNICIPIO", "include_electoral_context": True}),
                  label="territorio-municipio").json()
keys = {f["segment_key"]: (f["n_bruto"], (f["territory"] or {}).get("eleitorado_apto")) for f in body_e["findings"]}
check("E-MUNICIPIO findings + contexto", keys == {
    "profile:1105=FEM|territory:municipio=900": (6, 50000),
    "profile:1105=MASC|territory:municipio=900": (1, 50000),
    "profile:1105=MASC|territory:municipio=901": (3, 30000),
}, str(keys))
check("E-MUNICIPIO nao resolvido diagnosticado",
      body_e["territory_diagnostics"]["municipio_nao_resolvido_n"] == 1, str(body_e["territory_diagnostics"]))
mun_rates = evidence(finding(body_e, "profile:1105=FEM|territory:municipio=900"), "SECOND_OPTION")

# eleitorado NAO e peso: mudar drasticamente nao altera taxas
with DB.begin() as conn:
    conn.execute(text("UPDATE territorio_eleitoral SET eleitorado_apto=999999 WHERE id=900"))
body_e2 = analisar(A, base_config(territory={"level": "MUNICIPIO", "include_electoral_context": True})).json()
mun2 = evidence(finding(body_e2, "profile:1105=FEM|territory:municipio=900"), "SECOND_OPTION")
ctx2 = finding(body_e2, "profile:1105=FEM|territory:municipio=900")["territory"]["eleitorado_apto"]
check("E-ELEITORADO nao altera taxas (so contexto)",
      approx(mun2["segment_rate"], mun_rates["segment_rate"]) and approx(mun2["delta_pp"], mun_rates["delta_pp"])
      and ctx2 == 999999, f"ctx={ctx2}")
with DB.begin() as conn:
    conn.execute(text("UPDATE territorio_eleitoral SET eleitorado_apto=50000 WHERE id=900"))

# ============================================================================
# FASE F — determinismo, hashes, fingerprint, cotas
# ============================================================================
b1 = analisar(A, base_config()).json()
b2 = analisar(A, base_config()).json()
fp1 = b1["snapshot"]["input_fingerprint"]
for b in (b1, b2):
    b["snapshot"].pop("executed_at")
check("F-DETERMINISMO identico exceto executed_at", b1 == b2)
check("F-HASH mesmo config -> mesmo hash",
      b1["snapshot"]["configuration_hash"] == b2["snapshot"]["configuration_hash"])
b3 = analisar(A, base_config(minimum_base={"suppress_below_n": 2, "warn_below_n": 3})).json()
check("F-HASH config diferente -> hash diferente",
      b3["snapshot"]["configuration_hash"] != b1["snapshot"]["configuration_hash"])

with DB.begin() as conn:  # alteracao IRRELEVANTE (pergunta 1115 nao-analitica)
    conn.execute(text("UPDATE respostas SET valor_resposta='observacao ALTERADA' "
                      "WHERE pergunta_id=1115 AND coleta_id=9003"))
b4 = analisar(A, base_config()).json()
check("F-FP alteracao irrelevante nao muda fingerprint (politica doc/16)",
      b4["snapshot"]["input_fingerprint"] == fp1)

with DB.begin() as conn:  # alteracao RELEVANTE (2a opcao do 9013)
    conn.execute(text("UPDATE respostas SET valor_resposta='A. da Silva' "
                      "WHERE pergunta_id=1103 AND coleta_id=9013"))
b5 = analisar(A, base_config()).json()
so_m5 = evidence(finding(b5, "profile:1105=MASC"), "SECOND_OPTION")
check("F-FP alteracao relevante muda fingerprint + metrica",
      b5["snapshot"]["input_fingerprint"] != fp1 and so_m5["segment_numerator"] == 3, str(so_m5["segment_numerator"]))
with DB.begin() as conn:
    conn.execute(text("UPDATE respostas SET valor_resposta='B. de Souza' "
                      "WHERE pergunta_id=1103 AND coleta_id=9013"))
b6 = analisar(A, base_config()).json()
check("F-FP reversivel", b6["snapshot"]["input_fingerprint"] == fp1)

# cotas NAO sao pesos
with DB.begin() as conn:
    conn.execute(text(
        "INSERT INTO planos_cota_perfil (pesquisa_id, company_id, ativo, pergunta_sexo_id, pergunta_idade_id, modo_idade, sexo_valores, criado_em, atualizado_em) "
        "VALUES (1001, 11, true, 1105, 1106, 'NUMERICA', '{\"MASCULINO\": [\"Masculino\"], \"FEMININO\": [\"Feminino\"]}', now(), now())"))
    conn.execute(text(
        "INSERT INTO cotas_perfil (plano_id, territorio_eleitoral_id, sexo, faixa_rotulo, idade_min, idade_max, meta, ordem) "
        "SELECT id, 900, 'FEMININO', '18+', 18, NULL, 99999, 1 FROM planos_cota_perfil WHERE pesquisa_id=1001"))
b7 = analisar(A, base_config()).json()
b7["snapshot"].pop("executed_at")
check("F-COTAS nao alteram o calculo", b7 == b1)
with DB.begin() as conn:
    conn.execute(text("DELETE FROM cotas_perfil"))
    conn.execute(text("DELETE FROM planos_cota_perfil"))

# ============================================================================
# FASE G — ballot modes (pesquisa 1002)
# ============================================================================
mult_cfg = {
    "schema_version": 1, "pesquisa_id": 1002,
    "target": {"label": "Candidato A", "cargo": "Senador",
               "bindings": [{"question_id": 1201, "values": ["Candidato A"]}]},
    "scenario": {"label": "QA MULTIPLE", "ballot_selection_mode": "MULTIPLE",
                 "intention_questions": [{"question_id": 1201, "slot": "VOTOS"}]},
    "intention_taxonomy": {"indeciso_declarado": ["Indeciso"], "ns_nr": ["NS/NR"],
                            "branco_nulo": [], "nao_pretende_votar": []},
    "eligibility": {"include_indeciso": True, "include_branco_nulo": False,
                    "include_ns_nr": False, "include_nao_pretende_votar": False},
    "signals": [],
    "profile_dimensions": [{"question_id": 1205, "label": "Sexo", "mode": "CATEGORICAL",
                             "groups": [{"key": "FEM", "label": "Mulheres", "values": ["Feminino"]}]}],
    "territory": {"level": "NONE"}, "weighting": {"mode": "NAO_PONDERADO"},
    "minimum_base": {"suppress_below_n": 1, "warn_below_n": 2},
    "reference": {"type": "ELIGIBLE_UNIVERSE"},
    "uncertainty": {"method": "WILSON_AAS_APPROX", "confidence_level": 0.95},
}
body_g = analisar(A, mult_cfg, pesquisa=1002).json()
u = body_g["universe"]
check("G-MULTIPLE alvo em qualquer posicao = apoiador", u["current_target_supporters_n"] == 1, str(u))
check("G-MULTIPLE eligible=1 / conflito=1", u["eligible_n"] == 1 and u["mixed_special_conflict_n"] == 1)
check("G-MIXED warning conservador",
      "MIXED_SPECIAL_ELIGIBILITY" in {w["code"] for w in body_g["warnings"]})

ord_cfg = copy.deepcopy(mult_cfg)
ord_cfg["target"]["bindings"] = [
    {"question_id": 1202, "values": ["Candidato A"]},
    {"question_id": 1203, "values": ["Candidato A"]},
]
ord_cfg["scenario"] = {"label": "QA ORDERED", "ballot_selection_mode": "ORDERED_MULTIPLE",
                       "intention_questions": [
                           {"question_id": 1202, "slot": "VOTO_1", "order": 1},
                           {"question_id": 1203, "slot": "VOTO_2", "order": 2}]}
ord_cfg["intention_taxonomy"] = {"indeciso_declarado": [], "branco_nulo": [], "ns_nr": [], "nao_pretende_votar": []}
body_g = analisar(A, ord_cfg, pesquisa=1002).json()
u = body_g["universe"]
check("G-ORDERED alvo em qualquer slot = apoiador (2)", u["current_target_supporters_n"] == 2, str(u))
check("G-ORDERED eligible=1", u["eligible_n"] == 1)

# ============================================================================
# FASE H — mass assignment / malformado / cross-tenant
# ============================================================================
for field in ("company_id", "tenant_id", "campo_desconhecido"):
    payload = base_config(); payload[field] = 999
    check(f"H-422 {field} rejeitado",
          A.post(growth_path("validar-configuracao"), json=payload).status_code == 422)
r = A.post(growth_path("validar-configuracao"),
           content="{invalido", headers={"Content-Type": "application/json"})
check("H-422 JSON malformado", r.status_code == 422)
payload = base_config(weighting={"mode": "PONDERADO"})
check("H-422 enum invalido", A.post(growth_path("validar-configuracao"), json=payload).status_code == 422)
payload = base_config(); payload["scenario"]["intention_questions"][0]["question_id"] = 2101
payload["target"]["bindings"][0] = {"question_id": 2101, "values": ["X"]}
r = A.post(growth_path("validar-configuracao"), json=payload)
codes = {e["code"] for e in r.json().get("errors", [])}
check("H-CROSS pergunta B nao vaza existencia",
      r.status_code == 200 and "QUESTION_NOT_FOUND" in codes and "QUESTION_FROM_OTHER_SURVEY" not in codes, str(codes))
payload = base_config(territory={"level": "SETOR", "setor_ids": [5101]})
r = A.post(growth_path("validar-configuracao"), json=payload)
check("H-CROSS setor B -> SETOR_NOT_FOUND",
      "SETOR_NOT_FOUND" in {e["code"] for e in r.json().get("errors", [])}, r.text[:150])
payload = base_config()
payload["target"]["bindings"][0]["values"] = [f"Valor {i}" for i in range(500)]
r = A.post(growth_path("validar-configuracao"), json=payload)
check("H-LISTA grande -> 200 valid=false sem 500", r.status_code == 200 and r.json()["valid"] is False)

# ============================================================================
# FASE I — stress e SEGMENT_LIMIT
# ============================================================================
def stress_cfg(two_dims):
    dims = [{"question_id": 1302, "label": "P1", "mode": "CATEGORICAL",
             "groups": [{"key": f"V{i:02d}", "label": f"V{i:02d}", "values": [f"V{i:02d}"]} for i in range(25)]}]
    if two_dims:
        dims.append({"question_id": 1303, "label": "P2", "mode": "CATEGORICAL",
                     "groups": [{"key": f"W{i:02d}", "label": f"W{i:02d}", "values": [f"W{i:02d}"]} for i in range(20)]})
    return {
        "schema_version": 1, "pesquisa_id": 1003,
        "target": {"label": "Candidato A", "cargo": "QA",
                   "bindings": [{"question_id": 1301, "values": ["Candidato A"]}]},
        "scenario": {"label": "stress", "ballot_selection_mode": "SINGLE",
                     "intention_questions": [{"question_id": 1301, "slot": "VOTO"}]},
        "intention_taxonomy": {"indeciso_declarado": [], "branco_nulo": [], "ns_nr": [], "nao_pretende_votar": []},
        "eligibility": {"include_indeciso": True, "include_branco_nulo": True,
                        "include_ns_nr": False, "include_nao_pretende_votar": False},
        "signals": [],
        "profile_dimensions": dims,
        "territory": {"level": "NONE"}, "weighting": {"mode": "NAO_PONDERADO"},
        "minimum_base": {"suppress_below_n": 1, "warn_below_n": 2},
        "reference": {"type": "ELIGIBLE_UNIVERSE"},
        "uncertainty": {"method": "WILSON_AAS_APPROX", "confidence_level": 0.95},
    }

body_i = analisar(A, stress_cfg(False), pesquisa=1003, label="stress-25seg-2400col").json()
check("I-STRESS 25 findings / eligible=2340", len(body_i["findings"]) == 25
      and body_i["universe"]["eligible_n"] == 2340, f"{len(body_i['findings'])}/{body_i['universe']['eligible_n']}")
body_i = analisar(A, stress_cfg(True), pesquisa=1003, label="stress-100seg-2400col").json()
check("I-STRESS 100 findings observados (25x20 -> 100 combos)", len(body_i["findings"]) == 100,
      str(len(body_i["findings"])))
keys_sorted = [f["segment_key"] for f in body_i["findings"]]
check("I-ORDEM deterministica", keys_sorted == sorted(keys_sorted))

# SEGMENT_LIMIT via segundo uvicorn (porta 8056, P360_GROWTH_MAX_SEGMENTS=50)
try:
    limited = httpx.Client(base_url="http://127.0.0.1:8056",
                           headers={"Authorization": f"Bearer {token_a}"}, timeout=120)
    r = limited.post(growth_path("analisar", 101, 1003), json=stress_cfg(True))
    check("I-LIMIT 422 SEGMENT_LIMIT_EXCEEDED", r.status_code == 422
          and r.json()["detail"]["code"] == "SEGMENT_LIMIT_EXCEEDED", r.text[:150])
except Exception as exc:  # segundo servidor pode nao estar de pe
    check("I-LIMIT 422 SEGMENT_LIMIT_EXCEEDED", False, f"servidor 8056 indisponivel: {exc}")

# ============================================================================
print("\n===== TIMINGS (ms) =====")
for label, ms, status in TIMINGS:
    print(f"  {label}: {ms} ms (HTTP {status})")
total = len(RESULTS)
fails = [r for r in RESULTS if not r[1]]
print(f"\n===== QA HTTP: {total - len(fails)}/{total} PASS =====")
for case, _, detail in fails:
    print(f"  FAIL {case}: {detail}")
sys.exit(1 if fails else 0)
