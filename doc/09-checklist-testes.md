# Pesquisa360 — Checklist de Testes

**Status:** checklist manual obrigatório antes de novas entregas  
**Objetivo:** padronizar validação e evitar regressões.

## 1. Ambiente

Backend:

```powershell
cd C:\Dev\pesquisa360_backend
python -m compileall pesquisa360
```

Web:

```powershell
cd C:\Dev\pesquisa360-web
npm run build
```

Mobile:

```powershell
cd C:\Dev\pesquisa360_app
flutter analyze
flutter test
```

## 2. Autenticação

- [ ] Login com usuário válido.
- [ ] Token retornado.
- [ ] `/usuarios/me/` retorna usuário real.
- [ ] `company_id` presente.
- [ ] Login inválido falha.
- [ ] Logout remove token.
- [ ] Rotas privadas redirecionam sem token.
- [ ] Não existe fallback de usuário fake.

## 3. Multitenancy

- [ ] Usuário A vê apenas projetos da Empresa A.
- [ ] Usuário B vê apenas projetos da Empresa B.
- [ ] Usuário B não acessa projeto A por URL.
- [ ] Usuário B não acessa pesquisa A por URL.
- [ ] Usuário B não acessa relatório A.
- [ ] Usuário B não acessa monitoramento A.
- [ ] Criação de projeto não aceita `company_id` do frontend.
- [ ] Criação de coleta associa `agente_id=current_user.id`.

## 4. Projetos

- [ ] Listar projetos.
- [ ] Criar projeto.
- [ ] Editar nome/descrição/status.
- [ ] Soft delete de projeto.
- [ ] Projeto excluído aparece com tag ou some conforme UX definida.
- [ ] Projeto de outro tenant não aparece.

## 5. Pesquisas

- [ ] Criar pesquisa.
- [ ] Editar pesquisa.
- [ ] Soft delete de pesquisa.
- [ ] Pesquisa excluída aparece com tag ou some conforme UX definida.
- [ ] Não há erro de rota `PATCH`.
- [ ] Não há erro de service com função inexistente.

## 6. Perguntas

- [ ] Criar pergunta de escolha simples.
- [ ] Criar pergunta de múltipla escolha.
- [ ] Editar pergunta.
- [ ] Adicionar opção.
- [ ] Remover opção.
- [ ] Soft delete de pergunta sem respostas.
- [ ] Bloquear exclusão com respostas, se regra ativa.
- [ ] Perguntas excluídas não aparecem por padrão.
- [ ] Toggle exibe perguntas excluídas com tag.

## 7. Cerca global

- [ ] Abrir Gestão de Território.
- [ ] Desenhar polígono.
- [ ] Confirmar habilita.
- [ ] Salvar cerca.
- [ ] Recarregar página.
- [ ] Cerca salva reaparece.
- [ ] Mapa abre na cerca global.
- [ ] Sem cerca, mapa abre em Macapá-AP.
- [ ] Não duplica controles Leaflet.

## 8. Setores e agentes

- [ ] Agentes carregam no select.
- [ ] Apenas agentes são listados.
- [ ] Desenhar setor.
- [ ] Salvar setor.
- [ ] Setor aparece em lista.

## 20. Modularização e Gates (Prompt 03)

### 20.1 Sem Licença

- [ ] Login tenant sem módulo (ex.: Inteligência Eleitoral).
- [ ] Core funciona: Projetos, Pesquisas, Relatórios, Monitoramento carregam.
- [ ] Menu "Inteligência Eleitoral" não aparece.
- [ ] URL `/inteligencia` redireciona para `/projetos`.
- [ ] URL `/projetos/:id/pesquisas/:id/inteligencia` redireciona para `/projetos`.

### 20.2 Com Módulo

- [ ] Login tenant com Inteligência Eleitoral.
- [ ] Menu "Inteligência Eleitoral" aparece.
- [ ] Clique em menu navega para `/inteligencia`.
- [ ] Shell do módulo abre.
- [ ] Feature `potencial_crescimento` (inativa) não aparece como utilizável.

### 20.3 Erro na API /usuarios/me/modulos/

- [ ] Simular falha (500, timeout, rede).
- [ ] Usuário continua autenticado.
- [ ] Core continua funcional.
- [ ] Menu modular não aparece.
- [ ] Rota modular nega acesso.

### 20.4 Troca de Tenant

- [ ] Empresa A (COM módulo) → Logout → Empresa B (SEM módulo).
- [ ] Empresa A: `hasModule("inteligencia_eleitoral")` = true.
- [ ] Logout: `modulesStore` = `{ modules: [], status: 'idle', error: null }`.
- [ ] Durante loading de B: NÃO vaza capabilities de A.
- [ ] Empresa B ready: `hasModule("inteligencia_eleitoral")` = false.
- [ ] Menu não aparece. URL direta nega acesso.
- [ ] Setor aparece no mapa.
- [ ] Tolerância aparece corretamente.
- [ ] Excluir setor.
- [ ] Setor some da lista.
- [ ] Setor some do mapa.
- [ ] Recarregar mantém setores restantes.
- [ ] Controles Leaflet não duplicam.

- [ ] CLI lista apenas gerente/superadmin ativo com tenant ativo.
- [ ] CLI restringe projeto, pesquisa ativa e agente ao tenant selecionado.
- [ ] Dry-run valida arquivo, CRS, geometria, duplicidade e nao grava setor.
- [ ] Polygon EPSG:4326 e aceito; MultiPolygon e uniao desconexa sao rejeitados.
- [ ] Importacao confirmada persiste `meta`, `tolerancia` e `geometria` pelo CRUD existente.
- [ ] Editar nome, meta, tolerância e agente preserva o ID do setor.
- [ ] Editar vértices persiste o novo Polygon EPSG:4326.
- [ ] Salvar sem alterar o mapa preserva a geometria existente.
- [ ] Projeto, pesquisa, setor ou agente de outro tenant retorna `404`.
- [ ] Polygon inválido não deixa alterações parciais no setor.

Futuro - finalidade territorial:

- [ ] Setores existentes sao interpretados/migrados como `OPERACAO`.
- [ ] Criar setor `OPERACAO` exige regras operacionais aplicaveis.
- [ ] Criar setor `RELATORIO` nao exige agente, meta/cota ou tolerancia
  operacional.
- [ ] Criar setor `AMBOS` cumpre regras operacionais quando usado em operacao.
- [ ] Setor `RELATORIO` exclusivo nao aparece no download Mobile, monitoramento
  operacional, geofence operacional ou cotas.
