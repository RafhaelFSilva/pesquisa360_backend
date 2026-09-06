# QA LOCAL DO BACKEND (Docker) — procedimento reproduzível

**Data:** 2026-09-06
**Escopo:** levantar um ambiente **QA local** (Docker) utilizável pelo
Supervisor mobile, pelo Web e pelo Mobile Agente, com o seed QA compatível
com o schema atual. Não é staging, não é produção e não é URL oficial: tudo
roda em `127.0.0.1` na máquina do desenvolvedor.

Registro de defeito desta rodada: **QA-BE-001** (§9).

## 1. Serviços do `docker-compose.yml`

| Serviço | Container | Imagem | Porta host | Health |
|---|---|---|---|---|
| `db` | `pesquisa360_db` | `postgis/postgis:15-3.3` | `5432` | `pg_isready` (healthcheck do compose; `api` só sobe depois de `healthy`) |
| `api` | `pesquisa360_api` | build local (`Dockerfile`, Python 3.13) | **`8000`** | sem healthcheck no compose; verificar por `docker compose ps` + `GET /` |

O código é montado em `/app` (hot-reload), então scripts em `scripts/` rodam
dentro do container com o mesmo código do checkout.

Health canônico da API (não há endpoint `/health` — não criado nesta rodada):

```powershell
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/     # {"message":"API Pesquisa360 no ar!"}
```

## 2. Pré-requisitos

- Docker Desktop ativo.
- `.env` na raiz do backend (copiar de `.env.example`; **não versionado** —
  confirmado por `.gitignore`). `DATABASE_URL` aponta para o host `db` do
  compose. `APP_ENV=development` mantém `/docs`.
- Para QA em device físico: `adb` no PATH e depuração USB habilitada.

## 3. Procedimento (PowerShell)

```powershell
# 1. Subir containers (db + api)
docker compose up -d
docker compose ps                     # db "healthy", api "Up"

# 2. Migrations (head único: c6d7e8f9a0b1)
docker compose exec api alembic upgrade head
docker compose exec api alembic current

# 3. Seed QA (idempotente; senha SOMENTE por variável de ambiente)
$env:CONFIRM_QA_SEED = "SEED_QA_DATASET"
$env:QA_DEFAULT_PASSWORD = "<senha temporária escolhida por você>"
docker compose exec `
  -e CONFIRM_QA_SEED="$env:CONFIRM_QA_SEED" `
  -e QA_DEFAULT_PASSWORD="$env:QA_DEFAULT_PASSWORD" `
  api python scripts/seed_qa_dataset.py
Remove-Item Env:QA_DEFAULT_PASSWORD

# 4. Health
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/

# 5. Credenciais QA: e-mails em §5; a senha é a que você definiu no passo 3.
#    Para trocar, basta reexecutar o passo 3 com outra QA_DEFAULT_PASSWORD.

# 6. Conectar o device (USB) e confirmar
adb devices

# 7. Encaminhar a porta da API para o aparelho físico
adb reverse tcp:8000 tcp:8000

# 8. Build debug do Supervisor (repositório pesquisa360_supervisor_app)
flutter build apk --debug --dart-define=API_BASE_URL=http://127.0.0.1:8000
adb install -r build/app/outputs/flutter-apk/app-debug.apk

# 9. Executar o roteiro de QA do Supervisor (doc do próprio Supervisor)

# 10. Remover o encaminhamento ao final
adb reverse --remove tcp:8000

# 11. Parar containers quando desejar (o volume postgres_data é preservado)
docker compose down
```

Notas:

- **Banco recém-criado:** `alembic upgrade head` semeia apenas os perfis
  `Superadmin`, `Coordenador`, `Supervisor` e `Cliente`. O seed QA cria
  `Gerente` e `Agente` se faltarem (papéis canônicos de `core/rbac.py`; nenhum
  papel novo). O bootstrap do Superadmin (`scripts/bootstrap_superadmin.py`)
  é opcional para o QA do Supervisor.
- **Banco existente:** o seed atualiza no lugar (upsert por nome/e-mail/
  título/`client_uuid`) e **redefine a senha** dos cinco usuários QA para a
  `QA_DEFAULT_PASSWORD` informada. Nada mais é apagado. Para zerar dados de
  desenvolvimento existe `scripts/dev_reset_database.py` (dry-run por padrão).
- O seed aborta se `APP_ENV`/`ENV`/`ENVIRONMENT`/`FASTAPI_ENV` indicar
  produção, se `CONFIRM_QA_SEED` não for exatamente `SEED_QA_DATASET` ou se
  `QA_DEFAULT_PASSWORD` estiver ausente.

## 4. `API_BASE_URL` por ambiente

| Cliente | `API_BASE_URL` | Observação |
|---|---|---|
| Emulador Android | `http://10.0.2.2:8000` | `10.0.2.2` é o alias do host dentro do emulador |
| Device físico (USB) | `http://127.0.0.1:8000` | exige `adb reverse tcp:8000 tcp:8000`; `10.0.2.2` **não existe** no aparelho |
| Web (`vite dev`) | `http://localhost:8000` | CORS já libera `localhost:5173` e `127.0.0.1:5173` |

