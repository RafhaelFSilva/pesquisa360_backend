# Status atual

## Sprint 0 — Fundação da Modularização (Prompt 01)

Implementado em 2026-09-01: catálogo `modulos`, catálogo
`modulo_funcionalidades`, licenças `modulo_entitlements`, concessões explícitas
`modulo_entitlement_funcionalidades`, resolução temporal/aditiva por Empresa,
Projeto e Pesquisa e `GET /usuarios/me/modulos/`.

Migration: `c6d7e8f9a0b1`, filha de `b5c6d7e8f9a0`, novo head validado.
Validação executável em Python Windows 3.13: upgrade, downgrade e re-upgrade em
SQLite descartável passaram; `test_modulos_entitlements.py` passou (18) e a
suíte backend passou (1481 passed, 12 skipped, 81 warnings). O banco do `.env`
não foi acessado: Docker não estava disponível e o host `db` não é local.
Nenhum tenant recebeu licença no seed. O gating das rotas existentes **não foi
ativado**. Web e Mobile não foram alterados.

## Sprint 0 — Enforcement Backend (Prompt 02)

Implementado em 2026-09-01: gates reutilizáveis `require_module` e
`require_feature`, com contextos de Empresa, Projeto e Pesquisa. Projeto e
Pesquisa passam primeiro pela autorização de recurso/ACL; somente depois o
Backend verifica catálogo ativo, entitlement efetivo e concessão explícita de
feature. Ausência de capacidade contratada retorna 403; recurso não autorizado
ou capacidade inexistente/inativa retorna 404.

Nenhuma rota produtiva foi ligada aos gates nesta fase. Projetos, Pesquisas,
Relatórios, Monitoramento, Lideranças, Inteligência Territorial, Controle de
Campo e Mobile continuam sem gating comercial. `potencial_crescimento`
permanece planejada e inativa.

## Sprint 0 — Contexto / Gates Web (Prompt 03)

Implementado em 2026-09-02: infraestrutura Web de capacidades comerciais em
`zustand` com `GET /usuarios/me/modulos/`, `ModuleGate`, `FeatureGate`,
`ModuleRoute`, `FeatureRoute`, registry central de módulos/features e reset
explícito em logout/troca de usuário. A UX usa fail-closed para loading/error
sem decidir segurança; o Backend continua sendo a autoridade final de
entitlement e ACL. Nenhum módulo legado foi ativado; o fluxo atual mantém o
cliente apenas ocultando opções e bloqueando acesso de rota quando o backend
não concede a capacidade.

## Sprint 0 — Administração de Licenças (Prompt 04)

Implementada a administração global de catálogo e entitlements por Superadmin,
com escopos Empresa/Projeto/Pesquisa, validade, status, features explícitas,
efeito imediato nas capabilities e auditoria persistente before/after. O Web
ganhou a página `/admin/modulos`, protegida por `SuperadminRoute`, com seleção
de empresa, criação, suspensão, reativação, cancelamento e gestão de features.
Catálogo permanece read-only; `potencial_crescimento` permanece inativa.

Validação: Backend 1517 passed, 12 skipped, 0 failures e 84 warnings; Web
1119/1119 testes e build de produção aprovados. Lint mantém 10 erros
preexistentes, nenhum em arquivo novo do Prompt 04. Nenhuma migration criada;
head `c6d7e8f9a0b1`.

## Sprint 0 — baseline final validada (Prompts 05B/05C/05D)

Validação encerrada em 2026-09-02 com Python 3.13.14. A regressão Backend
completa passou com 1529 testes, 13 skips e zero falhas; o Web passou 1119/1119
testes e build. O lint mantém 10 erros preexistentes, sem erro novo da Sprint 0.

Em PostgreSQL real descartável, a lineage completa passou por `upgrade head`,
`downgrade base` e novo `upgrade head`. Foram corrigidos somente os downgrades
históricos `28f012bafc15` e `91fbe6db1f17`; seus `upgrade()`, `revision` e
`down_revision` não mudaram. A lineage permanece única em `c6d7e8f9a0b1`.

O hardening confirmou IDOR administrativo, rejeição de mass assignment,
atomicidade entre mutação e auditoria, 404 antes de 403, ausência de
autoelevação e fail-closed Web na troca de empresa e na falha da API de módulos.

## Inteligência Eleitoral — MVP 1 (Potencial de Crescimento) — Prompt 01

