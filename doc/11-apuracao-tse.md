# Pesquisa360 — Apuração TSE

Estado em 2026-10-04. Legenda: **IMPLEMENTADO** (código e testes),
**VALIDADO** (exercitado contra o TSE), **PARCIAL**, **PENDENTE**, **FUTURO**.

> **Atenção.** Toda a validação com votos foi feita no **ambiente oficial de
> SIMULAÇÃO** do TSE (pleito 17801), em banco PostgreSQL descartável. A
> apuração **oficial** de 04/10/2026 ainda estava zerada quando esta rodada
> terminou: o *smoke test oficial* (seção 20) continua **PENDENTE**.

## 1. Objetivo

Ingerir os arquivos oficiais de divulgação de resultados do TSE, preservar a
evolução da apuração e oferecer uma camada de serviço para as futuras APIs e
dashboards (majoritário, proporcional/nominata, personalizado e territorial
UF → Município → Zona → Seção). A API analítica e as telas Web foram
entregues na rodada seguinte (seções 23 e 24).

```
TSE
 │
 ├── EA11 ─ configuração
 ├── EA12 ─ municípios
 ├── EA14 ─ acompanhamento Brasil
 ├── EA15 ─ acompanhamento UF
 ├── EA16 ─ zonas/seções
 ├── EA18 ─ arquivos da urna            (PARCIAL)
 ├── EA20 ─ resultado consolidado
 └── BU ─── resultado por seção         (PENDENTE)
        │
        ▼
  pesquisa360/services/tse  (TseIngestion, execução manual por CLI)
        │
        ├── Snapshot     tse_snapshots (arquivo como recebido, SHA-256)
        ├── Normalize    normalization.py (DTOs)
        ├── Persist      repository.py (append-only)
        └── Reconcile    reconciliation.py
               │
               ▼
           PostgreSQL  (tabelas tse_*, sem company_id)
               │
               ▼
   API /apuracao (somente leitura da base)
               │
       ┌───────┼────────┐
       ▼       ▼        ▼
 Majoritário Nominata Territorial       (Web)
                        │
             UF→Município→Zona→Seção    (Seção: PENDENTE de BU)
```

## 2. Fontes TSE

| Ambiente | `origem` | Base | Ambiente (path) | Pleito | Eleições |
|---|---|---|---|---|---|
| Oficial | `OFICIAL` | `https://resultados.tse.jus.br` | `oficial` | 3220 | 6257 federal, 6259 estadual, 6261 municipal |
| Simulação | `SIMULADO` | `https://resultados-sim.tse.jus.br/simulado` | `simulado2026` | 17801 | 21270 federal, 21272 estadual, 21274 municipal |

Os dois ambientes usam o ciclo `ele2026` e os mesmos templates. Deputado
Federal (0006) pertence à eleição **estadual** (6259 / 21272), não à federal.
Todo arquivo traz `f`: `"o"` (oficial) ou `"s"` (simulado); é desse campo que
sai a `origem`, nunca do nome do host.

## 3. EA11 — configuração (VALIDADO)

`<base>/<ambiente>/comum/config/ele-c.json`. Único caminho fixo: é o ponto de
partida. Fornece ciclo, pleitos, eleições, cargos e os templates de diretório
(`arq[].dir`), de onde saem todas as demais URLs (`discovery.py`).

## 4. EA12 — municípios (VALIDADO)

`mun-e<eleicao>-cm.json`. Por UF: código TSE, código IBGE, nome, capital e
zonas. AP: 16 municípios; Macapá = `06050`, zonas `0002`, `0010`, `0014`.

## 5. EA14 — acompanhamento Brasil (VALIDADO)

`br-e<eleicao>-ab.json`. Uma linha por UF e uma `br`: andamento (`and`: `n`
não iniciado, `p` parcial, `f` final), última totalização (`dt`/`ht`), seções
(`ts` total, `st` totalizadas, `sni` não instaladas, `sna` não apuradas),
eleitorado, comparecimento e abstenção.

## 6. EA15 — acompanhamento UF (VALIDADO)

`<uf>-e<eleicao>-ab.json`. Mesmo contrato do EA14, com uma linha por município
e uma da própria UF.