- [ ] Edicao de nome, geometria e finalidade nao reclassifica historico
  silenciosamente.
- [ ] Importacao de Shapefile permite escolher finalidade sem aceitar
  `company_id` do cliente.

## 9. Seed de coletas

- [ ] Criar 100 coletas via API real.
- [ ] Todas as obrigatórias respondidas.
- [ ] Coordenadas dentro da área definida.
- [ ] 80 online / 20 offline para teste.
- [ ] 80 com endereço / 20 sem endereço para teste.
- [ ] `status_sincronizacao=sincronizado`.

## 10. Relatórios

Resumo:

- [ ] Abrir Central de Relatórios.
- [ ] Entrar em Resumo das Respostas.
- [ ] Exibir total de entrevistas.
- [ ] Carregar todas as perguntas.
- [ ] Cada pergunta obrigatória soma 100 respostas no seed.
- [ ] Sem erro no console.

Crosstab:

- [ ] Abrir Cruzamento de Dados.
- [ ] Perguntas categóricas aparecem.
- [ ] Texto da pergunta aparece nos cards.
- [ ] `escolha_simples` é elegível.
- [ ] Selecionar 2 perguntas.
- [ ] Gerar crosstab.
- [ ] Selecionar 5 perguntas.
- [ ] Gerar 10 combinações 2 a 2.
- [ ] Gráficos renderizam.
- [ ] Sem erro CORS/500/ResponseValidationError.

Futuro - Mapas Estrategicos:

- [ ] Cobertura das Coletas filtra setores analiticos por tenant.
- [ ] Resultado por Setor usa setores `RELATORIO` ou `AMBOS`.
- [ ] Lideranca por Setor nao duplica entrevistas em sobreposicao/conflito.
- [ ] Distribuicao de Coletas classifica coleta fora de setor como `SEM_SETOR`.
- [ ] Classificacao espacial roda no Backend/PostGIS, preferencialmente com
  `ST_Covers`.
- [ ] Localizacao inicial e usada como referencia principal; localizacao final
  funciona como fallback.
- [ ] Relatorio Executivo de Mapas respeita filtros, escolha de setores e
  previa.

## 11. Monitoramento

- [ ] Abrir monitoramento.
- [ ] Carregar coletas.
- [ ] Registros mostra total correto.
- [ ] Marcadores aparecem.
- [ ] Azul = online.
- [ ] Vermelho = offline.
- [ ] Legenda correta.
- [ ] Tabela mostra 10 por página.
- [ ] Mostrar todas ativa scroll interno.
- [ ] Não há rolagem horizontal.
- [ ] Endereço aparece nos detalhes.
- [ ] Coleta sem endereço mostra “Endereço não processado”.
- [ ] Filtro por agente funciona.
- [ ] Filtro por setor funciona.
- [ ] Clicar em coleta foca ponto no mapa.

## 12. Mobile

- [ ] Login.
- [ ] `/usuarios/me/`.
- [ ] Download de pesquisas.
- [ ] Abrir formulário offline.
- [ ] Preencher coleta offline.
- [ ] Capturar GPS início/fim.
- [ ] Nova coleta persiste um `client_uuid` único.
- [ ] Retry reutiliza o mesmo `client_uuid` e não cria duplicidade.
- [ ] Coleta legada pendente recebe backfill persistente de `client_uuid`.
- [ ] Sincronizar.
- [ ] Coleta aparece no monitoramento.
- [ ] Agente correto.
- [ ] `foi_offline=true` quando sem internet.
- [ ] Logout limpa dados sensíveis.

## 13. Release

- [ ] Backend compile OK.
- [ ] Web build OK.
- [ ] Mobile analyze OK.
- [ ] Fluxos principais sem erro no console.
- [ ] Checkpoint git criado.
- [ ] Tags criadas.

## 14. Cruzamentos Estratégicos

Backend:

- [x] Payload legado de pergunta mantém defaults `null` e `{}`.
- [x] Metadados aceitam somente objeto e podem ser limpos com `{}`.
- [x] Duas, três e N dimensões respeitam ordem e profundidade.
- [x] Junção por `coleta_id`, contagem distinta e ausência de produto cartesiano.
- [x] Base válida, base pai, percentuais e base zero validados.
- [x] Sem resposta e múltipla escolha validadas.
- [x] Duplicidades são deduplicadas e geram aviso.
- [x] Perguntas inativas, estrangeiras e não categóricas são rejeitadas.
- [x] Outro tenant recebe 404 e o payload não aceita `company_id`.
- [x] Contrato do crosstab 2D permanece disponível.
- [x] Endpoint de opções retorna metadados, cardinalidade, contagem e origem.
- [x] Respostas espontâneas usam a categorização compartilhada, sem fuzzy matching.
- [x] Valor espontâneo não mapeado aparece como “Não categorizada”.
- [x] Filtros por resposta preservam bases e percentuais sem renormalização.
- [x] Caso de base 100 preserva A = 40% e B = 30% ao ocultar C = 30%.

Web/E2E:

- [x] Central de Inteligência contextual abre os Cruzamentos Estratégicos.
- [x] Central de Relatórios mantém Resumo e Crosstab 2D e não exibe card de Cruzamentos Estratégicos.
- [x] Seletor aceita múltiplas perguntas por checkbox e preserva a ordem.
- [x] Setas reordenam dimensões; detalhes expandem opções e cardinalidade.
- [x] Selecionar todas e Limpar funcionam nos filtros de resposta.
- [x] Opções com contagem zero continuam selecionáveis.
- [x] Modo Explorar usa cache de profundidades, breadcrumb e troca BAR/PIE/DONUT sem nova requisição.
- [x] Gráficos usam `percentual_pai` e exibem o contexto do caminho.
- [x] Modo Relatório agrupa nodos por caminho pai em segmentos.
- [x] Detalhes são opcionais e ficam ocultos por padrão.
- [x] Desktop usa tabela; mobile usa cards; não há scroll interno no relatório.
- [x] Impressão abre pelo navegador com HTML/SVG/CSS preparado para A4.
- [x] Fluxos desktop e mobile não geram resposta HTTP 4xx/5xx.

Execução validada:

```text
Backend compileall: PASS
Backend pytest focado: 62 passed, 23 warnings, 4 subtests passed
Web node --test tests/*.test.mjs: 125 passed
Web npm run lint: PASS
Web npm run build: PASS
E2E CDP consolidado: PASS, zero respostas HTTP >= 400
```

