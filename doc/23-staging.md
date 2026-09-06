# STAGING DO BACKEND — arquitetura, deploy por `git push` e estado

**Data:** 2026-09-06 (PROMPT 17)
**Estado:** infraestrutura **versionada e ensaiada localmente**; o ambiente
remoto está **BLOQUEADO** por decisão do time (sem servidor nem hostname
aprovados nesta rodada). Nada foi provisionado fora desta máquina.

Staging **não é produção**: banco, volumes, containers, segredos e hostname
próprios; dados exclusivamente sintéticos do seed QA (doc/22).

## 1. O que já existe (inventário, sem segredos)

| Item | Situação encontrada |
|---|---|
| Produção | `https://360.rtecnologia.online` → `68.183.133.86`; nginx 1.24 (Ubuntu) + Let's Encrypt (CN=360.rtecnologia.online, válido até 2026-11-01); containers `pesquisa360_api`/`pesquisa360_db` em `/opt/pesquisa360` (conforme `scripts/g21_prod_readonly_audit.sh`) |
| DNS | `rtecnologia.online`, `www.` e `api.` atrás da Cloudflare; `api-staging.`, `staging.` e `qa.rtecnologia.online` **não existem** |
| Reverse proxy versionado | nenhum; o nginx de produção não está neste repositório |
| CI/CD | nenhum workflow existia neste repositório |
| Acesso SSH não interativo | **indisponível** (sem chave configurada para `68.183.133.86`); inventário remoto do servidor não foi possível, só o externo (DNS/TLS/HTTP) |
| Remoto Git | `origin` = `github.com/RafhaelFSilva/pesquisa360_backend` |

## 2. Arquitetura do staging (versionada nesta rodada)

```
Internet ── HTTPS 443 ──> nginx (host) ── http://127.0.0.1:8010 ──> pesquisa360_staging_api
                          certbot/LE                                        │ rede interna do compose
                                                                pesquisa360_staging_db (sem porta publicada)
```

| Arquivo | Papel |
|---|---|
| `docker-compose.staging.yml` | projeto compose **`pesquisa360_staging`**: `db` (postgis 15-3.3, volume `staging_pgdata`, **sem `ports`**), `api` (imagem autocontida, `restart: unless-stopped`, healthcheck `GET /`, publicada **só em `127.0.0.1:8010`**, volume `staging_uploads`) |
| `.env.staging.example` | modelo do `.env.staging` (ignorado pelo git) — nomes das variáveis em §7 |
| `deploy/staging/deploy.sh` | deploy idempotente no servidor: `up -d --build` → espera healthy → `alembic upgrade head` → seed QA (só com `CONFIRM_QA_SEED` + `QA_DEFAULT_PASSWORD` no ambiente) → smoke `GET /` |
| `deploy/staging/nginx.api-staging.conf.example` | vhost nginx (porta 80; o certbot gera o 443 e o redirect) com `X-Forwarded-*` |
| `.github/workflows/deploy-staging.yml` | **deploy por `git push`** na branch `staging` (ou manual): SSH no servidor, `git checkout <sha>`, `deploy.sh`. Falha de propósito enquanto os secrets não existirem |
| `Dockerfile` | corrigido (INFRA-001, §6) para a imagem funcionar sem bind mount |

Isolamento em relação ao compose de DEV (`docker-compose.yml`, projeto
`pesquisa360_backend`): nomes de projeto, containers, rede e volumes
diferentes; portas diferentes (8000 vs 8010); Postgres de staging nunca é
publicado.

`APP_ENV=staging`: **não** é produção (ADR-038 só desliga `/docs`,
`/redoc` e `/openapi.json` com `production`), e o seed QA recusa
`production`. Portanto em staging a documentação OpenAPI **fica disponível**
e o seed é permitido — comportamento pré-existente, nenhum endpoint ou modo
de depuração novo foi adicionado.

## 3. Procedimento de deploy por `git push` (quando houver servidor aprovado)

Uma vez, no servidor (operador humano):

1. `git clone <origin> /opt/pesquisa360-staging` (ou o `STAGING_APP_DIR` escolhido).
2. `cp .env.staging.example .env.staging` e preencher (senha do Postgres de
   staging, `SECRET_KEY` própria, `CORS_ALLOWED_ORIGINS` se houver Web).
3. Instalar o vhost a partir de `deploy/staging/nginx.api-staging.conf.example`
   substituindo `<HOST_STAGING>` pelo hostname aprovado; `nginx -t`;
   `sudo certbot --nginx -d <HOST_STAGING>`.
4. Criar usuário de deploy (sem root) com chave dedicada e acesso ao Docker.

Uma vez, no GitHub (Settings → Environments → `staging` → secrets):
`STAGING_SSH_HOST`, `STAGING_SSH_USER`, `STAGING_SSH_KEY`, `STAGING_SSH_PORT`
(opcional), `STAGING_APP_DIR`, `QA_DEFAULT_PASSWORD` (opcional; se presente o
seed roda a cada deploy, idempotente).

A cada release de staging:

```
git push origin <commit>:staging     # dispara .github/workflows/deploy-staging.yml
```

O workflow faz `git checkout --detach <sha>` no servidor e roda
`deploy/staging/deploy.sh`. Rollback = `git push --force origin <sha_anterior>:staging`
(o script é idempotente; migrations só avançam — downgrade é decisão manual
com `alembic downgrade <rev>` dentro de `pesquisa360_staging_api`).

## 4. Operação manual equivalente (sem CI)

```
cd /opt/pesquisa360-staging && git fetch && git checkout <sha>
CONFIRM_QA_SEED=SEED_QA_DATASET QA_DEFAULT_PASSWORD='<fora do git>' bash deploy/staging/deploy.sh
```

