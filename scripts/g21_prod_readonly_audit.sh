#!/usr/bin/env bash
# Pesquisa360 - G.2.1 auditoria PROD
# Objetivo: reconfirmar base Backend, auditar canonicalizacao e inventariar Web.
#
# SEGURANCA:
# - O UNICO write esperado em PROD e a propria transferencia deste arquivo via SCP.
# - Este script nao aplica patch, nao faz migration, nao reinicia containers e nao altera DB.
# - A transacao usada na auditoria do PostgreSQL e explicitamente READ ONLY e termina em ROLLBACK.
# - Nao imprime DATABASE_URL, senhas, tokens, cookies ou variaveis de ambiente da aplicacao.

set -uo pipefail
export GIT_OPTIONAL_LOCKS=0

EXPECTED_HEAD="${EXPECTED_HEAD:-11b76e7b26ce57815865d88682128d46c87263eb}"
EXPECTED_ALEMBIC="${EXPECTED_ALEMBIC:-b2c3d4e5f6a7}"
API_CONTAINER="${API_CONTAINER:-pesquisa360_api}"
PESQUISA_ID="${PESQUISA_ID:-2}"
PERGUNTA_ID="${PERGUNTA_ID:-46}"
SETOR_NOME="${SETOR_NOME:-Cutias do Araguari}"
KNOWN_WEB_BUNDLE="${KNOWN_WEB_BUNDLE:-index-mt6zcbde-ktbk3r-DjOgQwG8.js}"

say() {
  printf '%s\n' "$*"
}

section() {
  printf '\n======================================================================\n'
  printf '%s\n' "$*"
  printf '======================================================================\n'
}

have() {
  command -v "$1" >/dev/null 2>&1
}

section "G.2.1 - AUDITORIA PROD READ ONLY"
say "timestamp_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || true)"
say "whoami=$(whoami 2>/dev/null || true)"
say "hostname=$(hostname 2>/dev/null || true)"
say "pwd=$(pwd 2>/dev/null || true)"
say "expected_head=$EXPECTED_HEAD"
say "expected_alembic=$EXPECTED_ALEMBIC"
say "api_container=$API_CONTAINER"
say "pesquisa_id=$PESQUISA_ID"
say "pergunta_id=$PERGUNTA_ID"
say "setor_nome=$SETOR_NOME"

section "1. CONTAINER BACKEND ATUAL"
if ! have docker; then
  say "ERRO: docker nao encontrado."
  exit 20
fi

if ! docker inspect "$API_CONTAINER" >/dev/null 2>&1; then
  say "ERRO: container '$API_CONTAINER' nao encontrado."
  docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' 2>/dev/null || true
  exit 21
fi

docker ps --filter "name=^/${API_CONTAINER}$" --format 'name={{.Names}} image={{.Image}} status={{.Status}}' || true
docker inspect "$API_CONTAINER" \
  --format 'image_id={{.Image}} started_at={{.State.StartedAt}} running={{.State.Running}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}' \
  2>/dev/null || true

section "2. RELEASE / GIT / CRUD SHA256"

RELEASE_PATH=""
for candidate in \
  "/opt/pesquisa360-releases/$EXPECTED_HEAD" \
  "/opt/pesquisa360"
do
  if [ -d "$candidate/.git" ] || git -C "$candidate" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    RELEASE_PATH="$candidate"
    break
  fi
done

