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
