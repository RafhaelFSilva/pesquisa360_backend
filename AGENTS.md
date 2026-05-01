# AGENTS.md — Pesquisa360 Backend

## Contexto

Backend do Pesquisa360 em FastAPI + PostgreSQL/PostGIS + SQLAlchemy + Pydantic + Alembic.

A multitenância já foi implementada na API. O backend é a fonte de verdade para:
- empresa/tenant do usuário
- permissões
- filtro de dados por empresa
- vínculo de projetos, pesquisas, perguntas, coletas e relatórios

## Regras obrigatórias

- Não remover nem enfraquecer filtros de multitenância.
- Não aceitar empresa_id vindo do frontend/mobile para operações de usuário comum.
- empresa_id/tenant_id deve vir do usuário autenticado ou de regra explícita de Super Admin.
- Não alterar contrato de API sem listar impacto em Web e Mobile.
- Não criar migrations destrutivas sem confirmação.
- Não acessar banco de produção.
- Não usar SHA1 para senha. Usar bcrypt ou Argon2.
- Não misturar refatoração ampla com correção de bug.

## Antes de finalizar

- Informar endpoints afetados.
- Informar impacto no Web.
- Informar impacto no Mobile offline-first.
- Rodar testes relevantes, se disponíveis.
- Mostrar riscos restantes.