No E2E, os únicos avisos observados foram os avisos preexistentes de flags
futuras do React Router.

## ACL multiempresa/multiprojeto (ADR-034)

Backend — `tests/test_acl_multiempresa.py`:

- [x] backfill dá a todo usuário legado `acesso_todos_projetos` na própria empresa
- [x] backfill não cria linha por projeto e é idempotente (downgrade/re-upgrade)
- [x] `GET /projetos/` devolve A1, A2 e B1; nunca B2
- [x] acesso direto: A1/A2/B1 → 200, B2 → 404
- [x] projeto de outra empresa autorizado explicitamente → 200 (empresa principal continua A)
- [x] revogação vale com o MESMO token, sem novo login
- [x] `acesso_todos_projetos` alcança projeto criado depois
- [x] vínculo de empresa inativo bloqueia até projeto autorizado
- [x] Superadmin enxerga tudo sem nenhuma linha de ACL
- [x] coleta grava o `company_id` do PROJETO (não a empresa principal do agente)
- [x] `PUT /acessos` recusa projeto de outra empresa (422) sem gravar nada
- [x] payload inválido (empresa inexistente/inativa, duplicata, campo extra) → 422
- [x] rotas de ACL exigem Superadmin
- [x] `/usuarios/me/` mantém `company_id` e acrescenta `company_ids`
- [x] o filtro de ACL entra na própria query SQL (sem N+1)

Web — `tests/userAccess.test.mjs`: draft de 1 e de 2 empresas, seleção/remoção
de projetos, `acesso_todos_projetos`, empresa principal, payload sem
tenant/usuário, validação local, resumo "Empresa A +1", loading, erro e ausência
de request por linha na tabela.

## Cadastro por convite e ativação (ADR-035)

Backend — `tests/test_ativacao_conta.py` (23 testes):

- [x] política de senha: `abc123`/`pesquisa9` aceitas; `123456`, `abcdef`, `a12` recusadas
- [x] mensagens de senha não citam bcrypt/hash/salt
- [x] convite cria conta **inativa** no tenant do criador
- [x] `company_id` do payload é ignorado (Gerente não planta conta em outro tenant)
- [x] e-mail duplicado → 400, sem criar e sem convidar
- [x] criação não é anônima
- [x] banco guarda só o SHA-256 do token; token puro não aparece na tabela
- [x] e-mail traz link e validade, sem senha
- [x] validação distingue VALIDO / INVALIDO / EXPIRADO / UTILIZADO
- [x] ativação define senha, ativa a conta e consome o token
- [x] token é de uso único (2º uso → 400, senha não muda)
- [x] token expirado não ativa
- [x] senha fraca → 422 e o token **não** é consumido
- [x] confirmação divergente → 422
- [x] conta inativa não autentica; ativada autentica
- [x] usuário antigo continua logando
- [x] criação com senha mantém o comportamento anterior
- [x] reenvio invalida o convite anterior; recusado para conta ativa
- [x] falha ao gravar senha faz rollback e preserva o token
- [x] falha de e-mail não perde o convite

Web — `tests/activateAccount.test.mjs` (13 testes): política espelhada,
checklist de requisitos, textos por desfecho do token, `/ativar-conta` fora do
`ProtectedRoute`, rotas do service, estados (validando/formulário/bloqueado/
sucesso), bloqueio de senha inválida e divergente, ausência de login automático,
falha de rede distinta de token inválido, convite pela tela de admin.

## Link de convite no painel (ADR-036)

Backend — `tests/test_ativacao_conta.py::LinkConviteTests` (10 testes):

- [x] criação por convite devolve `activation_url` utilizável (valida e ativa)
- [x] **o token do link não existe em nenhuma tabela** (dump completo do banco)
- [x] criação com senha não inventa link (`convite: null`)
- [x] `GET /usuarios/` e `GET /admin/usuarios/` não vazam `activation_url`
- [x] reenvio devolve link novo e invalida o anterior
- [x] usuário ativo não recebe convite (400, sem link na resposta)
- [x] falha de e-mail mantém o link utilizável e sinaliza `email_enviado: false`
- [x] link usa `WEB_BASE_URL`, sem domínio fixo
- [x] gerar convite exige autorização administrativa
- [x] não existe rota para reler link antigo

Web — `tests/activationInvite.test.mjs` (13 testes): expiração formatada,
mensagem/URL do WhatsApp codificada e sem telefone, resumo da URL, texto de
falha de e-mail, cópia com fallback e sem `console`, modal (dados, ações,
feedback "✓ Copiado" temporário, responsivo, aviso de que o link não volta),
criação abrindo o modal, "Gerar novo convite" só para conta inativa, e o link
saindo do estado ao fechar — sem storage/store/analytics.

## RBAC por perfil (ADR-037)

Backend — `tests/test_rbac_perfis.py` (23 testes, chamadas HTTP diretas):

- [x] matriz: Cliente sem nenhuma capacidade de escrita; Agente só campo;
      Supervisor sem administração; Coordenador sem tenant; só Superadmin em empresas
- [x] perfil desconhecido não recebe permissão; usuário inativo idem
- [x] Cliente + ACL A1 + GET → 200
- [x] Cliente sem ACL em A2 → **404**
- [x] Cliente + ACL A1 + PATCH → **403** (e o dado não muda)
- [x] projeto de outro tenant → 404 para todos menos Superadmin
- [x] Cliente é read-only em 10 rotas de escrita
- [x] Cliente não administra usuários nem empresas
- [x] Agente faz missão/sync e recebe 403 no painel inteiro
- [x] `/usuarios/me/` aberto a qualquer autenticado, com `papel` e `permissions`
- [x] Supervisor monitora mas não administra
- [x] Coordenador gerencia só onde tem ACL
- [x] Gerente não atravessa tenant; Superadmin preserva capacidades
- [x] escopo é avaliado antes da capacidade (404 vence 403)
- [x] permissão não substitui ACL

Web — `tests/permissions.test.mjs` (13 testes): capacidades vindas do Backend,
fallback de sessão antiga, Cliente read-only, Agente fora do painel, papéis
intermediários, `PermissionRoute`, `/sem-permissao`, ações não renderizadas e
ausência de comparação de perfil solta nas telas.

## Documentação por ambiente (ADR-038)

Backend — `tests/test_documentacao_ambiente.py` (12 testes):