if [ -n "$RELEASE_PATH" ]; then
  say "release_candidate=$RELEASE_PATH"
  ACTUAL_HEAD="$(git -C "$RELEASE_PATH" rev-parse HEAD 2>/dev/null || true)"
  say "release_head=$ACTUAL_HEAD"
  say "release_status_begin"
  git -C "$RELEASE_PATH" status --short 2>/dev/null || true
  say "release_status_end"
  if [ -f "$RELEASE_PATH/pesquisa360/crud.py" ]; then
    if have sha256sum; then
      CRUD_SHA="$(sha256sum "$RELEASE_PATH/pesquisa360/crud.py" | awk '{print $1}')"
      say "crud_sha256=$CRUD_SHA"
    fi
  else
    say "crud_sha256=INDISPONIVEL (arquivo nao encontrado no release)"
  fi
  if [ "$ACTUAL_HEAD" = "$EXPECTED_HEAD" ]; then
    say "backend_head_corresponde_g2=SIM"
  else
    say "backend_head_corresponde_g2=NAO"
  fi

  say "helper_usage_begin"
  grep -RIn --exclude-dir=.git --include='*.py' \
    'resolve_reportable_response_value' \
    "$RELEASE_PATH/pesquisa360" 2>/dev/null | head -80 || true
  say "helper_usage_end"
else
  say "release_candidate=NAO_LOCALIZADO"
  say "backend_head_corresponde_g2=DESCONHECIDO"
fi

section "3. AUDITORIA DO BANCO - TRANSACAO READ ONLY"

# Executa o Python dentro do container para usar exatamente o DATABASE_URL ja configurado,
# sem exibir o valor. O Python roda com -B e PYTHONDONTWRITEBYTECODE=1.
docker exec -i \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e G21_PESQUISA_ID="$PESQUISA_ID" \
  -e G21_PERGUNTA_ID="$PERGUNTA_ID" \
  -e G21_SETOR_NOME="$SETOR_NOME" \
  -e G21_EXPECTED_ALEMBIC="$EXPECTED_ALEMBIC" \
  "$API_CONTAINER" python -B - <<'PY'
import importlib
import os
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from sqlalchemy import text

PESQUISA_ID = int(os.environ.get("G21_PESQUISA_ID", "2"))
PERGUNTA_ID = int(os.environ.get("G21_PERGUNTA_ID", "46"))
SETOR_NOME = os.environ.get("G21_SETOR_NOME", "Cutias do Araguari")
EXPECTED_ALEMBIC = os.environ.get("G21_EXPECTED_ALEMBIC", "b2c3d4e5f6a7")

def out(key, value):
    print(f"{key}={value}")

# Importa o engine da propria aplicacao sem imprimir DATABASE_URL.
from pesquisa360.db.session import engine

# Usa a mesma normalizacao ja existente no projeto.
from pesquisa360.utils.response_normalization import normalizar_resposta_espontanea

def normalizar_chave_categoria(value):
    if value is None:
        return ""
    return normalizar_resposta_espontanea(str(value))

# Tenta usar as funcoes oficiais de tipo da base PROD.
is_categorical_question_type = None
is_multiple_response_question_type = None
for mod_name in ("pesquisa360.question_types", "pesquisa360.core.question_types"):
    try:
        mod = importlib.import_module(mod_name)
        is_categorical_question_type = getattr(mod, "is_categorical_question_type", None)
        is_multiple_response_question_type = getattr(mod, "is_multiple_response_question_type", None)
        if is_categorical_question_type:
            break
    except Exception:
        pass

# Fallback conservador apenas se o modulo oficial nao puder ser importado.
def _norm_type(v):
    return str(v or "").strip().replace("-", "_").replace(" ", "_").upper()

SINGLE_FALLBACK = {
    "ESCOLHA_SIMPLES",
    "MULTIPLAESCOLHA_UNICA",
    "MULTIPLA_ESCOLHA_UNICA",
}
MULTI_FALLBACK = {
    "MULTIPLA_ESCOLHA",
    "MULTIPLAESCOLHA_MULTIPLA",
    "MULTIPLA_ESCOLHA_MULTIPLA",
}

def tipo_categorico(tipo):
    if is_categorical_question_type:
        try:
            return bool(is_categorical_question_type(tipo))
        except Exception:
            pass
    t = _norm_type(tipo)
    return t in SINGLE_FALLBACK or t in MULTI_FALLBACK

def tipo_multiplo(tipo):
    if is_multiple_response_question_type:
        try:
            return bool(is_multiple_response_question_type(tipo))
        except Exception:
            pass
    return _norm_type(tipo) in MULTI_FALLBACK