Health: `docker compose -p pesquisa360_staging -f docker-compose.staging.yml --env-file .env.staging ps`
(`api` e `db` `healthy`) e `curl -s https://<HOST_STAGING>/` →
`{"message":"API Pesquisa360 no ar!"}`. Não há endpoint `/health` (não criado).

Logs: `docker compose -p pesquisa360_staging ... logs api --since 10m` (o
uvicorn registra método, rota, query e status; nunca corpo, token ou senha).

Restart: `docker compose -p pesquisa360_staging ... restart api` — só o
projeto de staging; `restart: unless-stopped` traz os dois containers de volta
após reboot do host.

Backup: dados 100% sintéticos e regeneráveis pelo seed; **não há** política de
backup de staging e ela não deve se misturar ao backup de produção. Para zerar:
`docker compose -p pesquisa360_staging ... down -v` e novo deploy.

## 5. Smoke remoto (após DNS + certbot)

```
curl -sSI https://<HOST_STAGING>/                       # 200, sem aviso de certificado
openssl s_client -connect <HOST_STAGING>:443 -servername <HOST_STAGING> </dev/null | openssl x509 -noout -issuer -subject -dates
POST /login/token (gerente.qa.a@pesquisa360.com)         # 200
GET  /usuarios/me/                                       # 200, papel GERENTE, CAMPO_MONITORAR
GET  /projetos/                                          # 200, Projeto QA Mobile/Web
GET  /projetos/{id}/pesquisas/{id}/controle-campo        # 200
GET  /projetos/{id}/pesquisas/{id}/setores               # 200
Agente QA: /projetos/, /controle-campo, /setores         # 403 ; Gerente QA B em recursos da A: 404
```

Supervisor: `flutter build apk --debug --dart-define=API_BASE_URL=https://<HOST_STAGING>`
instalado no aparelho **sem `adb reverse`** (rede normal). O build release
continua HTTPS-only: nenhuma exceção de texto claro é necessária.

## 6. Ensaio local reproduzível (executado em 2026-09-06)

Mesmos arquivos, nesta máquina, sem tocar no compose de DEV nem em produção:

| Passo | Resultado |
|---|---|
| `docker compose -p pesquisa360_staging -f docker-compose.staging.yml --env-file .env.staging config` | válido; `api` em `127.0.0.1:8010`, `db` sem porta |
| 1ª execução de `deploy.sh` | **FALHOU**: `RuntimeError: Directory 'static' does not exist` — a imagem não era autocontida (INFRA-001) |
| Correção | `Dockerfile` copia `migrations/`, `alembic.ini`, `scripts/` e cria `static/` e `uploads/`; regressão em `tests/test_dockerfile_packaging.py` |
| 2ª execução | `db`/`api` healthy; migrations: vazio → `c6d7e8f9a0b1 (head)`; seed QA executado; `GET / → 200` |
| Seed 2ª vez | contagens idênticas (companies 3, usuarios 5, perfis 6, projetos 1, pesquisas 1, perguntas 9, opcoes 15, setores 2, setor_agentes 2, tentativas 3); CNPJs com 14 dígitos; perfis Gerente/Agente criados pelo seed |
| Smoke | Gerente A: login/`/me`/`/projetos/`/`controle-campo`/`setores` 200, `CAMPO_MONITORAR`; Agente: `/me` 200, dados 403; Gerente B: `/projetos/` vazio e 404 nos recursos da A; sem token 401 |
| Portas | `pesquisa360_staging_api 127.0.0.1:8010->8000`, `pesquisa360_staging_db 5432/tcp` (interna) |

O que o ensaio **não** cobre: hostname, DNS, HTTPS/certificado, firewall do
servidor e o aparelho pela rede normal — todos dependem do servidor aprovado.

## 7. Segredos e variáveis (apenas nomes)

`.env.staging` (servidor): `POSTGRES_USER`, `POSTGRES_PASSWORD`,
`POSTGRES_DB`, `DATABASE_URL`, `APP_ENV`, `SECRET_KEY`,
`ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS`,
`STAGING_API_BIND`, `STAGING_API_PORT`, `CORS_ALLOWED_ORIGINS`,
`UPLOAD_MAX_SIZE_BYTES`, `UPLOAD_DIRECTORY`, `AUDIT_TRUST_PROXY`, SMTP_* ,
`WEB_BASE_URL`.

Seed (nunca no arquivo): `CONFIRM_QA_SEED`, `QA_DEFAULT_PASSWORD`.

GitHub Environment `staging`: `STAGING_SSH_HOST`, `STAGING_SSH_USER`,
`STAGING_SSH_KEY`, `STAGING_SSH_PORT`, `STAGING_APP_DIR`, `QA_DEFAULT_PASSWORD`.

Nenhum valor real está no Git (`.env.*` ignorado, exceto os `*.example`).

## 8. Pendências para STAGING = PASS (decisões do time)

1. **Servidor aprovado** (mesmo VPS com projeto compose isolado, ou outro) e
   usuário de deploy com chave dedicada.
2. **Hostname oficial** (candidato: `api-staging.rtecnologia.online`) e
   registro DNS `A → <IP do servidor>`; se na Cloudflare, criar com proxy
   **desligado** (DNS only) para o HTTP-01 do certbot.
3. Certificado Let's Encrypt via `certbot --nginx` no hostname aprovado.
4. Secrets do GitHub Environment `staging` e primeiro `git push … :staging`.
5. Smoke §5 e QA de equivalência do Supervisor no aparelho sem `adb reverse`.

Até lá: **RC-BLK-02 (staging), RC-BLK-03 (credencial staging) e RC-BLK-04
(API_BASE_URL) continuam BLOQUEADOS**; o QA local (doc/22) permanece válido.