Concluído em 2026-09-02 como rodada exclusivamente de modelagem: auditoria da
estrutura real de dados (entidades, tipos de pergunta, respostas, pesos,
denominadores dos relatórios, espontânea, território) e proposta de contrato
de domínio em
`doc/14-inteligencia-eleitoral-potencial-crescimento-modelagem.md`.

Resultado: **GO para revisão metodológica humana** (questões Q01–Q15 e matriz
de decisão no documento). Nenhum código funcional foi alterado; nenhuma
migration criada; head permanece `c6d7e8f9a0b1`. A feature
`inteligencia_eleitoral.potencial_crescimento` permanece planejada e inativa;
nenhum tenant recebeu entitlement.

**Atualização (Prompt 02):** a modelagem foi APROVADA como baseline
metodológica do MVP 1 — decisões D01–D15 registradas no doc/14 (incluindo a
correção da D11: no modo não ponderado, `weighted_base` é indisponível/null,
nunca `n_bruto` disfarçado).

## Inteligência Eleitoral — MVP 1 (Potencial de Crescimento) — Prompt 02

Implementado em 2026-09-02: contrato de configuração canônico
`GrowthAnalysisConfiguration` no novo package
`pesquisa360/inteligencia_eleitoral/` (`config.py` + `validation.py`), com
enums do domínio, `TargetCandidacy` (bindings explícitos por pergunta),
`ElectoralScenario` (SINGLE/MULTIPLE/ORDERED_MULTIPLE), taxonomia explícita
de intenção, `EligibilityPolicy` (D01 invariável), sinais opcionais
(rejeição/segunda opção/decisão do voto), 1–2 dimensões de perfil, território
NONE/SETOR/MUNICIPIO, filtros compatíveis com `filtros_universo`, políticas
de ponderação (só NAO_PONDERADO), base mínima sem default, referência
ELIGIBLE_UNIVERSE, incerteza WILSON_AAS_APPROX e qualidade de espontânea.
Validação em duas camadas (estrutural Pydantic + domínio contra dados reais,
com erros/warnings tipados e normalização canônica de valores).
Documentação: `doc/15-inteligencia-eleitoral-potencial-crescimento-configuracao.md`.

Sem migration (head `c6d7e8f9a0b1`), sem tabela, sem endpoint, sem
persistência da configuração, sem motor estatístico, sem alteração em
`models.py`, Web e Mobile intocados. A feature `potencial_crescimento` segue
inativa e sem entitlement. Testes: `tests/test_growth_analysis_configuration.py`
(63 casos, C01–C65) passando; regressão focada e completa sem falhas novas.

## Inteligência Eleitoral — MVP 1 (Potencial de Crescimento) — Prompt 03

Implementado em 2026-09-02: motor estatístico
(`pesquisa360/inteligencia_eleitoral/`: `results.py`, `statistics.py`,
`engine.py`) com interface pública
`analyze_growth_potential(db, current_user, configuration) -> GrowthAnalysis`.
Revalida a configuração (erro tipado `GrowthAnalysisConfigurationError`),
congela o universo analítico (filtros estruturais aplicados uma vez),
classifica intenção por ballot mode (SINGLE/MULTIPLE/ORDERED_MULTIPLE, com
exclusão conservadora `MIXED_SPECIAL_ELIGIBILITY`), exclui apoiadores atuais
(D01), segmenta apenas nos cruzamentos configurados (perfil × território pela
regra territorial oficial `ST_Covers`), calcula sinais com denominadores
explícitos por entrevista (`coleta_id` únicos), base mínima por segmento E
por sinal, delta pp, lift (referência zero → null), IC de Wilson AAS
aproximado (Decimal), qualidade de espontânea como condição
(`SIGNAL_UNAVAILABLE_QUALITY`) e snapshot com `GROWTH_ENGINE_VERSION="1.0"`,
`configuration_hash` e `input_fingerprint` determinísticos. Sem score, sem
ranking agregado, sem projeção de votos (`eleitorado_apto` apenas contexto),
`weighted_base` sempre null. Parser de múltipla escolha extraído para
`services/response_values.py` (o motor multidimensional consome via alias,
sem mudança de comportamento).

Sem endpoint, sem persistência, sem migration (head `c6d7e8f9a0b1`),
`models.py` intacto, Web e Mobile intocados; feature `potencial_crescimento`
segue inativa. Testes: `tests/test_growth_analysis_engine.py` (46 casos,
E01–E112, incl. caso oráculo manual) passando em Python 3.13.14; regressão
focada e completa sem falhas novas. Documentação:
`doc/16-inteligencia-eleitoral-potencial-crescimento-motor.md`.

