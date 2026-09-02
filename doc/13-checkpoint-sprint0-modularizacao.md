# Checkpoint Sprint 0 — Modularização

**Data:** 2026-09-02
**Classificação:** GO — SPRINT 0 ENCERRADA / BASELINE VALIDADA

## Identidade validada

- Backend: `0275d234c1304b53271805d063c79ee7d9addfd9` (HEAD técnico validado e pai do
  commit documental deste checkpoint)
- Web: `e96d10d1a6a6f89b2b7932ffe9ffa2314e4c3789`
- Mobile: `cf6687af67fab8ff16f91fe2dba456e0da19e415`
- Alembic: head único `c6d7e8f9a0b1`
- Branches: Backend/Web `feature/mapa-liderancas`; Mobile
  `feature/mobile-multitenancy-integration`

## Migrations e runtime

Python 3.13.14 foi usado no Windows. O WinError 50 observado em Python 3.14 foi
classificado como incompatibilidade ambiental de subprocesso, não como defeito
de migration. `tests/test_migration_chain.py`: 13 passed, 1 skipped; o skip é o
teste PostgreSQL opt-in, executado separadamente com sucesso.

Em PostgreSQL real descartável: `upgrade head` → `downgrade base` →
`upgrade head` passou. `28f012bafc15` agora remove somente as FKs/colunas
`company_id` de usuários/projetos e `companies`; `91fbe6db1f17` remove somente
`setores.tolerancia`. Upgrades e lineage não foram alterados.

## Evidência executável

- Backend focado: 206 passed, 1 skipped, 28 subtests passed, zero falhas.
- Backend completo: 1529 passed, 13 skipped, 95 warnings, zero falhas/erros,
  745,39 s. Foi usado `--basetemp` exclusivo porque o temp padrão do Windows
  gerou PermissionError ambiental; os 26 casos de shapefile passaram.
- Segurança: IDOR, mass assignment, atomicidade mutação+auditoria, 404 antes de
  403 e bloqueio de autoelevação passaram.
- Web: 1119 passed, zero falhas; build de produção passou.
- W01/W02: stores, Axios, ModuleGate e ModuleRoute reais carregados via Vite;
  troca A→B e falha de `/usuarios/me/modulos/` permaneceram fail-closed, com
  autenticação/Core preservados no erro.
- Lint Web: 10 erros preexistentes, zero novo da Sprint 0.
- Busca estática produtiva: nenhum tenant/usuário fixo, `all_features=true`,
  fallback permissivo ou DELETE de entitlement; payload administrativo usa
  empresa no path protegido, e payload operacional não ganhou seletor de tenant.
- Mobile: clean e inalterado.

## Riscos residuais

- Os 95 warnings Backend são depreciações conhecidas; não são falhas.
- O lint Web mantém dívida preexistente de 10 erros.
- O teste PostgreSQL completo é opt-in e exige banco descartável explícito.
- Nenhum deploy, push, migration de produção ou teste de produção foi feito.

## Próxima fase

A baseline permite iniciar a modelagem do MVP 1 — Inteligência Eleitoral:
Potencial de Crescimento. O MVP não foi iniciado neste checkpoint.
