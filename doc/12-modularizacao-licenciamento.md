# Modularização e Licenciamento

## Objetivo e conceitos

A Sprint 0 funda a comercialização modular dentro do monólito atual. Um
**módulo** é um produto comercial; uma **feature** é uma capacidade interna; um
**entitlement** é a concessão comercial do módulo a uma empresa em determinado
escopo. Isso não substitui multitenancy nem ACL/permissão por usuário.

## Implementado

O modelo contém `modulos`, `modulo_funcionalidades`, `modulo_entitlements` e
`modulo_entitlement_funcionalidades`. O entitlement possui FKs reais para
Empresa, Projeto e Pesquisa, permite exatamente um escopo (Empresa = ambos
nulos; Projeto ou Pesquisa = uma FK preenchida) e status `ATIVO`, `SUSPENSO` ou
`CANCELADO`. Índices únicos parciais impedem licenças equivalentes duplicadas em
cada escopo.

Uma licença é efetiva quando está `ATIVO`, já iniciou (se houver início) e ainda
não expirou (se houver expiração). Licenças são aditivas: Empresa alcança seus
Projetos/Pesquisas; Projeto alcança suas Pesquisas; não há override negativo.
Features precisam ser concedidas uma a uma e pertencer ao módulo do
entitlement. Tenant é validado antes do escopo; recursos cross-tenant retornam
404 e licenciamento nunca concede acesso ao dado.

O catálogo inicial contém `inteligencia_eleitoral`. A feature
`potencial_crescimento` é apenas planejada e foi semeada inativa, portanto não é
retornada como utilizável. Nenhuma empresa recebe entitlement no seed.

`GET /usuarios/me/modulos/` deriva a empresa principal do usuário autenticado,
retorna somente chaves/nomes comerciais e responde lista vazia com HTTP 200.

### Enforcement implementado no Prompt 02

`require_module(chave, contexto)` exige módulo ativo e ao menos um entitlement
efetivo aplicável. `require_feature(modulo, feature, contexto)` também exige
feature ativa e vínculo explícito em um entitlement aplicável. Os contextos
reutilizáveis são Empresa, Projeto e Pesquisa.

Ordem: autenticação -> autorização do recurso/tenant -> entitlement -> endpoint.
Sem Projeto/Pesquisa, só licença de Empresa vale. No Projeto valem Empresa ou o
próprio Projeto. Na Pesquisa valem Empresa, Projeto pai ou a própria Pesquisa.
Entitlements continuam aditivos e um suspenso específico não nega outro amplo
ativo.

HTTP 404 protege recurso não autorizado e oculta catálogo inexistente/inativo;
HTTP 403 representa capacidade ativa não contratada, suspensa ou fora da janela
temporal. Nenhum erro expõe IDs ou datas contratuais. O tenant nunca vem de
query/body/header; no contexto multiempresa vem do recurso autorizado.

### Ciclo administrativo implementado no Prompt 04

```text
Catálogo -> Entitlement -> Features -> Capabilities -> Backend Gate -> Web Gate
```

Superadmin consulta catálogo read-only e administra entitlement por empresa.
Criação valida escopo/tenant/features/datas/duplicidade; PATCH preserva módulo e
escopo; PUT define exatamente as features; status preserva histórico sem DELETE.
Toda mutação é auditada. Cobrança, self-service, ACL individual por feature,
Mobile modular e Potencial de Crescimento continuam planejados.

## Exemplos

- Licença Empresa A + Projeto A1: a licença ampla continua válida.
- Licença de Projeto A1: não vale em A2.
- Licença de Pesquisa A1/P1: não vale em A1/P2.
- Empresa A informando recurso da Empresa B: 404 antes da resolução.
- Feature nova no catálogo: não entra em contratos existentes sem vínculo.

## Invariantes de segurança

O cliente operacional não seleciona `company_id`; FKs de escopo são reais;
Projeto/Pesquisa precisam pertencer ao tenant; o endpoint não expõe IDs internos
ou contratos de outro tenant; ausência de licença não bloqueia funcionalidades
legadas nesta fase.

## Planejado, não implementado

Sprint 0: Prompt 03 contexto/gates Web; Prompt 04
administração de licenças; Prompt 05 QA/hardening. Também não implementados:
ACL por feature, cobrança, FeatureGate Web, mudanças Mobile e motor/algoritmo de
Potencial de Crescimento.

## Validação executável (Prompt 01B)

Runtime temporário: Python Windows 3.13 com as faixas de dependência do projeto.
`alembic heads` retornou uma única head `c6d7e8f9a0b1`; `history` confirmou a
relação com `b5c6d7e8f9a0`. Upgrade em SQLite descartável criou as quatro tabelas
e os três índices únicos parciais; downgrade para `b5c6d7e8f9a0` e re-upgrade
passaram. O teste de domínio passou com 18 casos e a regressão backend passou
com 1481 testes, 12 skips e 81 warnings preexistentes. A validação do endpoint
confirmou lista vazia sem entitlement, isolamento de tenant e exclusão da
feature planejada inativa. Nenhum banco do `.env` foi acessado.
