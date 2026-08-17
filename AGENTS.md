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
- Não acessar banco de produção por padrão. O acesso ao banco de produção é permitido somente quando a solicitação do administrador contiver explicitamente a string exata: `Administrador autoriza execução!`. Na ausência dessa string exata, qualquer acesso ao banco de produção, inclusive READ ONLY, permanece proibido.
- Quando houver autorização explícita para produção, limitar a execução estritamente ao escopo solicitado, preservar as regras de multitenância e aplicar as salvaguardas descritas na tarefa.
- Não usar SHA1 para senha. Usar bcrypt ou Argon2.
- Não misturar refatoração ampla com correção de bug.

## Política de economia de tokens

- Priorizar patches mínimos e localizados.
- Não inspecionar migrations, Dockerfile ou pyproject.toml salvo se o erro envolver esses arquivos.
- Para erro de rota FastAPI, verificar apenas:
  - endpoint
  - schema
  - CRUD chamado
  - model envolvido
- Evitar respostas longas.
- Não repetir código inteiro; mostrar apenas trechos alterados.
- Não criar documentação extensa durante correção de bug.

## Antes de finalizar

- Informar endpoints afetados.
- Informar impacto no Web.
- Informar impacto no Mobile offline-first.
- Rodar testes relevantes, se disponíveis.
- Mostrar riscos restantes.