## 7. EA16 — seções (VALIDADO)

`<uf>-p<pleito>-cs.json`. Município → Zona → Seção. É configuração do
**pleito**, não de uma eleição. Depois de totalizada, a seção principal ganha
`da`/`ha` (data/hora do arquivo auxiliar). Ver seção 17 sobre agregação.

## 8. EA18 — auxiliar de seção (PARCIAL)

`p<pleito>-<uf>-m<mun>-z<zona>-s<secao>-aux.json`. Situação da seção e, por
hash, a lista de arquivos de urna (`bu`, `rdv`, `log`, `vota`); a URL do
arquivo é `<diretório do aux>/<hash>/<nome>`.

- Contrato com arquivos: comprovado apenas no pleito **452 (2024)**.
- Simulado 2026: responde 200 com `hashes: [{"arq": []}]` — seção
  "Totalizada" sem arquivo publicado. O parser tolera hash ausente.
- Oficial 2026: 404 antes da totalização. **PENDENTE** confirmar o contrato
  com arquivos reais de 2026.

A ingestão grava o snapshot do EA18 e os metadados dos arquivos em
`tse_arquivos_secao`; não baixa os arquivos.

## 9. EA20 — resultado consolidado (VALIDADO no simulado)

`<uf>[<mun>][-z<zona>]-c<cargo>-e<eleicao>-u.json`. **Fonte oficial
consolidada.** `tpabr` vale `uf`, `mu` ou `zona`; no arquivo de zona `cdabr`
traz só o número da zona — o município vem da requisição.

Campos usados: `idg`, `dg`/`hg` (geração), `dt`/`ht` (última totalização),
`and`, `tf`, seções, eleitorado, comparecimento, abstenção, votos (`tv`, `vv`,
`vnom`, `vl`, `vb`, `tvn`, `van`, `vansj`), `qe`, `nv`, federações, partidos e
candidatos (`sqcand`, `n`, `nm`, `nmu`, `st`, `e`, `vap`, `pvap`, `dvt`).

**Destinação do voto (`dvt`).** O voto de candidato "Anulado sub judice"
aparece em `vap`, mas não é válido. No AP simulado: soma dos `vap` = 463.013;
soma dos candidatos com `dvt` "Válido" = 378.662 = `vnom`; soma dos anulados
sub judice = 84.351 = `vansj`. Somar `vap` sem olhar `dvt` infla o resultado.

**Diferença estrutural observada.** O EA20 oficial zerado (pré-totalização)
não traz `dvt`, `esae` nem `mnae`; o simulado totalizado traz os três. Com a
apuração oficial em andamento (04/10/2026), o oficial passou a trazer `dvt`,
mas não `esae` nem `mnae`. O parser aceita todas as variações.

## 10. BU — boletim de urna (PENDENTE)