- [x] produção desliga `docs_url`, `redoc_url` e `openapi_url` **juntas**
- [x] desenvolvimento mantém os três caminhos padrão
- [x] `APP_ENV` decide o ambiente; default (ausente/vazio) = desenvolvimento;
      `production`/`producao`/`prod` (case-insensitive) = produção
- [x] o ambiente não é inferido de host/porta/Docker (análise da AST: só `APP_ENV`)
- [x] produção: `/docs`, `/redoc` e `/openapi.json` → **404**, sem redirect
- [x] produção: variantes com barra final também → 404, sem HTML do Swagger
- [x] produção: as rotas não estão sequer registradas em `app.routes`
- [x] produção: `/login/token`, `/usuarios/me/`, `/projetos/` e `/agente/pesquisas/`
      continuam registradas
- [x] produção: login não é 404 e rota protegida sem token responde 401
- [x] desenvolvimento: `/docs` e `/redoc` → 200 (HTML)
- [x] desenvolvimento: `/openapi.json` → JSON válido, com `openapi`, `paths` e `/login/token`
- [x] sem `APP_ENV`, o comportamento é o de hoje (compatibilidade)

## Auditoria de segurança (ADR-039)

Backend — `tests/test_auditoria.py` (41 testes, por HTTP com o middleware real):

- [x] login inválido registra `attempted_email`, motivo interno e **nenhuma senha**
- [x] conta inativa e login válido (motivo só na trilha; cliente vê mensagem genérica)
- [x] ip, user-agent e `request_id` vêm do middleware; `request_id` == header `X-Request-ID`
- [x] `X-Forwarded-For` só vale com `AUDIT_TRUST_PROXY=true` (primeiro IP da cadeia)
- [x] `RBAC_DENIED` registra permissões exigidas, papel, método e caminho
- [x] negação de projeto classificada: `sem_acl` / `outro_tenant` (HIGH) / `inexistente`
- [x] `PROJECT_ACCESS` nasce no Backend, no `GET /projetos/{id}`
- [x] evento sobrevive ao rollback da request (sessão própria)
- [x] falha na auditoria **não** derruba a request
- [x] `details` nunca carrega segredo (filtro por chave, inclusive aninhado)
- [x] registro fora de request funciona sem contexto
- [x] leitura restrita a Gerente/Superadmin via dependency declarada, com filtros
- [x] trilha append-only: FKs nullable sem cascade; serviço sem `update`/`delete`

PROJECT_ACCESS — deduplicação de 15 min (AU14–AU19):

- [x] GET autorizado cria `PROJECT_ACCESS`
- [x] mesmo usuário + mesmo projeto dentro de 15 min → continua 1 evento
- [x] fora da janela (relógio controlado por `agora`/`occurred_at`) → novo evento
- [x] usuário diferente → novo evento
- [x] projeto diferente → novo evento
- [x] Superadmin em projeto de outro tenant → `company_id` do evento = tenant do projeto

API `GET /admin/auditoria/eventos` (AU20–AU36):

- [x] Superadmin lista eventos globais e filtra `company_id`
- [x] Gerente lista só o próprio tenant; `company_id` de outro tenant não vaza; evento de outro tenant não aparece
- [x] Coordenador, Supervisor, Cliente e Agente → 403
- [x] filtros `event_type`, `severity`, `project_id`, `user_id`, `ip_address`, `data_inicio`/`data_fim`
- [x] mais recente primeiro (`occurred_at DESC`)
- [x] limite máximo da página = 100 (`limit=500` → 422; `limit=100` aceito)

Segredos e regressões de contrato (AU37–AU41):

- [x] senha marcadora, JWT emitido, `Authorization: Bearer`, token de ativação e `activation_url`: **0 ocorrências** na tabela serializada
- [x] query string (`?token=SEGREDO_QUERY`) nunca persiste; `path` = só o caminho
- [x] token expirado → `TOKEN_EXPIRED`; token inválido → `TOKEN_INVALID` (sem segundo parse)
- [x] Empresa A pede projeto da B: 404 externo **e** `CROSS_TENANT_ACCESS_ATTEMPT` HIGH interno
- [x] Cliente com ACL faz PATCH: 403 externo e **um único** `RBAC_DENIED`

## Notificação de acesso ao projeto (ADR-040)

Backend — `tests/test_notificacao_acesso_projeto.py` (21 testes, por HTTP, SMTP mockado com contador real):

- [x] acesso autorizado → 1 `PROJECT_ACCESS` → 1 e-mail ao Gerente responsável → `NOTIFICATION_SENT`
- [x] segundo GET na janela de 15 min → nenhum evento novo, nenhum segundo e-mail
- [x] fora da janela (relógio controlado) → novo evento, novo e-mail
- [x] usuário diferente → e-mail próprio; projeto diferente → destinatário do outro projeto
- [x] Gerente responsável acessa o próprio projeto → `PROJECT_ACCESS` continua, `SUPPRESSED self_access`
- [x] Superadmin acessa projeto → Gerente responsável é notificado (`company_id` = tenant do projeto)
- [x] projeto sem responsável / responsável inativo / sem e-mail → `SUPPRESSED` (`no_project_manager` · `inactive_recipient` · `missing_recipient_email`), sem 500
- [x] SMTP ok → `SENT`; SMTP recusa ou lança exceção → GET continua 200, evento persistido, `FAILED` WARNING
- [x] `SMTP_HOST` ausente → `SUPPRESSED smtp_not_configured`, não `FAILED`
- [x] e-mail não contém JWT, `Authorization`, `Bearer`, senha, token, activation, cookie, nem dado de coleta
- [x] cross-tenant (404), sem ACL (404) e RBAC (403) → zero notificações
- [x] dois Gerentes no tenant → **somente** o responsável recebe (1 × 0)
- [x] conteúdo mínimo: assunto com nome do projeto, destinatário, ator, perfil, empresa, data/hora `(UTC)`, IP, User-Agent, link `WEB_BASE_URL/projetos/{id}` (sem link de API)
- [x] 10 GETs consecutivos → 1 evento, 1 tentativa de e-mail
- [x] `montar_mensagem` é pura: formata UTC e trunca User-Agent em 160 chars

## Painel administrativo de segurança (ADR-041)

Backend — `tests/test_painel_seguranca.py` (22 testes, `GET /admin/auditoria/resumo` por HTTP):