## Inteligência Eleitoral — MVP 1 (Potencial de Crescimento) — Prompt 04

Implementado em 2026-09-02: API do produto — router
`pesquisa360/api/endpoints/potencial_crescimento.py` com três rotas sob
`/projetos/{id}/pesquisas/{id}/inteligencia-eleitoral/potencial-crescimento`
(`GET opcoes-configuracao`, `POST validar-configuracao`, `POST analisar`),
cadeia de segurança em ordem garantida (recurso/ACL 404 — inclusive
cross-tenant — antes de `INTELIGENCIA_VER` 403 e do gate comercial
`require_feature`), camada DTO/mapper separada do domínio
(`inteligencia_eleitoral/http_contract.py`: Decimal interno → JSON number na
borda, sem arredondamento, null preservado) e opções reais de configuração
(`inteligencia_eleitoral/options.py`: compatibilidades por regra técnica,
valores canônicos, territórios oficiais, constraints sem defaults
metodológicos). Validação retorna HTTP 200 `valid=false` para configuração
semanticamente inválida (formulário) e o motor não roda; análise é síncrona
e efêmera com 422 tipados (`GROWTH_CONFIGURATION_INVALID`,
`SEGMENT_LIMIT_EXCEEDED`). Motor, statistics e results intocados.

**API implementada ≠ produto liberado**: a feature `potencial_crescimento`
permanece INATIVA no catálogo real (rotas respondem 404 até ativação
formal); nenhum entitlement real, nenhum seed/migration alterado (head
`c6d7e8f9a0b1`), `models.py` intacto, Web e Mobile intocados, nenhuma
persistência. Testes: `tests/test_growth_analysis_api.py` (36 casos,
A01–A87 + E2E) passando em Python 3.13.14; regressão focada e completa sem
falhas novas. Documentação:
`doc/17-inteligencia-eleitoral-potencial-crescimento-api.md` e doc/02
atualizado.

## Inteligência Eleitoral — MVP 1 (Potencial de Crescimento) — Prompt 05

Implementado em 2026-09-02 (repo `pesquisa360-web`): experiência Web
funcional ponta a ponta. Rota
`/projetos/:projectId/pesquisas/:surveyId/inteligencia-eleitoral/potencial-crescimento`
protegida por `ModuleRoute` + `FeatureRoute` (primeiro uso real do
FeatureRoute da Sprint 0; fail-closed), card gated na Central de
Inteligência, service tipado (`growthPotentialService`), DTOs sem `any`,
lógica pura testável (`lib/growthPotential.ts`: reducer com estados
explícitos, dirty state, guarda de corrida por pesquisa, builders de payload
com conversão %→fração, formatadores rate/pp/lift/interval com null→"—") e
página em 7 etapas orientada por `compatible_as` (nenhuma heurística
textual), com validação-formulário (errors/warnings tipados,
VALUE_NORMALIZED informado), execução da configuração NORMALIZADA e
resultado básico (funil analítico, limitações metodológicas, diagnostics
separados, segmentos na ordem do motor, evidências com
numeradores/bases/intervalos/delta/lift/direção observada). Sem
score/ranking/projeção/persistência; contexto eleitoral apenas como
contexto.

Validação: suíte Web 1175/1175 (baseline 1119 + 56 novos, incl. ajuste de
duas contagens congeladas em `leadershipEntrypoint.test.mjs` para `>= 2`
preservando a intenção), build de produção PASS, lint com os mesmos 10 erros
preexistentes (zero novo). Backend NÃO alterado nesta rodada; Mobile
intacto; feature `potencial_crescimento` segue inativa. Documentação:
`doc/18-inteligencia-eleitoral-potencial-crescimento-web.md` e doc/04
atualizado.

## Inteligência Eleitoral — MVP 1 (Potencial de Crescimento) — Prompt 06

Implementado em 2026-09-02 (repo `pesquisa360-web`): camada de leitura
analítica sobre o resultado — visão executiva com barra metodológica sempre
visível, seletor de sinal com direção favorável explicada (rejeição:
menor=favorável, sem inversão do delta), gráfico de diferença vs referência
(delta_pp real, zero line, suprimido/null nunca vira barra 0), scatter
escala × diferença (X=participação, Y=delta, sem quadrantes semânticos nem
limiar de escala), comparação segmento × universo elegível com IC de Wilson
e denominadores, **interpretação determinística por templates rastreáveis
(sem LLM — ADR-073)** com leitura do segmento em listas (sem contagem
"3 de 4", sem conclusão global), warnings por segmento/evidência com
fallback para códigos desconhecidos, seleção única de segmento entre
tabela/gráficos/mapa, ordenação explícita (default: ordem do motor) e
filtros que alteram somente a visualização (uma única chamada de análise).
Leitura territorial com regra antiagregação (combinação completa de perfil
obrigatória; um finding por território) e **mapa Leaflet de SETOR** via
contratos existentes (`getSetores` + `anelExternoParaLatLng`), escala de cor
contínua centrada em 0 pp; **mapa de MUNICÍPIO adiado por contrato de
geometria ausente** no Web (gap registrado; leitura tabular disponível).