Nenhuma alteração de CORS ou de rede foi necessária: o loopback do device
chega ao host via `adb reverse`, e o build **debug** do Supervisor já permite
texto claro para loopback. O build release permanece HTTPS-only.

## 5. Usuários e permissões do seed (sem senhas)

| E-mail | Perfil | Empresa | `CAMPO_MONITORAR` | Uso no QA |
|---|---|---|---|---|
| `gerente.qa.a@pesquisa360.com` | Gerente | Empresa QA A | **sim** | usuário autorizado do Supervisor (QA-RBAC-01) |
| `agente.qa.a1@pesquisa360.com` | Agente | Empresa QA A | não | usuário **sem** permissão (QA-RBAC-03): `/projetos/`, `/controle-campo` e `/setores` → 403 |
| `agente.qa.a2@pesquisa360.com` | Agente | Empresa QA A | não | segundo agente (setor A2) |
| `gerente.qa.b@pesquisa360.com` | Gerente | Empresa QA B | sim | isolamento entre tenants |
| `agente.qa.b1@pesquisa360.com` | Agente | Empresa QA B | não | isolamento entre tenants |

A senha é a mesma para os cinco (valor de `QA_DEFAULT_PASSWORD` no momento
do seed). Nunca registrar a senha em doc, commit ou log. O RBAC é o real
(`core/rbac.py`); o seed não cria bypass de tenant, superusuário nem
`company_id` fixo.

Pendência: contas `Supervisor` e `Cliente` com `CAMPO_MONITORAR` (QA-RBAC-02)
não são semeadas — dependem de ACL de projeto (ADR-024) e ficaram fora desta
rodada para não expandir o seed sem validação.

## 6. Cenário semeado (100% sintético)

| Entidade | Conteúdo |
|---|---|
| Empresas | `Empresa QA A` (CNPJ sintético `12345678000195`), `Empresa QA B` (`98765432000198`) |
| Projeto | `Projeto QA Mobile/Web` (Empresa QA A, status Ativo, coordenador = Gerente QA A, `data_inicio` fixa 2026-09-01) |
| Pesquisa | `Pesquisa QA Todos os Tipos` (ativa, cerca eletrônica em Macapá/AP sintética, tolerância 50 m) |
| Perguntas | 9 perguntas cobrindo todos os tipos (`TEXTO`, `NUMERO`, `ESCOLHA_SIMPLES`, `MULTIPLA_ESCOLHA`, `DATA`, `IMAGEM`, `TEXTO_LONGO`, `ESCALA`) |
| Setores / cotas territoriais | `Setor QA A1` (meta 5, agente A1) e `Setor QA A2` (meta 5, agente A2), polígonos PostGIS; vínculo N:N em `setor_agentes` + `agente_id` legado |
| Atividade de campo | 3 abordagens (`tentativas_campo`): 2 `RECUSA`, 1 `NAO_ELEGIVEL`, com GPS dentro dos setores e `client_uuid` determinístico (uuid5) |
| Plano de perfil / células | **não semeado** — exige base eleitoral, territórios e `ProjetoBaseEleitoral`; o painel devolve `cotas_perfil` sem plano (estado legítimo) |

Nenhum dado real de eleitor/entrevistado. Os CNPJs são sequências didáticas
com dígitos verificadores válidos, sem máscara, e passam por
`schemas.normalize_cnpj` (o mesmo validador da API) antes de persistir.

## 7. Determinismo e idempotência

- Sem `random`; datas fixas (`SEED_REFERENCE_DATE` / `SEED_REFERENCE_DATETIME`).
- Reexecutar não duplica: empresas por nome, usuários por e-mail, projeto por
  (empresa, nome), pesquisa por (projeto, título), perguntas por (pesquisa,
  texto), opções por (pergunta, texto), setores por (pesquisa, nome),
  `setor_agentes` por (setor, agente), tentativas por (empresa, `client_uuid`).