- [x] Superadmin: totais exatos globais (PS01); Gerente: só o próprio tenant (PS02)
- [x] Gerente com `company_id` de outro tenant não atravessa (PS03)
- [x] Cliente, Supervisor, Coordenador e Agente → 403 (PS04–PS07)
- [x] `login_failed` = LOGIN_FAILED + ACCOUNT_INACTIVE_LOGIN; LOGIN_SUCCESS não conta (PS08)
- [x] `access_denied` = RBAC + ACL; cross-tenant separado, sem dupla contagem (PS09)
- [x] `project_access` usa os eventos já deduplicados: 5 GETs → 1 (PS10)
- [x] `notification_failed` ignora SUPPRESSED/SENT (PS11); `high` conta severidade (PS12)
- [x] top IPs ordenado por volume, ≤ 10 (PS13); LOGIN_SUCCESS/PROJECT_ACCESS/SENT não contaminam (PS14)
- [x] top contas agrupa `attempted_email`, sem senha (PS15)
- [x] período exclui eventos fora da janela; granularidade hora/dia (PS16)
- [x] filtro por projeto (PS17) e por usuário; Gerente pedindo `user_id` de outro tenant → 0 no resumo e na lista (PS18)
- [x] Gerente não recebe eventos globais sem `company_id`; Superadmin recebe (PS19)
- [x] agregação em SQL: nenhum `AuditEvent` materializado; consultar não grava evento (PS20); rota só GET com `require_manager_or_superadmin` (PS21)
- [x] QA real por HTTP: 1 login inválido, 1 PATCH 403, 1 cross-tenant, 1 acesso legítimo, 1 falha simulada de notificação → cada um na categoria certa; lista mais recente primeiro; Gerente da outra empresa vê 0 (PS22)

Web — `tests/security.test.mjs` (30 testes):

- [x] menu Segurança: Superadmin e Gerente sim; Cliente, Supervisor, Coordenador, Agente não (PW01–PW06)
- [x] rota manual sob `PermissionRoute` → `/sem-permissao` (PW07)
- [x] cards vêm do resumo agregado; loading, erro com "Tentar novamente", vazio (PW08–PW11)
- [x] filtros de período (24h default, 7d, 30d, personalizado), evento, severidade, IP, projeto e usuário alteram a query (PW12–PW15)
- [x] paginação `limit/offset`, máximo 100, rótulo "1–50 de 328" (PW16)
- [x] Gerente nunca envia `company_id` e vê empresa somente leitura; Superadmin tem seletor (PW17–PW18)
- [x] detalhe em Dialog com todos os campos; `details` sem chaves sensíveis, nem aninhadas (PW19–PW20)
- [x] nomes amigáveis para todos os eventos; severidade com texto/marcador (PW21–PW22)
- [x] card cross-tenant com valor e filtro por clique, sem animação alarmista (PW23)
- [x] rankings de IP (sem "malicioso") e de contas renderizam (PW24–PW25)
- [x] série temporal com 3 séries, sem PROJECT_ACCESS; painel sem ação de bloqueio (extras)

## Cotas por Perfil — painel Web e ADR-034

Backend — `tests/test_cota_perfil.py` (+2) e `tests/test_base_eleitoral_multiempresa.py` (+1):

- [x] Superadmin de outra empresa configura e lê o plano; plano gravado no tenant do Projeto (CP_B21)
- [x] usuário principal A com ACL no Projeto B: perguntas B, Base B, municípios B, plano B (`company_id` = B); município da base A → 404; Gerente B lê o mesmo plano (CP_B22)
- [x] territórios/detalhe da Base do Projeto legíveis por Superadmin e ACL cruzada; sem ACL → 404; leitura não concede escrita (test_13)
- [x] suítes existentes preservadas: GET/PUT plano, progresso, classificação sexo/idade, município, tenant, pergunta de outra pesquisa, faixas sobrepostas/repetidas (CP_B01–B20)

Web — `tests/profileQuota.test.mjs` (13 casos):

- [x] perguntas elegíveis só da Pesquisa (escolha simples; idade também numérica); inativas/texto fora
- [x] sugestões de mapeamento; "35 a 34 anos" gera aviso e não é corrigido
- [x] totais por linha/coluna/geral e percentual visual
- [x] payload real do PUT (perguntas, modo, `sexo_valores`, `territorios/cotas`, `ativo`) sem `company_id`/`tenant_id`
- [x] validações: pergunta ausente/iguais, sexo sem mapear, faixa sem opção, sem município, duplicado, negativo
- [x] status visual verde/amarelo/cinza/vermelho (vermelho só com erro real)
- [x] GET real → formulário preenchido; editar meta reflete no novo PUT; progresso não é tocado
- [x] edição só Gerente/Superadmin; mensagens por 403/404/422/500/rede sem `AxiosError`
- [x] service com rotas reais, 404 = sem plano, PUT idempotente
- [x] card: "Não configurada" → "Configurar Cotas por Perfil"; Ativa/Inativa; vazio ≠ erro; Base ausente bloqueia; desativar com aviso, sem DELETE; confirmação forte ao trocar variáveis com coletas; link Controle de Campo
- [x] setup: perguntas, mapeamentos, municípios com busca/accordion, revisão, "Salvar e ativar", sem double-submit
- [x] painel na aba Configuração Analítica com municípios da Base do Projeto; não vive na Gestão de Território

## Setor → Município + Cotas (ADR-035-B)

Backend — `tests/test_setor_municipio.py` (10 testes):

- [x] composição em Macapá → `municipio_territorio_id` persistido e exposto no GET (47); backfill da migration preenche só composição única (47b)
- [x] fronteira 82/18 → AMBIGUO sem escolher o maior; 100% → RESOLVIDO; ruído <1% ignorado; composição em dois municípios em setor OPERACAO → 422 (48)
- [x] setor RELATORIO multi-municipal permitido, NULL, fora do contexto de perfil (49)
- [x] composição movida → referência recalculada; manual válido aceito; fora da Base ou bairro → 404 (50)
- [x] coleta Centro/Homem/23 → Centro realizado +1 e Macapá Homem 16–24 +1; não depende do nome do setor (51)
- [x] Centro + Zona Norte → cada setor +1, Macapá Homem 16–24 +2; meta territorial consolidada 250 no diagnóstico (52)
- [x] Macapá e Santana não se misturam (53)
- [x] usuário principal A com ACL no Projeto B resolve pela Base B; município da Base A → 404; sem ACL → 404 (54)
- [x] contexto territorial e situação para a UI (55)

Web — `tests/setorMunicipio.test.mjs` (5) e `tests/profileQuota.test.mjs` (+1):