Formato comprovado: binário **ASN.1 BER** (não JSON). A decodificação exige a
especificação oficial `bu.asn1` do TSE (ZIP "Formato dos arquivos de BU, RDV e
assinatura digital"), que não foi obtida — a página do TSE bloqueia clientes
automatizados. `bu.py` apenas identifica o formato; a função de decodificação
com a especificação existe, mas **nunca foi executada**. Não há parser
próprio, não há `tse_resultados_secao` e nenhum voto por seção é gravado.
A confirmar com a especificação: se o BU identifica o candidato por número.

## 11. Modelo de dados

Migration `cd95ebf79450` (filha de `be8036a45b28`), models em
`pesquisa360/db/models_tse.py`. Nenhuma tabela tem `company_id` nem FK para
tabelas de tenant.

| Tabela | Conteúdo | Chave natural |
|---|---|---|
| `tse_eleicoes` | eleição de um pleito | `origem + pleito + codigo_eleicao` |
| `tse_cargos` | cargos (0001, 0006…) | `codigo` |
| `tse_abrangencias` | BR, UF, município, zona (com `parent_id`) | `eleicao_id + chave` |
| `tse_federacoes`, `tse_partidos` | por eleição | `eleicao_id + numero` |
| `tse_candidatos` | candidato | `eleicao_id + sqcand` |
| `tse_snapshots` | arquivo como recebido (`payload_json`) | `url + sha256` |
| `tse_totalizacoes` | um estado do EA20 por (cargo, abrangência) | `cargo + abrangencia + snapshot` |
| `tse_resultados_candidato` | votos, %, situação, `destinacao_voto` | `totalizacao + candidato` |
| `tse_resultados_partido` | votos nominais e de legenda | `totalizacao + partido` |
| `tse_secoes` | seção do EA16 | `origem + pleito + uf + município + zona + seção` |
| `tse_arquivos_secao` | metadados dos arquivos do EA18 | `secao + hash + nome_arquivo` |

`chave` da abrangência: `br`, `uf:ap`, `mu:ap:06050`, `zona:ap:06050:0002`.

## 12. Fluxo de ingestão (IMPLEMENTADO)

`TseIngestion.run(IngestScope)` — uma passada, uma transação:

1. EA11 → eleições e cargos; cargo → eleição.
2. EA12 → abrangências BR, UF e todos os municípios da UF.
3. EA14 → a UF mudou? (impressão `andamento | última totalização | seções
   totalizadas | comparecimento`, comparada com o snapshot anterior)
4. Só se a UF mudou, ou se falta dado municipal: EA15 → quais municípios mudaram.
5. EA20 apenas das abrangências que mudaram, que não têm dado, ou cuja
   totalização gravada está atrás do acompanhamento. Zona segue o gatilho do
   seu município.
6. EA16 → `tse_secoes`.
7. Opcional (`secoes_ea18`): EA18 de N seções principais já totalizadas.

Todo GET é condicional (ETag / Last-Modified do último snapshot). Não há
daemon, worker nem polling contínuo.

## 13. Histórico (IMPLEMENTADO)

Append-only. Um EA20 só gera nova `tse_totalizacoes` quando o **conteúdo
material** muda (hash de andamento, seções, totais e votos por
candidato/partido — sem IDG nem data de geração). Arquivo regerado pelo TSE
sem mudança material gera snapshot novo, mas não totalização nova. Nenhuma
totalização ou resultado é atualizado depois de gravado.

Comprovado em teste automatizado com dois estados (1.500 → 3.115 votos). **Não
foi comprovado contra o TSE**: o simulado é estático e a apuração oficial
ainda não tinha começado.

## 14. Reconciliação (IMPLEMENTADO)

`reconcile_abrangencia`: município × zonas e UF × municípios. O EA20 da
abrangência maior é a referência; a soma própria nunca o substitui.

| Estado | Significado |
|---|---|
| `CONSISTENT` | soma das partes = oficial |
| `TEMPORAL_LAG` | diferença com arquivos de janelas de totalização distintas |
| `INCONSISTENT` | diferença dentro da mesma janela |
| `INSUFFICIENT_DATA` | falta parte (ex.: município não ingerido) |

Mesma janela = soma das seções totalizadas das partes igual à do oficial.
Seções × zona depende do BU: **PENDENTE**.

## 15. Rate limit

O TSE informa teto de 100 requisições/IP/segundo. O serviço usa 2 req/s por
padrão e recusa configuração acima de 10. Variáveis: `TSE_BASE_URL`,
`TSE_AMBIENTE`, `TSE_REQUEST_TIMEOUT` (20 s), `TSE_MAX_RETRIES` (3),
`TSE_REQUESTS_PER_SECOND` (2). Não há credencial.

## 16. Tratamento de erro

| Resposta | Comportamento |
|---|---|
| 200 | snapshot + processamento |
| 304 | reutiliza o último snapshot |
| 404 | definitivo na execução; nunca repetido; listado no relatório |
| 429 | retry respeitando `Retry-After` |
| 5xx / falha de rede | retry com backoff exponencial (1, 2, 4 s) |
| outro 4xx | erro definitivo (`TseHttpError`) |
| arquivo de outra origem | `TseOrigemError`; nada é gravado |

O cliente só acessa o host configurado em `TSE_BASE_URL`.

## 17. Seções agregadas

`nsa` (na principal) lista as agregadas; `nsp` (na agregada) aponta a
principal. Seção agregada **não tem urna própria**: seus votos estão no BU da
principal. O total de seções do EA14/EA15/EA20 equivale às **principais**.

- AP oficial: 1.971 seções = 1.914 principais + 57 agregadas.
- AP simulado: 2.181 = 2.177 principais + 4 agregadas (Pracuúba, seção 0027
  agrega 0090–0093).

Em `tse_secoes`, `eh_principal` e `secao_principal` são amarrados por CHECK.
Contagem de urnas usa apenas `eh_principal`.

## 18. Identificação de candidatos

`sqcand` é a identidade oficial (única por eleição). O número é chave
operacional contextual: só identifica com cargo + UF. Nome nunca identifica.

## 19. Multitenancy

Dados TSE são públicos e **globais**: não têm `company_id` e não são filtrados
por tenant (ADR-076). Painéis, favoritos e preferências, quando existirem,
serão multitenant. Ver `03-regras-multitenancy.md`.

## 20. Operação manual

```bash
# oficial (pleito 3220)
python scripts/tse_apuracao.py ingest --uf ap --cargo 0006 \
    --municipios todos --zonas-de 06050
python scripts/tse_apuracao.py consulta --uf ap --cargo 0006 --sqcand <sqcand> \
    --reconciliar-municipio 06050

# simulação (pleito 17801) — grava com origem SIMULADO
python scripts/tse_apuracao.py ingest --simulado --uf ap --cargo 0006 \
    --municipios todos --zonas-de 06050 --secoes-ea18 2
```

`--force` ignora os gatilhos e reconsulta todos os EA20 do escopo.

**Smoke test oficial (PENDENTE).** Quando a apuração oficial tiver votos:
rodar a ingestão oficial em banco de QA e conferir EA20 com votos reais,
mesmo contrato, mesma normalização e presença ou não de `dvt`. Diferença
material em relação ao simulado: parar e reportar.

Ingestão real executada nesta rodada (simulado, AP, Deputado Federal): 27
requisições, 27 snapshots, 20 totalizações (UF + 16 municípios + 3 zonas),
2.181 seções. Reexecução: 6 requisições, todas 304, 0 linhas novas.

O ambiente **oficial** (pleito 3220) também foi ingerido no mesmo banco de QA,
às 15h12 de 04/10/2026, ainda **zerado** (0/1914 seções, `and = n`): 25
requisições, 20 totalizações com 0 voto, 1.971 seções. Isso prova o caminho
oficial de ponta a ponta e a coexistência com o simulado, mas **não** valida
votos reais — é a linha de base para o smoke test oficial.

## 21. Troubleshooting

- **`TseOrigemError`**: `--simulado` ausente ou sobrando; o arquivo é de outro ambiente.
- **404 no EA18**: seção ainda não totalizada; esperado antes da apuração.
- **Nada novo na reexecução**: correto — o EA14 não mudou. Use `--force`.
- **`INSUFFICIENT_DATA` na UF**: nem todos os municípios foram ingeridos
  (`--municipios todos`).
- **`alembic current` falha no DEV local**: ver ADR-084 e `00-status-atual.md`.
- **Worker**: ver seção 25.3.

## 22. Roadmap dos dashboards

Fases em `08-roadmap.md`. A–F entregues; G entregue até Zona — Seção depende
do BU.

## 23. API analítica (IMPLEMENTADO)

Contratos em `02-contratos-api.md` ("Apuração Eleitoral"). Rotas em
`pesquisa360/api/endpoints/apuracao_tse.py`, leituras em
`services/tse/analytics.py`, painéis em `services/apuracao_paineis.py`.

- Leitura do TSE: `INTELIGENCIA_VER`. Dado global, sem filtro de empresa.
- Painéis: tabelas `apuracao_paineis` e `apuracao_painel_itens` (migration
  `0f1b3b6c572f`), com `company_id` do usuário.
- A API não consulta o TSE e não infere resultado.
- Nominata: federação agrupa seus partidos; partido isolado é a própria
  nominata. `total = votos nominais válidos + votos de legenda`.
- Presidente é servido na abrangência UF.

Exemplo real (simulado, AP, Deputado Federal, candidato `41609530`):
UF 3.115 votos · Macapá 1.612 (51,75%) · zonas 0002 = 676, 0014 = 509,
0010 = 427 · reconciliação `CONSISTENT` nos dois níveis.

## 24. Web (IMPLEMENTADO)

Rotas, camadas e regras de apresentação em `04-frontend-web.md`, seção 16.
Para QA local:

```bash
# API contra o banco de QA (porta livre, ex.: 8010)
DATABASE_URL=postgresql://p360qa:***@127.0.0.1:55433/p360_tse_qa \
  SECRET_KEY=... CORS_ALLOWED_ORIGINS=http://localhost:5173 \
  UPLOAD_DIRECTORY=... UPLOAD_MAX_SIZE_BYTES=5242880 \
  python -m uvicorn pesquisa360.main:app --port 8010

# Web apontando para essa API
VITE_API_URL=http://127.0.0.1:8010 npx vite --host localhost --port 5173
```

Usuários: seed QA (`scripts/seed_qa_dataset.py`) aplicado ao banco de QA.

## 25. Ingestão em produção — worker automático e CLI de fallback

### 25.1 Worker `tse_ingestor`

```
TSE oficial ──> worker ──> PostgreSQL ──> API
```

Processo separado da API (ADR-087), mesma imagem, comando
`python -m pesquisa360.services.tse.worker`, sem porta. Nasce **desligado**.

| Variável | Padrão | Observação |
|---|---|---|
| `TSE_INGESTION_ENABLED` | `false` | desligado: o processo fica vivo e ocioso |
| `TSE_INGESTION_ORIGIN` | `OFICIAL` | ou `SIMULADO`; define o ambiente do TSE |
| `TSE_INGESTION_UFS` | — | obrigatória quando habilitado; ex.: `AP` ou `AP,PA` |
| `TSE_INGESTION_CARGOS` | `0001,0003,0005,0006,0007` | 4 dígitos |
| `TSE_INGESTION_MUNICIPIOS` | `todos` | `todos` ou códigos TSE de 5 dígitos |
| `TSE_INGESTION_ZONAS_DE` | vazio | vazio, `todos` ou códigos; habilita o nível zona |
| `TSE_INGESTION_INTERVAL_SECONDS` | `20` | de 15 a 3600 |
| `TSE_REQUESTS_PER_SECOND` | `2` | teto interno de 10 |
| `TSE_INGESTION_PLEITO` | vazio | vazio = descoberto no EA11 |
| `TSE_INGESTION_HEARTBEAT_FILE` | `<tmp>/tse_ingestor.heartbeat` | lido pelo healthcheck |
| `TSE_INGESTION_LOG_LEVEL` | `INFO` | |

Configuração inválida (origem, UF, cargo, intervalo) impede o processo de
subir, com a mensagem do erro.

**Pleito.** Sem `TSE_INGESTION_PLEITO`, o worker escolhe no EA11 o pleito mais
recente, já realizado, que disputa todos os cargos configurados. Um segundo
turno não disputa todos e por isso não substitui o pleito geral; para
ingeri-lo, configurar um worker/escopo com os cargos do segundo turno (ou
fixar o pleito).

**Ciclo.** A cada intervalo, para cada UF: EA11 → EA12 → EA14 → (se a UF
mudou) EA15 → EA20 só do que mudou → EA16. Tudo por GET condicional. Uma
passada sem novidade no AP faz cerca de 6 requisições, todas 304. Cada UF é
uma transação; erro faz rollback e o ciclo seguinte tenta de novo.

**Singleton.** Advisory lock `pg_try_advisory_lock(5526341, 1|2)` por origem
(ADR-088). Um segundo ingestor da mesma origem fica em espera. Conferir:

```sql
SELECT classid, objid, granted FROM pg_locks WHERE locktype = 'advisory';
```

**Log.** Uma linha por ciclo, sem payload:

```
tse_ingest_cycle {"timestamp": ..., "origem": "OFICIAL", "ufs": ["ap"], "cargos": [...],
  "pleito": "3220", "ea14_changed": ["uf:ap"], "ea15_changed": 3, "requests": 14,
  "200": 8, "304": 6, "404": 0, "429": 0, "ea20": 6, "snapshots_new": 8,
  "totalizacoes_new": 6, "errors": [], "status": "ok", "duration_ms": 7012}
```

`status`: `ok`, `error` (com `errors[]`; houve rollback da UF) ou
`interrupted` (parada pedida). Outros eventos: `tse_ingestor_start`,
`tse_ingestor_standby`, `tse_ingestor_signal`, `tse_ingestor_stop`.

**Health.** `python -m pesquisa360.services.tse.worker --healthcheck` sai com
0 se o heartbeat tem menos de `max(120 s, 4 × intervalo)` e o worker não foi
encerrado. O heartbeat é renovado a cada ciclo e a cada espera entre
requisições, então um worker travado fica `unhealthy`. Desligado e em espera
também são estados saudáveis.

**Startup.**

```bash
# staging (compose versionado)
docker compose -p pesquisa360_staging -f docker-compose.staging.yml \
    --env-file .env.staging up -d tse_ingestor

# produção: o compose não é versionado; aplicar o override conferido
docker compose -f <compose de produção> \
    -f deploy/tse_ingestor/docker-compose.tse-ingestor.yml up -d tse_ingestor
```

Pré-requisitos: banco no head `0f1b3b6c572f`, imagem com `httpx` e o código do
worker, `TSE_INGESTION_ENABLED=true` e `TSE_INGESTION_UFS` no ambiente.

**Shutdown.** `docker compose stop tse_ingestor` envia SIGTERM: a espera é
interrompida, uma ingestão em andamento é abortada com rollback, a trava é
solta e o processo sai com código 0 (`stop_grace_period: 30s`).

### 25.2 CLI manual (fallback)

A CLI usa a **mesma trava**. Com o worker ativo na mesma origem ela não
executa e sai com código 3. Para ingerir manualmente: parar o worker, rodar a
CLI, subir o worker de novo.

```bash
docker compose stop tse_ingestor
python scripts/tse_apuracao.py ingest --uf ap \
    --cargo 0001 --cargo 0003 --cargo 0005 --cargo 0006 --cargo 0007 \
    --municipios todos --zonas-de 06050
docker compose start tse_ingestor
```

Sem `--pleito`, a CLI também descobre o pleito pelo EA11. Sucesso: JSON na
saída e código 0 (`totalizacoes_novas` > 0, ou 0 com
`ea20_ignorados_sem_mudanca` > 0 quando nada mudou). `--force` reconsulta
todos os EA20 do escopo e continua idempotente. Nunca usar `--simulado` em
produção.

### 25.3 Troubleshooting

| Sintoma | Causa provável | Ação |
|---|---|---|
| container `healthy`, sem `tse_ingest_cycle` | `TSE_INGESTION_ENABLED=false` | habilitar e reiniciar o serviço |
| `tse_ingestor_standby` repetido | outro ingestor ou uma CLI segura a trava | localizar em `pg_locks`; manter só um |
| `status: error` com `TseTransientError` | TSE fora do ar ou lento | nenhuma: o próximo ciclo repete |
| `status: error` com `JSONDecodeError` | resposta inválida do TSE | nenhuma: o último estado válido foi preservado |
| `status: error` com `TseOrigemError` | origem configurada ≠ ambiente consultado | conferir `TSE_INGESTION_ORIGIN` |
| `status: error` com `LookupError` | pleito, cargo ou município inexistente no EA11/EA12 | conferir cargos e `TSE_INGESTION_PLEITO` |
| `tse_ingestor_lock_error` | banco indisponível | o worker tenta de novo a cada ciclo |
| container `unhealthy` | worker travado ou encerrado | `docker compose restart tse_ingestor` |
| CLI sai com código 3 | worker ativo | parar o worker antes (25.2) |

### 25.4 Procedimento de deploy

1. Preflight: conferir que o patch local de `crud.py` em PROD equivale ao do
   repositório; conferir o override `deploy/tse_ingestor/` contra o compose real.
2. Atualizar o código e reconstruir a imagem (instala `httpx` declarado).
3. `alembic upgrade head` → `0f1b3b6c572f`.
4. Subir a API; depois o `tse_ingestor` com `TSE_INGESTION_ENABLED=true`.
5. Conferir `healthy`, o primeiro `tse_ingest_cycle` com `status: ok` e um
   único lock advisory.
6. **Smoke oficial com votos reais**: `scripts/tse_apuracao.py consulta` e a
   API com candidato com votos > 0 em UF, município e zona.

## 26. Estado de publicação e bloqueadores de deploy (2026-10-04)

O backend foi validado em PostgreSQL descartável criado do zero
(`p360_tse_release_qa`), com ingestão real do simulado e do oficial ainda
zerado, e depois com a apuração oficial em andamento. **Não está validado
em produção.**

IMPLEMENTADO: ingestão (EA11/12/14/15/16/20), persistência, histórico
append-only, origem OFICIAL × SIMULADO, reconciliação, API analítica, painéis
multitenant (backend), território até zona.

PENDENTE:

- **Smoke oficial em PRODUÇÃO** — feito apenas em QA (abaixo); o deploy deve
  repetir.
- **BU e seção** — sem `bu.asn1` oficial; EA18 de 2026 com arquivos não visto.
- **Operação prolongada do worker** — validado em QA com a apuração oficial
  em andamento, por alguns minutos; não há prova de horas de operação.
- **DEV antigo = NÃO CANÔNICO** — `alembic_version` órfão e migration
  sintética ausente. Nenhum stamp foi feito. A referência para migrations é o
  banco de QA criado do zero.
- **Deploy em PROD** — não realizado.

**Smoke oficial com votos reais — executado em QA (04/10/2026, 17:22–17:24
de Brasília).** O worker, em container, ingeriu a apuração oficial do AP
(pleito 3220 descoberto pelo EA11) no banco de QA descartável, com a
apuração em andamento (`and = p`, 32 de 1.914 seções):

| Abrangência | Código | Votos | IDG | Gerado em (Brasília) |
|---|---|---|---|---|
| UF | `ap` | 566 | 1103376 | 04/10 17:23:28 |
| Município | `06050` Macapá | 31 | 1103311 | 04/10 17:23:09 |
| Zona | `0010` | 10 | 1103306 | 04/10 17:23:09 |
| Zona | `0014` | 21 | 1089672 | 04/10 17:21:19 |
| Zona | `0002` | 0 | 1079417 | 03/10 16:06:51 (sem seção totalizada) |

Candidato: JOSENILDO, nº 1212, `sqcand 30002532841`, Deputado Federal, PDT,
11,16% na UF, `dvt = Válido`, situação ainda não publicada pelo TSE.

- Zonas × Macapá: 31 = 31, 5 = 5 seções → `CONSISTENT`.
- Municípios × UF: 566 × 553, 32 × 30 seções → `TEMPORAL_LAG` (os arquivos
  municipais estavam uma janela atrás do da UF; não é inconsistência).
- Soma dos candidatos válidos = `vnom` (4.935); legenda 135; válidos 5.070.
- **O EA20 oficial passou a trazer `dvt`** assim que houve votos (inclusive
  "Anulado sub judice"); `esae` e `mnae` não apareceram. O contrato com votos
  é o mesmo do simulado nesse ponto.
- **Histórico real:** três totalizações da UF para o mesmo candidato —
  0 votos (IDG 1078269, zerada) → 543 (IDG 1089442, 28 seções) → 566
  (IDG 1103376, 32 seções) —, todas preservadas.

Isso valida ingestão, normalização, reconciliação e histórico com dado
oficial **em QA**. Não substitui o smoke em produção, que o deploy deve
repetir.

**Patch de PROD em `pesquisa360/crud.py`:** a regra (resposta espontânea sem
texto → `NS/SR`) foi reimplementada no repositório com teste. O preflight deve
comparar com o patch local de PROD antes do pull; se forem equivalentes, o
arquivo local pode ser descartado em favor do versionado. O `AGENTS.md`
alterado em PROD é instrução operacional local e fica fora do Git.