Validação: suíte Web **1229/1229** (baseline 1175 + 54 novos, incl. oráculos
de narrativa: segunda opção 30%/15%/+15 pp e rejeição 12%/22%/−10 pp sem
"88% de aceitação"), build PASS, lint com os mesmos 10 erros preexistentes
(zero novo). Backend e Mobile intocados; feature `potencial_crescimento`
segue inativa. Documentação:
`doc/19-inteligencia-eleitoral-potencial-crescimento-visualizacoes.md`;
doc/04 e doc/18 atualizados.

## Inteligência Eleitoral — MVP 1 (Potencial de Crescimento) — Prompt 07

Executado em 2026-09-02: QA independente completo. Ambiente PostgreSQL/
PostGIS DESCARTÁVEL (Docker, migrations `upgrade head` = `c6d7e8f9a0b1`),
feature ativada e entitlement concedido SOMENTE nesse banco (catálogo real
intacto), dataset oráculo de 20 entrevistas somado à mão + cenários ballot/
stress. **112/112 verificações via HTTP real**: universos/elegibilidade/
denominadores exatos, rejeição múltipla contando entrevista, Wilson validado
por implementação INDEPENDENTE do QA (+âncoras de literatura), zero≠ausência,
lift ref-zero, borda territorial provada em PostGIS (`ST_Covers` true onde
`ST_Contains` é false), sobreposição/sem-coordenada diagnosticados,
determinismo/hash/fingerprint (incl. insensibilidade a resposta
não-analítica), cotas/eleitorado provados como NÃO-pesos, 404-antes-de-403,
mass assignment, SEGMENT_LIMIT tipado. **E2E Web em browser real: PASS**
(fluxo completo com funil idêntico ao oráculo e números na tela; troca A→B
fail-closed; zero 4xx/5xx). Stress: 2.400 coletas/100 segmentos ~260 ms
(síncrono confortável, ADR-070 mantida).

Correções permitidas aplicadas (Web): **F1/Q1 pré-existente** — crash de
runtime da ProjectsPage (`user` órfão desde a extração do Header na Sprint
0); **F2/Q2 pré-existente** — deep link em rotas gated expulsava usuário
licenciado (status `'idle'` tratado como não-ready em ModuleRoute/
FeatureRoute); **F3/Q2** — tipagem do onClick do Recharts v3 no delta chart.
Achados registrados para o Prompt 08: **F4** — `npm run build` não
typechecka (tsc bare em tsconfig solution-style) e o typecheck real revela
38 erros TS pré-existentes fora da feature; **F5** — EmailStr rejeita
domínios especiais (.local). Nenhum achado Q3/Q4/Q5. Backend/motor
INTOCADOS nesta rodada. Regressões: Web 1229/1229, build PASS, lint nos 10
preexistentes; Backend suíte completa verde em 3.13.

Validade interna APROVADA; validade externa LIMITADA por construção (sem
ponderação — declarada na experiência). **GO para o Prompt 08** — detalhes e
matrizes em `doc/20-inteligencia-eleitoral-potencial-crescimento-qa.md`.

## Inteligência Eleitoral — MVP 1 (Potencial de Crescimento) — Prompt 08

Executado em 2026-09-03: hardening e checkpoint final. **Potencial de
Crescimento: MVP TECNICAMENTE VALIDADO / FEATURE INATIVA.**

**F4 resolvido por completo (ADR-074):** o script `build` do Web rodava `tsc`
a seco sobre tsconfig solution-style (checava ZERO arquivos). O comando real
`tsc -b` revelou 39 erros pré-existentes de baseline (nenhum no código do
MVP 1), todos corrigidos mecanicamente sem flexibilizar o tsconfig (zero
`any` novo, zero `@ts-ignore`); dois arquivos mortos `* copy.tsx` removidos;
`tsconfig.node.json` corrigido (apontava `vite.config.ts` inexistente).
`build` agora executa `tsc -b` de verdade e há `npm run typecheck` canônico.
**Typecheck real: PASS (0 erros).**