- [x] tipo Setor com `municipio`/`municipio_status`; card "Município: X" pelo vínculo; "Município indefinido" sem esconder
- [x] formulário: referência detectada (somente leitura), sem seleção manual, sem hardcode, sem company_id
- [x] composição: cabeçalho com Município de referência e compatibilidade das unidades (sinaliza, não bloqueia)
- [x] Controle de Campo: coluna Município vem do backend
- [x] Cotas: etapa Municípios com setores + meta consolidada; revisão com cobertura territorial e diferença informativa; referência da matriz pré-preenchida
# Modularização / Entitlements

Cobertura automatizada em `tests/test_modulos_entitlements.py`: 18 testes
passaram. A suíte completa também passou: 1481 passed, 12 skipped, 81 warnings.

- [x] Empresa A resolve somente licenças da Empresa A.
- [x] Empresa A não usa Projeto/Pesquisa da Empresa B (404).
- [x] Empresa B não aparece em `GET /usuarios/me/modulos/` da Empresa A.
- [x] Empresa sem licença recebe HTTP 200 e lista vazia.
- [x] Escopos Empresa, Projeto e Pesquisa; herança ampla aditiva.
- [x] Suspenso, futuro e expirado não concedem; janela válida concede.
- [x] Features são explícitas e precisam pertencer ao módulo contratado.
- [x] `company_id` de query não troca tenant.
- [x] Rotas existentes permanecem sem gating.

## Backend Module Gates

- [x] Empresa sem licença recebe 403 em recurso autorizado.
- [x] Licenças de Empresa, Projeto e Pesquisa respeitam seus alcances aditivos.
- [x] Suspensa, expirada e ainda não iniciada não autorizam.
- [x] Feature ativa exige concessão explícita; feature nova não é herdada.
- [x] Feature ou módulo inativo não pode ser utilizado, mesmo com vínculo.
- [x] Isolamento cross-tenant e tenant do recurso multiempresa preservados.
- [x] Recurso retorna 404 antes de qualquer 403 de entitlement.
- [x] `company_id` de query não altera o contexto.
- [x] G01–G26 exercitados por rotas exclusivas da aplicação de teste.

## Administração de Licenças

- [x] Somente Superadmin; Gerente/Cliente e URL manipulada recebem 403.
- [x] Empresa alvo e escopos Empresa/Projeto/Pesquisa validados.
- [x] Projeto/Pesquisa cross-tenant rejeitados.
- [x] Duplicidade retorna 409; datas/escopo inválidos retornam 422.
- [x] Feature explícita, do mesmo módulo e ativa; inativa rejeitada.
- [x] Suspensão, reativação e cancelamento sem DELETE físico.
- [x] Capabilities refletem mutação na consulta seguinte.
- [x] Auditoria registra ator, empresa, entitlement e before/after.
- [x] Web: guard Superadmin, loading/error/empty, confirmações e feature disabled.

## Baseline final da Sprint 0 — Prompt 05D

- [x] Python 3.13.14: migration chain, subprocessos e regressão sem WinError 50.
- [x] PostgreSQL descartável: `upgrade head` → `downgrade base` → `upgrade head`.
- [x] Migrations históricas `28f012bafc15` e `91fbe6db1f17` removem somente os
  objetos criados pelos respectivos upgrades.
- [x] IDOR: entitlement de outra empresa não é alterado e retorna 404.
- [x] Mass assignment: `company_id`, ator, módulo, escopo, `ativo` e `chave`
  são rejeitados com 422 e não produzem alteração/auditoria indevida.
- [x] Transacionalidade: falha controlada de commit desfaz mutação e audit event.
- [x] Não-Superadmin não administra nem eleva a própria licença.
- [x] Recurso/tenant não autorizado retorna 404 antes do 403 comercial.
- [x] Logout A→B limpa capabilities; loading, erro e ausência de licença fecham
  menu/gate/rota sem invalidar a autenticação nem o Core.
- [x] Backend: 206 focados + 28 subtests; regressão 1529 passed, 13 skipped.
- [x] Web: 1119/1119; build aprovado; lint no baseline preexistente de 10 erros.

## Potencial de Crescimento — Configuração (MVP 1, Prompt 02)

Suíte automatizada: `tests/test_growth_analysis_configuration.py` (C01–C65).

- [x] Candidatura: bindings explícitos por pergunta; valor inexistente rejeitado;
  binding ausente para sinal candidato-específico rejeitado; binding sem uso
  gera warning.
- [x] Cenário: SINGLE/MULTIPLE/ORDERED_MULTIPLE; slots/orders incoerentes
  rejeitados; MULTIPLA_ESCOLHA como intenção exige ballot MULTIPLE.
- [x] Taxonomia: classes especiais disjuntas entre si e disjuntas da
  candidatura; `__SEM_RESPOSTA__` inconfigurável.
- [x] Sinais: intenção obrigatória via cenário; rejeição/segunda opção/decisão
  opcionais com warning; pergunta TERRITORIAL, inativa, de outra pesquisa ou de
  tipo incompatível rejeitada.
- [x] Base mínima: sem default numérico; `warn > suppress ≥ 1` validado.
- [x] Perfil: 1–2 dimensões; grupos categóricos disjuntos; faixas numéricas
  inclusivas sem inversão/sobreposição.
- [x] Território: NONE/SETOR/MUNICIPIO; setor de outra pesquisa 404-like;
  setor OPERACAO-only rejeitado; MUNICIPIO indisponível sem resolução oficial.
- [x] Espontânea: categoria ativa aceita; inexistente/inativa rejeitada; sinal
  espontâneo exige `max_uncategorized_rate` explícito (0–1 exclusivos).
- [x] Ponderação: somente NAO_PONDERADO; contrato não expõe weighted_base.
- [x] Multitenancy: sem `company_id` no contrato; pesquisa/pergunta cross-tenant
  indistinguíveis de inexistentes.
- [x] Serialização: JSON determinístico, round-trip preserva semântica, sem
  campos ORM/tenant.

## Potencial de Crescimento — Motor Estatístico (MVP 1, Prompt 03)

Suíte automatizada: `tests/test_growth_analysis_engine.py` (E01–E112).

- [x] Universo: survey/analytical/eligible explícitos; filtros estruturais
  congelam o universo; nenhuma entrevista contada duas vezes (1 Coleta = 1
  observação).
