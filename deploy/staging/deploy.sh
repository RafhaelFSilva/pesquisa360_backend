#!/usr/bin/env bash
# Pesquisa360 - deploy de STAGING (idempotente). Roda NO SERVIDOR, a partir do
# checkout do backend (ex.: /opt/pesquisa360-staging), normalmente disparado
# pelo workflow .github/workflows/deploy-staging.yml apos `git push`.
#
# O que faz, nesta ordem:
#   1. sobe/atualiza SOMENTE o projeto compose `pesquisa360_staging`
#   2. espera db e api ficarem healthy
#   3. `alembic upgrade head` no banco de STAGING
#   4. seed QA (apenas se CONFIRM_QA_SEED e QA_DEFAULT_PASSWORD vierem do ambiente)
#   5. smoke local: GET http://127.0.0.1:<porta>/  == 200
#
# O que NUNCA faz: tocar no projeto compose de producao, publicar Postgres,
# imprimir segredos, alterar DNS ou nginx.
set -euo pipefail

PROJECT="${STAGING_COMPOSE_PROJECT:-pesquisa360_staging}"
COMPOSE_FILE="${STAGING_COMPOSE_FILE:-docker-compose.staging.yml}"
ENV_FILE="${STAGING_ENV_FILE:-.env.staging}"
API_PORT="${STAGING_API_PORT:-8010}"
HEALTH_TIMEOUT="${STAGING_HEALTH_TIMEOUT:-180}"

cd "$(dirname "$0")/../.."

say() { printf '[staging] %s\n' "$*"; }

[ -f "$ENV_FILE" ] || { say "ERRO: $ENV_FILE ausente (copie de .env.staging.example)"; exit 2; }
grep -q '^SECRET_KEY=.\+' "$ENV_FILE" || { say "ERRO: SECRET_KEY vazio em $ENV_FILE"; exit 2; }
if grep -Eq '^APP_ENV=(production|producao|prod)$' "$ENV_FILE"; then
  say "ERRO: $ENV_FILE declara APP_ENV de producao; staging usa APP_ENV=staging"; exit 2
fi

compose() { docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }

say "commit: $(git rev-parse --short HEAD) ($(git branch --show-current 2>/dev/null || echo detached))"
say "subindo projeto compose '$PROJECT'"
compose up -d --build --remove-orphans

say "aguardando health (ate ${HEALTH_TIMEOUT}s)"
deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
until [ "$(compose ps --format '{{.Health}}' api 2>/dev/null)" = "healthy" ] \
   && [ "$(compose ps --format '{{.Health}}' db 2>/dev/null)" = "healthy" ]; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    say "ERRO: containers nao ficaram healthy"; compose ps; exit 3
  fi
  sleep 5
done
compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'

say "migrations: antes = $(compose exec -T api alembic current 2>/dev/null | tail -1)"
compose exec -T api alembic upgrade head >/dev/null
say "migrations: depois = $(compose exec -T api alembic current 2>/dev/null | tail -1) | heads = $(compose exec -T api alembic heads 2>/dev/null | tail -1)"

if [ "${CONFIRM_QA_SEED:-}" = "SEED_QA_DATASET" ] && [ -n "${QA_DEFAULT_PASSWORD:-}" ]; then
  say "seed QA (senha nao exibida)"
  compose exec -T -e CONFIRM_QA_SEED="$CONFIRM_QA_SEED" -e QA_DEFAULT_PASSWORD="$QA_DEFAULT_PASSWORD" \
    api python scripts/seed_qa_dataset.py | grep -vi "senha" | sed -n '1,4p'
else
  say "seed QA pulado (defina CONFIRM_QA_SEED=SEED_QA_DATASET e QA_DEFAULT_PASSWORD para executar)"
fi

say "smoke local"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "http://127.0.0.1:${API_PORT}/")
[ "$code" = "200" ] || { say "ERRO: GET / respondeu $code"; exit 4; }
say "GET http://127.0.0.1:${API_PORT}/ -> 200"
say "concluido"