Consolidação: fixes F1/F2/F3 do QA cobertos por guarda estática
(`tests/hardening.test.mjs`); ferramentas de QA promovidas para
`scripts/qa/growth_qa_seed.py` e `scripts/qa/growth_qa_run.py` (dados 100%
sintéticos, `P360_QA_DATABASE_URL` obrigatória sem default); E2E formalizado
como opt-in `npm run qa:growth-e2e` com credenciais SOMENTE via env.
Varreduras de segurança/linguagem limpas; feature `potencial_crescimento`
confirmada semeada `ativo=false` (migration `c6d7e8f9a0b1`, head único);
nenhum entitlement real criado; `GROWTH_ENGINE_VERSION` permanece `1.0`.

Regressão definitiva: Backend suíte completa verde em Python 3.13; Web
typecheck 0 erros, suíte 1233/1233 (1229 baseline + 4 guardas), build PASS,
lint com os mesmos 10 erros preexistentes (zero novo). Mobile intocado.
Higiene: `.env` do backend NÃO é rastreado pelo git (confirmado por
`git ls-files --error-unmatch`); nenhum segredo/dado pessoal nos commits.
Checkpoint completo, commits e HEADs:
`doc/21-checkpoint-mvp1-potencial-crescimento.md`.

## Hardening do ambiente QA local + correção do seed (Prompt 15 — Backend)

Executado em 2026-09-06. Defeito **QA-BE-001** reproduzido e corrigido:
`scripts/seed_qa_dataset.py` gravava CNPJ com máscara (18 caracteres) em
`companies.cnpj`, `VARCHAR(14)` desde a migration `e7f9a2b3c4d5`, falhando com
`StringDataRightTruncation`. Causa raiz: seed desatualizado (schema e
validador corretos; nada foi alargado). O seed passou a usar CNPJs sintéticos
válidos sem máscara, validados pelo mesmo `schemas.normalize_cnpj` da API e
checados contra o limite lido do modelo; a senha embutida foi removida
(`QA_DEFAULT_PASSWORD` obrigatória); perfis `Gerente`/`Agente` são criados se
faltarem; setores ganharam vínculo N:N em `setor_agentes` e três abordagens de
campo determinísticas. Regressão em `tests/test_seed_qa_dataset.py`
(SQLite com `CHECK` real + seed completo duas vezes em PostGIS descartável,
opt-in por `P360_QA_DATABASE_URL`).

Ambiente QA local reproduzível documentado em `doc/22-qa-local.md`
(compose `db`+`api`, API na porta host 8000, migrations, seed, `adb reverse`,
`API_BASE_URL` por ambiente). O banco local do compose estava em
`b5c6d7e8f9a0` e foi levado ao head `c6d7e8f9a0b1`. Smoke após o seed:
`/login/token`, `/usuarios/me/`, `/projetos/`, `/controle-campo` e `/setores`
com 200 para o Gerente QA (com `CAMPO_MONITORAR`) e 403 para o Agente QA.
Nenhuma regra de produção, contrato de API, schema ou migration alterados.

## Staging por `git push` — infraestrutura versionada (Prompt 17 — Backend)

Executado em 2026-09-06. Sem servidor nem hostname de staging aprovados
(decisão do time nesta rodada) e sem acesso SSH não interativo ao VPS de
produção, nada foi provisionado remotamente: **STAGING HOST = BLOQUEADO**.
O que foi entregue, versionado: `docker-compose.staging.yml` (projeto
`pesquisa360_staging` isolado, API só em `127.0.0.1:8010`, Postgres sem porta
publicada), `.env.staging.example`, `deploy/staging/deploy.sh` (up → healthy
→ `alembic upgrade head` → seed QA por env → smoke), vhost nginx modelo e o
workflow `.github/workflows/deploy-staging.yml` (deploy por push na branch
`staging`, gated por secrets). Ensaio local completo em `doc/23-staging.md`:
migrations do zero ao head, seed idempotente, RBAC e isolamento de tenant.
Defeito **INFRA-001** encontrado e corrigido: a imagem Docker não era
autocontida (`static/`, `migrations/`, `alembic.ini` e `scripts/` faltavam;
só funcionava com o bind mount do compose de DEV) — regressão em
`tests/test_dockerfile_packaging.py`. RC-BLK-02/03/04 permanecem bloqueados
para staging; o QA local (doc/22) continua válido.