- [x] Elegibilidade: apoiador atual excluído (D01); especiais por política
  explícita; políticas divergentes → exclusão conservadora diagnosticada;
  sem resposta de intenção → unknown, nunca indeciso.
- [x] Ballot modes: SINGLE, MULTIPLE (alvo em qualquer posição) e
  ORDERED_MULTIPLE (alvo em qualquer slot).
- [x] Denominadores: base válida por sinal = entrevistas com resposta
  reportável; ausência ≠ zero; decisão do voto só conta valores dos grupos.
- [x] Segmentação: apenas cruzamentos configurados; faixas numéricas
  inclusivas; missing/unmatched diagnosticados; segment_key determinística;
  queries não crescem com segmentos.
- [x] Sinais: segunda opção, rejeição (múltipla conta entrevista uma vez;
  taxa é rejeição, não 1−rejeição) e decisão do voto com oráculos de mão.
- [x] Wilson: valores conhecidos (0/n, n/n, intermediário), z de 95%,
  intervalo em fração 0–1, n=0 indisponível; sem p-value/significância.
- [x] Base mínima: supressão de finding e de evidência por sinal; alerta
  SMALL_BASE; outros sinais permanecem disponíveis.
- [x] Espontânea: categoria ativa resolve; "Não categorizada" na base;
  limiar excedido bloqueia o sinal (SIGNAL_UNAVAILABLE_QUALITY).
- [x] Determinismo: hash de configuração, input_fingerprint, execução
  repetida idêntica exceto executed_at; findings ordenados.
- [x] Ausência de score/ranking/projeção: nenhum campo de score, rank,
  projected/potential votes; eleitorado_apto apenas contexto.
- [x] Multitenancy: pesquisa cross-tenant não executa; coleta de outro
  tenant fora do universo; contrato sem company_id.

## Potencial de Crescimento — API (MVP 1, Prompt 04)

Suíte automatizada: `tests/test_growth_analysis_api.py` (A01–A87 + E2E).

- [x] Auth: sem token → 401; perfil sem INTELIGENCIA_VER → 403.
- [x] ACL/multitenancy: projeto/pesquisa inexistentes, pesquisa fora do
  projeto do path e recurso cross-tenant → 404, sempre ANTES do 403
  comercial.
- [x] Feature/entitlement: inativa → 404 CAPACIDADE_INDISPONIVEL; ativa sem
  entitlement → 403; escopos Empresa/Projeto/Pesquisa concedem; escopo de
  outro projeto/pesquisa nega; aditividade preservada (suspenso específico
  não anula amplo ativo).
- [x] Path/body: pesquisa divergente → 422 SURVEY_PATH_BODY_MISMATCH;
  company_id/tenant_id no body → 422.
- [x] Options: só perguntas ativas da Pesquisa; compatibilidades por regra
  técnica (territorial nunca é sinal; TEXTO com papel PERFIL não vira
  perfil); valores canônicos; categorias espontâneas ativas; setores da
  Pesquisa com elegibilidade analítica; municípios oficiais; constraints
  sem defaults metodológicos.
- [x] Validação: 200 valid=false com issues tipadas (code/path/context);
  VALUE_NORMALIZED + configuração normalizada; motor não executa (spy).
- [x] Análise: motor executa uma vez; 422 tipados
  (GROWTH_CONFIGURATION_INVALID, SEGMENT_LIMIT_EXCEEDED); erro inesperado
  não vira 422.
- [x] Números: métricas como JSON numbers em unidade canônica (0–1, pp,
  razão), nunca strings; null preservado (weighted_base, lift indefinido).
- [x] Warnings metodológicos chegam estruturados ao HTTP.
- [x] Determinismo via HTTP (exceto executed_at); ordem dos findings
  preservada.
- [x] Provas negativas: sem score/rank/potential_level/projeção/company_id.
- [x] OpenAPI: rotas registradas, schemas nomeados, métricas como number.

## Potencial de Crescimento — Web (MVP 1, Prompt 05)

Suítes: `tests/growthPotential.test.mjs` (lógica pura) e
`tests/growthPotentialPage.test.mjs` (verificação estática) no repo Web.

- [x] Capabilities: rota sob ModuleRoute + FeatureRoute (fail-closed em
  loading/error); card na Central só com feature pronta e concedida.
- [x] Service: três endpoints reais, apiClient autenticado, sem company_id,
  erros Axios sobem para a página.
- [x] Options: loading/erro/vazio modelados; papel_analitico só como
  sugestão; escolhas por compatible_as; nenhuma heurística textual.
- [x] Configuração: bindings explícitos; SINGLE/MULTIPLE/ORDERED_MULTIPLE;
  taxonomia e include/exclude serializados; alvo nunca vira indeciso;
  espontânea exige threshold em % convertido para fração; 1–2 dimensões;
  faixas inclusivas com inversão detectada; base mínima sem defaults
  (warn > suppress ≥ 1, texto de entrevistas reais).
- [x] Validação: 200 valid=false exibe erros com code/path por seção;
  warnings separados; VALUE_NORMALIZED informado; valid=true guarda a
  configuração normalizada.
- [x] Dirty state: edição pós-validação desabilita Analisar até revalidar;
  Analisar envia somente a configuração normalizada.
- [x] Números: 0.187→18,7%; delta 7.7→+7,7 pp (nunca 770%); lift 1.8→1,80×
  (nunca 180%); intervalo 0–1→percentuais; null→"—" (weighted_base nunca 0).
- [x] Resultado: funil com contagens explícitas; warnings metodológicos
  visíveis; diagnostics separados dos segmentos; evidências com
  numerador/base de segmento e referência; base insuficiente nunca vira 0%.
- [x] Troca de Pesquisa: RESET total; resposta atrasada não sobrescreve
  (sequence + surveyKey); nada em localStorage/sessionStorage.
- [x] Erros HTTP: 422 tipados (config inválida, SEGMENT_LIMIT_EXCEEDED),
  403/404 fail-closed, 500 genérico.
- [x] Provas negativas: sem score, potential_level, ranking default,
  projected/potential votes, "chance de conversão".
- [x] Suíte Web completa 1175/1175; build PASS; lint sem erro novo.

## Potencial de Crescimento — Visualizações e Interpretação (MVP 1, Prompt 06)

Suítes: `tests/growthVisualization.test.mjs` (domínio visual puro) e
`tests/growthVisualizationPage.test.mjs` (verificação estática) no repo Web.