def safe_pct(n, d):
    if not d:
        return Decimal("0")
    return (Decimal(n) * Decimal("100") / Decimal(d)).quantize(Decimal("0.01"))

conn = engine.connect()
tx = conn.begin()
try:
    # Deve ser a primeira instrucao SQL da transacao.
    conn.execute(text("SET TRANSACTION READ ONLY"))

    print("db_transaction=READ ONLY")
    print("db_write_intent=NONE")

    # Alembic por SELECT, sem chamar upgrade/current.
    alembic_rows = [r[0] for r in conn.execute(text("SELECT version_num FROM alembic_version")).fetchall()]
    out("alembic_versions", ",".join(alembic_rows))
    out("alembic_expected", EXPECTED_ALEMBIC)
    out("alembic_corresponde_g2", "SIM" if alembic_rows == [EXPECTED_ALEMBIC] else "NAO")

    # Perguntas + opcoes.
    rows = conn.execute(text("""
        SELECT
            p.id AS pergunta_id,
            p.pesquisa_id,
            p.texto_pergunta,
            p.tipo_pergunta,
            COALESCE(p.eh_resposta_espontanea, FALSE) AS espontanea,
            COALESCE(p.ativo, TRUE) AS ativo,
            o.id AS opcao_id,
            o.texto AS opcao_texto
        FROM perguntas p
        LEFT JOIN opcoes o ON o.pergunta_id = p.id
        WHERE COALESCE(p.ativo, TRUE) = TRUE
        ORDER BY p.id, o.ordem NULLS LAST, o.id
    """)).mappings().all()

    perguntas = {}
    for r in rows:
        p = perguntas.setdefault(
            r["pergunta_id"],
            {
                "id": r["pergunta_id"],
                "pesquisa_id": r["pesquisa_id"],
                "texto": r["texto_pergunta"],
                "tipo": r["tipo_pergunta"],
                "espontanea": bool(r["espontanea"]),
                "opcoes": [],
            },
        )
        if r["opcao_id"] is not None:
            p["opcoes"].append(str(r["opcao_texto"]))

    auditaveis = {
        pid: p for pid, p in perguntas.items()
        if tipo_categorico(p["tipo"]) and not tipo_multiplo(p["tipo"]) and not p["espontanea"]
    }

    out("perguntas_ativas_total", len(perguntas))
    out("perguntas_single_categoricas_auditadas", len(auditaveis))

    # Mapas canonicos e colisoes.
    canon_maps = {}
    collisions = {}
    total_options = 0
    for pid, p in auditaveis.items():
        by_key = defaultdict(set)
        for label in p["opcoes"]:
            total_options += 1
            by_key[normalizar_chave_categoria(label)].add(label)
        collisions[pid] = {k: sorted(v) for k, v in by_key.items() if k and len(v) > 1}
        canon_maps[pid] = {
            k: next(iter(v))
            for k, v in by_key.items()
            if k and len(v) == 1
        }

    collision_items = [
        (pid, key, labels)
        for pid, cmap in collisions.items()
        for key, labels in cmap.items()
    ]
    out("opcoes_auditadas", total_options)
    out("colisoes_total", len(collision_items))
    print("colisoes_begin")
    if collision_items:
        for pid, key, labels in collision_items:
            p = auditaveis[pid]
            print(
                f"pergunta_id={pid} | pergunta={p['texto']!r} | "
                f"chave={key!r} | opcoes={labels!r}"
            )
    else:
        print("(nenhuma)")
    print("colisoes_end")

    # Respostas agregadas para reduzir volume.
    resp_rows = conn.execute(text("""
        SELECT
            r.pergunta_id,
            r.valor_resposta,
            COUNT(*) AS total
        FROM respostas r
        JOIN perguntas p ON p.id = r.pergunta_id
        WHERE COALESCE(p.ativo, TRUE) = TRUE
        GROUP BY r.pergunta_id, r.valor_resposta
        ORDER BY r.pergunta_id, r.valor_resposta
    """)).mappings().all()

    by_question = defaultdict(list)
    for r in resp_rows:
        if r["pergunta_id"] in auditaveis:
            by_question[r["pergunta_id"]].append((r["valor_resposta"], int(r["total"])))

    global_total = 0
    global_changed = 0
    global_unchanged = 0
    global_unknown = 0
    global_ambiguous = 0
    impact_rows = []
    conversions_by_q = defaultdict(Counter)

    for pid, p in sorted(auditaveis.items()):
        total = changed = unchanged = unknown = ambiguous = 0
        collision_keys = set(collisions.get(pid, {}))
        cmap = canon_maps.get(pid, {})

        for raw, n in by_question.get(pid, []):
            total += n
            raw_s = "" if raw is None else str(raw)
            key = normalizar_chave_categoria(raw_s)
            if key in collision_keys:
                ambiguous += n
                continue
            canonical = cmap.get(key)
            if canonical is None:
                unknown += n
                continue
            if raw_s == canonical:
                unchanged += n
            else:
                changed += n
                conversions_by_q[pid][(raw_s, canonical)] += n

        global_total += total
        global_changed += changed
        global_unchanged += unchanged
        global_unknown += unknown
        global_ambiguous += ambiguous
        if total:
            impact_rows.append((pid, p, total, changed, unchanged, unknown, ambiguous))

    out("respostas_avaliadas", global_total)
    out("respostas_canonicalizadas", global_changed)
    out("respostas_inalteradas", global_unchanged)
    out("respostas_desconhecidas", global_unknown)
    out("respostas_ambiguas", global_ambiguous)
    out("percentual_canonicalizado", safe_pct(global_changed, global_total))

    print("impacto_por_pergunta_begin")
    for pid, p, total, changed, unchanged, unknown, ambiguous in impact_rows:
        print(
            f"id={pid} | pesquisa={p['pesquisa_id']} | tipo={p['tipo']} | "
            f"total={total} | canonicalizadas={changed} | pct={safe_pct(changed,total)} | "
            f"desconhecidas={unknown} | ambiguas={ambiguous} | texto={p['texto']!r}"
        )
        for (raw, canonical), n in conversions_by_q[pid].most_common(8):
            print(f"  conversao={raw!r} -> {canonical!r} | total={n}")
    print("impacto_por_pergunta_end")

    # Pergunta alvo.
    print("pergunta_alvo_begin")
    target = perguntas.get(PERGUNTA_ID)
    if target is None:
        print(f"ERRO: pergunta_id={PERGUNTA_ID} nao encontrada")
    else:
        print(
            f"id={target['id']} | pesquisa={target['pesquisa_id']} | "
            f"tipo={target['tipo']} | espontanea={target['espontanea']} | "
            f"texto={target['texto']!r}"
        )
        print(f"opcoes={target['opcoes']!r}")
        target_cmap = canon_maps.get(PERGUNTA_ID, {})
        target_collision_keys = set(collisions.get(PERGUNTA_ID, {}))

        grouped = conn.execute(text("""
            SELECT r.valor_resposta, COUNT(*) AS total
            FROM respostas r
            WHERE r.pergunta_id = :qid
            GROUP BY r.valor_resposta
            ORDER BY COUNT(*) DESC, r.valor_resposta
        """), {"qid": PERGUNTA_ID}).mappings().all()

        total_target = sum(int(r["total"]) for r in grouped)
        print(f"total_respostas={total_target}")

        acacio_key = normalizar_chave_categoria("Acácio Favacho")
        branco_key = normalizar_chave_categoria("BRANCO/NULO")
        acacio_total = 0
        branco_total = 0

        print("variantes_relevantes_begin")
        for r in grouped:
            raw = "" if r["valor_resposta"] is None else str(r["valor_resposta"])
            n = int(r["total"])
            key = normalizar_chave_categoria(raw)
            canonical = None if key in target_collision_keys else target_cmap.get(key)
            if key == acacio_key:
                acacio_total += n
                print(f"ACACIO | bruto={raw!r} | total={n} | canonico={canonical!r}")
            if key == branco_key:
                branco_total += n
                print(f"BRANCO | bruto={raw!r} | total={n} | canonico={canonical!r}")
        print("variantes_relevantes_end")
        print(f"acacio_canonicalizado_total={acacio_total}")
        print(f"branco_nulo_canonicalizado_total={branco_total}")
    print("pergunta_alvo_end")

    # Cutias / setor alvo usando a regra espacial de classificacao:
    # cada coleta so entra se for coberta por exatamente 1 setor analitico.
    print("setor_alvo_begin")
    setor_cols = {
        r[0] for r in conn.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'setores'
        """)).fetchall()
    }

    filter_parts = ["s.pesquisa_id = :pid"]
    if "finalidade" in setor_cols:
        filter_parts.append("s.finalidade IN ('RELATORIO','AMBOS')")
    if "ativo" in setor_cols:
        filter_parts.append("COALESCE(s.ativo, TRUE) = TRUE")
    setor_filter_sql = " AND ".join(filter_parts)

    setor_row = conn.execute(text(f"""
        SELECT s.id, s.nome
        FROM setores s
        WHERE {setor_filter_sql}
          AND LOWER(s.nome) = LOWER(:setor_nome)
        ORDER BY s.id
        LIMIT 1
    """), {"pid": PESQUISA_ID, "setor_nome": SETOR_NOME}).mappings().first()

    if setor_row is None:
        print(f"ERRO: setor {SETOR_NOME!r} nao localizado na pesquisa {PESQUISA_ID}")
    else:
        sid = int(setor_row["id"])
        print(f"setor_id={sid} | setor_nome={setor_row['nome']!r}")

        geo_rows = conn.execute(text(f"""
            WITH classificacao AS (
                SELECT
                    c.id AS coleta_id,
                    MIN(s.id) AS setor_id,
                    COUNT(s.id) AS quantidade_setores
                FROM coletas c
                JOIN setores s
                  ON {setor_filter_sql}
                 AND ST_Covers(
                        s.geometria,
                        COALESCE(c.localizacao_inicio, c.localizacao_fim)
                     )
                WHERE c.pesquisa_id = :pid
                  AND COALESCE(c.localizacao_inicio, c.localizacao_fim) IS NOT NULL
                GROUP BY c.id
            )
            SELECT
                r.valor_resposta,
                COUNT(*) AS total
            FROM respostas r
            JOIN classificacao cl ON cl.coleta_id = r.coleta_id
            WHERE r.pergunta_id = :qid
              AND cl.quantidade_setores = 1
              AND cl.setor_id = :sid
            GROUP BY r.valor_resposta
            ORDER BY COUNT(*) DESC, r.valor_resposta
        """), {"pid": PESQUISA_ID, "qid": PERGUNTA_ID, "sid": sid}).mappings().all()

        validas = sum(int(r["total"]) for r in geo_rows)
        target_cmap = canon_maps.get(PERGUNTA_ID, {})
        target_collision_keys = set(collisions.get(PERGUNTA_ID, {}))
        acacio_key = normalizar_chave_categoria("Acácio Favacho")
        acacio = 0

        for r in geo_rows:
            raw = "" if r["valor_resposta"] is None else str(r["valor_resposta"])
            key = normalizar_chave_categoria(raw)
            if key in target_collision_keys:
                continue
            if key == acacio_key and target_cmap.get(key) is not None:
                acacio += int(r["total"])

        print(f"respostas_validas={validas}")
        print(f"acacio_canonicalizado={acacio}")
        print(f"percentual_acacio={safe_pct(acacio, validas)}")
    print("setor_alvo_end")

finally:
    # Nunca commit.
    try:
        tx.rollback()
    finally:
        conn.close()
    print("db_transaction_final=ROLLBACK")
PY
DB_RC=$?
say "db_audit_exit_code=$DB_RC"
if [ "$DB_RC" -ne 0 ]; then
  say "ATENCAO: auditoria DB falhou. Nao liberar deploy com este resultado."
fi

section "4. INVENTARIO WEB PROD - SOMENTE LEITURA"

SEARCH_ROOTS=()
for d in /var/www /opt /srv; do
  [ -d "$d" ] && SEARCH_ROOTS+=("$d")
done

if [ "${#SEARCH_ROOTS[@]}" -eq 0 ]; then
  say "Nenhuma raiz padrao encontrada para inventario Web."
else
  say "known_bundle_search=$KNOWN_WEB_BUNDLE"
  FOUND_BUNDLES=()
  while IFS= read -r f; do
    [ -n "$f" ] && FOUND_BUNDLES+=("$f")
  done < <(
    find "${SEARCH_ROOTS[@]}" -maxdepth 7 -type f \
      \( -name "$KNOWN_WEB_BUNDLE" -o -name 'index-*.js' \) \
      2>/dev/null | head -100
  )

  if [ "${#FOUND_BUNDLES[@]}" -eq 0 ]; then
    say "bundles_encontrados=0"
  else
    say "bundles_encontrados=${#FOUND_BUNDLES[@]}"
    for bundle in "${FOUND_BUNDLES[@]}"; do
      say "--- bundle=$bundle"
      if have sha256sum; then
        sha256sum "$bundle" 2>/dev/null || true
      fi
      ls -lh "$bundle" 2>/dev/null || true

      say "marcadores_presentes:"
      for marker in \
        "Selecionar setores" \
        "Composição eleitoral" \
        "data-sector-map-expanded" \
        "hiddenLegendKeys" \
        "onHideAllLegendKeys"
      do
        if grep -aFq "$marker" "$bundle" 2>/dev/null; then
          say "  SIM | $marker"
        else
          say "  NAO | $marker"
        fi
      done

      say "marcadores_fases_posteriores:"
      for marker in \
        "Cota atingida" \
        "refresh_token" \
        "Por município" \
        "Importar setores"
      do
        if grep -aFq "$marker" "$bundle" 2>/dev/null; then
          say "  PRESENTE | $marker"
        else
          say "  AUSENTE  | $marker"
        fi
      done

      dir="$(dirname "$bundle")"
      base="$(basename "$bundle")"
      map_file="$bundle.map"
      [ -f "$map_file" ] && say "sourcemap_direto=$map_file"
      [ -f "$dir/../manifest.json" ] && say "manifest=$dir/../manifest.json"
      [ -f "$dir/../.vite/manifest.json" ] && say "vite_manifest=$dir/../.vite/manifest.json"

      sm_line="$(grep -aEo 'sourceMappingURL=[^[:space:]]+' "$bundle" 2>/dev/null | tail -1 || true)"
      [ -n "$sm_line" ] && say "source_mapping_url=$sm_line"
    done
  fi

  say "source_maps_and_archives_begin"
  find "${SEARCH_ROOTS[@]}" -maxdepth 7 -type f \
    \( -name '*.js.map' -o -name 'manifest.json' -o -name 'manifest.webmanifest' \
       -o -name '*web*prod*.zip' -o -name '*web*.tar.gz' -o -name '*frontend*.zip' \
       -o -name '*dist*.zip' \) \
    -printf '%p | %s bytes | %TY-%Tm-%Td %TH:%TM\n' 2>/dev/null | head -200 || true
  say "source_maps_and_archives_end"
fi

section "5. VERIFICACAO DE ESCRITA"
say "Nenhum comando de patch/migration/restart/deploy foi executado por este script."
say "A transacao de banco foi READ ONLY e terminou em ROLLBACK."
say "OBS: a transferencia deste proprio script via SCP e uma escrita em /tmp no host PROD, autorizada pelo operador."

section "6. GATE INFORMATIVO"
say "Nao liberar G.3A apenas com este script sem revisar:"
say "  - backend_head_corresponde_g2"
say "  - alembic_corresponde_g2"
say "  - colisoes_total"
say "  - impacto/pergunta 46"
say "  - Cutias"
say "  - inventario Web"
say "Este script NAO faz deploy."