- Verificado em 2026-09-06: duas execuções consecutivas no banco do compose
  mantiveram as contagens de `companies`, `usuarios`, `perfis`, `projetos`,
  `pesquisas`, `perguntas`, `opcoes`, `setores`, `setor_agentes` e
  `tentativas_campo` idênticas; o mesmo é provado por
  `tests/test_seed_qa_dataset.py::SeedRealPostgresTests`.

## 8. Smoke da API (sem tokens)

Executado em 2026-09-06 contra o compose local após o seed:

| Rota | Gerente QA A | Agente QA A1 | Sem token |
|---|---|---|---|
| `POST /login/token` | 200 | 200 | — |
| `GET /usuarios/me/` | 200 (`papel=GERENTE`, `CAMPO_MONITORAR` presente) | 200 | — |
| `GET /projetos/` | 200 (inclui `Projeto QA Mobile/Web`) | 403 | — |
| `GET /projetos/{id}/pesquisas/{id}/controle-campo` | 200 (`resumo`, `setores`, `tentativas_por_resultado`, `cotas_perfil`…) | 403 | 401 |
| `GET /projetos/{id}/pesquisas/{id}/setores` | 200 (setores com `agente_ids`) | 403 | — |

## 9. QA-BE-001 — seed incompatível com o limite de `companies.cnpj`

- **Sintoma:** `psycopg2.errors.StringDataRightTruncation: value too long for
  type character varying(14)` em `UPDATE companies SET cnpj=...` ao rodar
  `scripts/seed_qa_dataset.py`.
- **Causa raiz (seed desatualizado):** o seed gravava o CNPJ **com máscara**
  (`NN.NNN.NNN/NNNN-NN`, 18 caracteres) direto no ORM, sem passar pelo
  validador. A migration `e7f9a2b3c4d5` (validate company tenant fields)
  estreitou a coluna para `VARCHAR(14)` + `UNIQUE`, normalizando os valores
  legados para 14 dígitos, e `schemas.normalize_cnpj` exige 14 dígitos com
  dígitos verificadores válidos. O valor antigo também falhava no validador
  (dígitos verificadores inválidos). O schema está correto: o domínio persiste
  CNPJ normalizado; nada foi alargado.
- **Correção:** CNPJs sintéticos válidos e sem máscara; `seed_cnpj()` usa o
  mesmo `normalize_cnpj` da API e checa o limite lido do modelo
  (`models.Company.cnpj.type.length`); conflito de CNPJ com outra empresa
  aborta com mensagem clara em vez de `IntegrityError`.
- **Correções colaterais de baixo impacto:** senha embutida removida
  (`QA_DEFAULT_PASSWORD` obrigatória); perfis `Gerente`/`Agente` criados se
  ausentes (banco recém-migrado); vínculo `setor_agentes`; abordagens de
  campo determinísticas; `data_inicio` fixa.
- **Teste:** `tests/test_seed_qa_dataset.py` — CNPJ do seed cabe na coluna e
  passa no validador; persistência com `CHECK(length <= 14)` real em SQLite
  (o teste prova que o CHECK barra o valor antigo); guarda de senha/confirmação/
  produção; e, opt-in via `P360_QA_DATABASE_URL`, seed completo duas vezes
  em PostgreSQL/PostGIS real sem truncamento nem duplicidade.

## 10. Testes

```powershell
# regressão específica (SQLite embutido)
python -m pytest -q tests/test_seed_qa_dataset.py

# opcional: seed real num PostGIS DESCARTÁVEL (padrão de doc/20)
docker run -d --name p360qa -e POSTGRES_PASSWORD=qa -e POSTGRES_DB=p360qa -p 55432:5432 postgis/postgis:16-3.4
$env:DATABASE_URL = "postgresql://postgres:qa@localhost:55432/p360qa"; $env:SECRET_KEY = "qa-secret"
python -m alembic upgrade head
$env:P360_QA_DATABASE_URL = "postgresql://postgres:qa@localhost:55432/p360qa"
python -m pytest -q tests/test_seed_qa_dataset.py
docker rm -f p360qa
```

Nunca apontar `P360_QA_DATABASE_URL` para o banco do compose ou qualquer
banco compartilhado: o teste faz commit.

## 11. Segurança

- Seed não cria tenant bypass, superusuário universal nem `company_id` fixo.
- Senha somente por variável de ambiente; nada versionado.
- Tokens nunca são impressos pelo seed nem por este documento.
- CNPJs sintéticos; nomes claramente de QA; nenhuma PII real.
- O seed não roda em produção: guarda por `APP_ENV`/`ENV`/`ENVIRONMENT`/
  `FASTAPI_ENV`, confirmação explícita e execução manual (nunca automática).