- [x] Delta chart: delta_pp real da API, linha de referência em 0 pp,
  rejeição não invertida (−10 pp permanece −10), null/suprimido sem barra
  (listados à parte), sem sort automático, sem Top N (scroll interno).
- [x] Scatter: X = participação (%), Y = delta_pp, zero line, sem limiar
  vertical de escala, sem quadrantes semânticos, pontos de raio constante.
- [x] Unidades: tooltips e legendas sempre com %, pp e ×; taxa e delta nunca
  no mesmo eixo sem distinção.
- [x] IC de Wilson exibido para segmento e referência com a nota de
  aproximação AAS; nunca "estatisticamente significativo"/"95% de certeza".
- [x] Templates determinísticos (oráculos: segunda opção 30%/15%/+15 pp;
  rejeição 12%/22%/−10 pp FAVORABLE sem "88% de aceitação" e sem "−10,0%");
  NEUTRAL, SMALL_BASE (cautela), suprimido sem direção, indisponível ≠ neutro.
- [x] Linguagem proibida ausente em todos os templates e componentes
  ("vai votar/converter", "chance de conversão", "votos potenciais",
  "significância", "melhor oportunidade" etc.); sem LLM.
- [x] Leitura do segmento em listas, sem contagem agregada de sinais e sem
  conclusão global de oportunidade.
- [x] Warnings visíveis (barra metodológica fixa; por segmento e por
  evidência; código desconhecido usa fallback da API).
- [x] Ordenação explícita com descrição "Ordenado por:"; default = ordem do
  motor; sem métrica "potential"; nulls por último.
- [x] Filtros de visualização puros: não chamam /analisar (uma única chamada
  na página), não mutam o resultado.
- [x] Seleção única de segmento entre tabela, barra, scatter e mapa.
- [x] Território: combinação completa de perfil obrigatória; NENHUMA
  agregação no Web (um finding por território, objeto original do motor);
  mapa de setor via contratos existentes com cor contínua centrada em 0;
  município adiado por geometria ausente; tabela como alternativa acessível;
  eleitorado apto só contexto (null nunca vira 0; cor nunca mistura
  eleitorado).
- [x] Sem score/ranking/projeção em toda a camada visual.
- [x] Suíte Web completa 1229/1229; build PASS; lint sem erro novo.

## Potencial de Crescimento — QA metodológico/estatístico/E2E (MVP 1, Prompt 07)

Artefatos de QA (promovidos a ferramenta permanente no Prompt 08):
`scripts/qa/growth_qa_seed.py` (dataset oráculo documentado) e
`scripts/qa/growth_qa_run.py` (112 verificações via HTTP real) —
`P360_QA_DATABASE_URL` obrigatória, sem default, apontando SEMPRE para banco
descartável; `tests/growthPotential.e2e.cdp.mjs` (E2E Web real, opt-in
`npm run qa:growth-e2e`, credenciais sintéticas via env) no repo Web. Ambiente:
PostgreSQL/PostGIS descartável (Docker), feature ativada só ali. Resultados
completos: doc/20.

- [x] Universo/elegibilidade: 20→14 conferido à mão (supporters, especiais
  por política, sem-resposta ≠ indeciso, conflito especial conservador).
- [x] Denominadores por sinal: rejeição múltipla conta ENTREVISTA (tripla=1,
  duplicada=1); decisão exclui não-classificados; participação usa
  eligible_n.
- [x] Wilson validado por implementação independente do QA (12 intervalos,
  tol 1e-9) + âncoras de literatura (5/10, 0/10, 10/10).
- [x] Zero real ≠ ausência (rate 0.0 vs null/SIGNAL_UNAVAILABLE); lift com
  referência zero → null + warning; espontânea com borda do limiar (igual
  não excede).
- [x] Território em PostGIS real: borda com ST_Covers=true/ST_Contains=false
  (SQL direto); sobreposição/sem-setor/sem-coordenada diagnosticados;
  município resolvido/não-resolvido; contexto eleitoral não altera taxas.
- [x] Determinismo: execuções idênticas exceto executed_at; fingerprint
  insensível a resposta não-analítica e sensível a analítica (reversível);
  cotas extremas não alteram o cálculo.
- [x] Gates E2E reais: inativa→404, ativa-sem-entitlement→403,
  concedida→200, RBAC 403, 404-antes-de-403 cross-tenant, mass assignment/
  malformado→422, SEGMENT_LIMIT→422 tipado.
- [x] E2E Web em browser real: fluxo completo com funil e números do oráculo
  na tela; rejeição crua; troca A→B fail-closed; viewports 1440/900; zero
  4xx/5xx.
- [x] Stress: 2.400 coletas / 100 segmentos ~260 ms (síncrono mantido).
- [x] Linguagem: varredura sem ocorrência indevida em produto e docs.
- [x] Correções de QA (Web): crash pré-existente da ProjectsPage (user
  órfão); deep link em rotas gated (idle fail-closed que expulsava
  licenciados); tipagem Recharts do delta chart.
- [x] Prompt 08: build Web com typecheck real (`tsc -b`) e os 39 erros TS
  pré-existentes (lista completa do comando canônico
  `npx tsc -b --pretty false`) quitados sem flexibilizar tsconfig (F4;
  ADR-074).

## Potencial de Crescimento — Hardening/Checkpoint (MVP 1, Prompt 08)

- [x] Typecheck canônico Web: `npm run typecheck` (`tsc -b --pretty false`)
  → 0 erros; `npm run build` executa `tsc -b` real.
- [x] Guardas estáticas de regressão (`tests/hardening.test.mjs`): F1
  (ProjectsPage define `user`), F2 (ModuleRoute/FeatureRoute aguardam
  idle/loading e negam error/ready-sem-acesso), F4 (build com `tsc -b`),
  higiene do E2E (credenciais só via env, sem senha embutida).
- [x] Suíte Web completa 1233/1233 (1229 baseline + 4 guardas); lint com os
  mesmos 10 erros preexistentes (zero novo).
- [x] Backend: `compileall` + suíte completa verde em Python 3.13
  (fixtures_qa restauradas após a rodada — só regeneram timestamp).
- [x] Feature `potencial_crescimento` confirmada `ativo=false` na migration
  `c6d7e8f9a0b1` (head único); nenhum entitlement real criado;
  `GROWTH_ENGINE_VERSION` = `1.0`.
- [x] Varreduras: sem `company_id` literal no código novo, sem
  score/ranking/projeção/causal fora de documentação de proibição, sem
  TODO/console.log/print de debug no código novo, `.env` não rastreado.
