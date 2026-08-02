# Pesquisa360 — Plano Priorizado de Implementação e Correção

**Documento:** Plano de execução para Codex  
**Projeto:** Pesquisa360  
**Data-base:** 02/08/2026  
**Status:** A iniciar  
**Fonte da verdade:** código atual dos três workspaces e documentação técnica existente

## 1. Objetivo

Concluir e estabilizar o ciclo operacional completo do Pesquisa360:

```text
Superadmin
  -> cadastra Tenant
  -> cadastra Gerente
  -> Gerente administra Agentes
  -> Tenant cadastra Projeto e Pesquisa
  -> configura perguntas, geofence e setores
  -> Agente baixa a pesquisa no Mobile
  -> realiza coleta offline ou online
  -> sincroniza dados e imagens
  -> coleta aparece no Monitoramento
  -> respostas aparecem nos Relatórios
```

O trabalho deve priorizar segurança, isolamento multitenant, integridade dos dados, sincronização idempotente e compatibilidade entre Backend, Web e Mobile.

---

## 2. Workspaces

```text
Backend:
C:\Dev\pesquisa360_backend

Frontend Web:
C:\Dev\pesquisa360-web

Mobile:
C:\Dev\pesquisa360_app
```

Branches observadas na inspeção:

```text
Backend:
feature/backend-contract-multitenancy

Web:
feature/web-multitenancy-integration

Mobile:
feature/mobile-multitenancy-integration
```

Antes de qualquer alteração, confirmar o estado local real de cada workspace.

---

## 3. Regras obrigatórias para o Codex

1. Trabalhar em apenas um workspace por patch.
2. Não misturar Backend, Web e Mobile no mesmo patch.
3. Não fazer refatoração ampla fora do escopo.
4. Não alterar contratos públicos sem documentar impacto.
5. Não remover compatibilidade existente sem justificativa.
6. Não executar reset destrutivo do banco.
7. Não apagar migrations existentes.
8. Não alterar dados de produção.
9. Não criar credenciais, senhas ou tokens fixos.
10. Não deixar URL, IP, segredo ou chave diretamente no código.
11. Não confiar em `company_id`, `agente_id`, perfil ou tenant enviados pelo cliente quando puderem ser derivados do usuário autenticado.
12. Toda operação multitenant deve validar o recurso pelo tenant antes de ler ou alterar.
13. Cada patch deve terminar com:
    - arquivos alterados;
    - diagnóstico;
    - implementação realizada;
    - testes executados;
    - testes pendentes;
    - riscos;
    - `git status --short`;
    - `git diff --check`;
    - sugestão objetiva do próximo patch.
14. Não criar commit, push, merge, rebase ou PR sem solicitação explícita.
15. Se um bloqueio impedir a implementação segura, parar e relatar o bloqueio sem improvisar.

---

## 4. Estratégia de execução

A ordem obrigatória é:

```text
FASE 0 — Baseline dos três workspaces
FASE 1 — Backend P0: segurança e autorização
FASE 2 — Backend P0: upload e idempotência
FASE 3 — Backend P1: contratos e consistência
FASE 4 — Web: Tenants e gerenciamento de Agentes
FASE 5 — Mobile: configuração, upload e sincronização
FASE 6 — Testes automatizados e validação multitenant
FASE 7 — Tenant Demo e pesquisa de teste
FASE 8 — Homologação ponta a ponta
FASE 9 — Release e documentação final
```

Nenhuma fase deve avançar se os critérios de saída da fase anterior não forem atendidos.

---

# FASE 0 — Baseline dos três workspaces

## Objetivo

Registrar o estado real dos repositórios, identificar alterações locais e confirmar se as branches locais correspondem ao código inspecionado.

## Workspace 1 — Backend

```powershell
cd C:\Dev\pesquisa360_backend
git status --short
git branch --show-current
git log -1 --oneline
git diff --check
python -m compileall pesquisa360
```

Também inspecionar:

```text
alembic current
alembic heads
alembic history
```

Não executar `alembic upgrade head` antes de validar a cadeia de migrations.

## Workspace 2 — Web

```powershell
cd C:\Dev\pesquisa360-web
git status --short
git branch --show-current
git log -1 --oneline
git diff --check
npm run lint
npm run build
```

## Workspace 3 — Mobile

```powershell
cd C:\Dev\pesquisa360_app
git status --short
git branch --show-current
git log -1 --oneline
git diff --check
flutter analyze
flutter test
```

## Entrega da Fase 0

Gerar relatório contendo:

- branch atual de cada workspace;
- último commit;
- arquivos modificados;
- resultado dos builds;
- resultado dos testes;
- erros existentes;
- divergências entre documentação e código;
- confirmação de que não houve alteração.

## Critério de saída

- Estado dos três workspaces conhecido.
- Nenhuma alteração aplicada.
- Bloqueios iniciais documentados.
- Ordem dos patches confirmada.

---

# FASE 1 — Backend P0: segurança e autorização

**Workspace:**

```text
C:\Dev\pesquisa360_backend
```

## PATCH-BE-P0-001 — Remover segredo JWT do código

### Problema

A chave de assinatura JWT está definida diretamente no código.

### Objetivo

Carregar `SECRET_KEY` exclusivamente por variável de ambiente.

### Implementação esperada

- Ler `SECRET_KEY` do ambiente.
- Falhar na inicialização se estiver ausente.
- Manter `ALGORITHM` configurável ou explicitamente documentado.
- Atualizar `.env.example` sem inserir segredo real.
- Garantir que nenhuma chave real permaneça no Git.
- Documentar que a chave atual precisa ser rotacionada.

### Critérios de aceite

- Não existe chave JWT fixa no código.
- A aplicação falha com mensagem segura se `SECRET_KEY` não estiver configurada.
- Login e validação de token continuam funcionando quando a variável está configurada.
- `.env` permanece ignorado pelo Git.

### Validação

```powershell
python -m compileall pesquisa360
```

Adicionar testes unitários de configuração, se a estrutura permitir.

---

## PATCH-BE-P0-002 — Corrigir CORS por ambiente

### Problema

A API aceita origem `"*"` e mantém origem de produção comentada.

### Objetivo

Configurar origens por variável de ambiente.

### Implementação esperada

Exemplo:

```text
CORS_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

Regras:

- Não combinar credenciais com origem global.
- Converter a variável em lista.
- Permitir configuração distinta para desenvolvimento, homologação e produção.
- Atualizar `.env.example`.

### Critérios de aceite

- `"*"` não é usado em produção.
- Web local continua funcionando.
- Origem não autorizada é bloqueada.
- A configuração está documentada.

---

## PATCH-BE-P0-003 — Corrigir brecha multitenant na edição de perguntas

### Problema

O endpoint valida acesso à pesquisa, mas o CRUD atualiza a pergunta apenas pelo `pergunta_id`.

### Objetivo

Garantir que toda alteração de pergunta valide simultaneamente:

```text
pergunta.id
pergunta.pesquisa_id
projeto.company_id
```

### Escopo mínimo

Revisar:

- atualização;
- exclusão;
- leitura individual, se existir;
- reordenação;
- atualização de opções;
- referência `proxima_pergunta_id`.

### Regra

Nunca carregar uma pergunta apenas pelo ID em uma operação autenticada quando o contexto da pesquisa estiver disponível.

### Critérios de aceite

- Tenant A não altera pergunta do Tenant B.
- Pergunta de outra pesquisa do mesmo tenant também não pode ser alterada por URL incorreta.
- Retorno deve ser `404` para recurso inexistente ou inacessível.
- Teste automatizado comprova o isolamento.

---

## PATCH-BE-P0-004 — Corrigir autorização da gestão de Agentes

### Problema

A tela da equipe utiliza `/usuarios/`, mas a criação exige Superadmin.

### Decisão funcional

O Gerente poderá administrar apenas usuários da própria empresa.

### Permissões esperadas

#### Superadmin

- Criar Tenant.
- Editar Tenant.
- Criar Gerente.
- Criar Agente.
- Mover usuário entre tenants, quando explicitamente permitido.
- Ativar e desativar qualquer usuário.
- Criar outro Superadmin apenas por endpoint administrativo.

#### Gerente

- Listar usuários da própria empresa.
- Criar Agente na própria empresa.
- Editar Agente da própria empresa.
- Ativar ou desativar Agente da própria empresa.
- Redefinir senha de Agente da própria empresa.
- Não criar Superadmin.
- Não mover usuário para outro tenant.
- Não acessar usuários de outro tenant.

#### Agente

- Não administrar usuários.

### Implementação esperada

Criar dependências de autorização explícitas, por exemplo:

```text
require_superadmin
require_manager_or_superadmin
require_same_company
```

Não depender de IDs fixos de perfil.

### Critérios de aceite

- Gerente cria Agente da própria empresa.
- Gerente não cria Gerente ou Superadmin, salvo regra explicitamente aprovada.
- Gerente não acessa Agente de outro tenant.
- Agente recebe `403`.
- Superadmin mantém acesso global.

---

## PATCH-BE-P0-005 — Bloquear usuário e empresa inativos

### Problema

A empresa possui `is_active`, mas essa condição não é aplicada integralmente no login e nas rotas autenticadas.

### Objetivo

Validar:

```text
usuario.ativo = true
empresa.is_active = true
```

### Critérios de aceite

- Usuário inativo não recebe token.
- Usuário de empresa inativa não recebe token.
- Token antigo deixa de funcionar após usuário ou empresa serem inativados.
- Mensagem de erro não expõe detalhes sensíveis.
- Testes cobrem os quatro estados:
  - usuário ativo / empresa ativa;
  - usuário inativo / empresa ativa;
  - usuário ativo / empresa inativa;
  - usuário inativo / empresa inativa.

---

## PATCH-BE-P0-006 — Validar coordenador do projeto

### Problema

A criação do projeto recebe `coordenador_id`, mas não confirma que o coordenador pertence à empresa do usuário.

### Objetivo

Impedir vínculo cruzado entre tenants.

### Critérios de aceite

- Coordenador deve existir.
- Coordenador deve estar ativo.
- Coordenador deve pertencer ao mesmo tenant.
- Perfil do coordenador deve ser compatível com a regra de negócio.
- Tenant A não cria projeto com coordenador do Tenant B.

---

# FASE 2 — Backend P0: upload e idempotência

## PATCH-BE-P0-007 — Proteger upload de arquivos

### Problema

O endpoint `/upload` é público e aceita arquivos arbitrários.

### Objetivo

Transformar upload em operação autenticada e controlada.

### Implementação mínima

- Exigir JWT.
- Validar empresa ativa.
- Limitar tamanho.
- Permitir somente formatos definidos.
- Validar MIME real.
- Não confiar na extensão enviada.
- Gerar nome seguro.
- Associar arquivo ao tenant.
- Sanitizar erros.
- Remover uploads de teste versionados, preservando somente arquivos realmente necessários.
- Não permitir caminho relativo manipulável.
- Usar configuração de diretório por ambiente.

### Formatos iniciais permitidos

```text
image/jpeg
image/png
image/webp
```

### Critérios de aceite

- Requisição sem token retorna `401`.
- Arquivo inválido retorna `415` ou `422`.
- Arquivo acima do limite é rejeitado.
- Arquivo válido retorna identificador ou URL controlada.
- Tenant não acessa arquivo privado de outro tenant, caso a mídia seja privada.
- Testes cobrem autenticação, formato e tamanho.

---

## PATCH-BE-P0-008 — Idempotência de coleta

### Problema

Uma coleta pode ser duplicada se o Backend aceitar a requisição e o Mobile não registrar o retorno.

### Objetivo

Adicionar um identificador imutável criado no dispositivo.

### Contrato esperado

Mobile envia:

```json
{
  "client_uuid": "UUID",
  "data_inicio_coleta": "...",
  "data_fim_coleta": "...",
  "localizacao_inicio": {},
  "localizacao_fim": {},
  "foi_offline": true,
  "respostas": []
}
```

Backend deve possuir restrição equivalente a:

```text
UNIQUE(company_id, client_uuid)
```

Se a mesma coleta for reenviada:

- não duplicar;
- retornar a coleta existente;
- manter resposta compatível com o Mobile.

### Critérios de aceite

- Primeiro envio cria uma coleta.
- Segundo envio com o mesmo UUID não cria duplicidade.
- UUID igual em tenants diferentes não causa conflito indevido.
- Teste de concorrência ou reenvio rápido não cria duas coletas.
- Migration possui downgrade coerente ou limitação documentada.

---

## PATCH-BE-P0-009 — Tornar criação de coleta transacional

### Objetivo

Persistir coleta e respostas em uma única transação.

### Critérios de aceite

- Falha em qualquer resposta desfaz a coleta inteira.
- Pergunta deve pertencer à pesquisa da coleta.
- Pergunta inativa não deve aceitar nova resposta.
- Respostas obrigatórias devem ser validadas conforme regra definida.
- Não aceitar `agente_id` ou `company_id` do payload.
- `agente_id` vem do token.
- Tenant vem do usuário autenticado.

---

# FASE 3 — Backend P1: contratos e consistência

## PATCH-BE-P1-001 — Corrigir campos inconsistentes de Coleta

### Problema

Existem referências a:

```text
data_inicio
data_fim
```

enquanto o modelo usa:

```text
data_inicio_coleta
data_fim_coleta
```

### Objetivo

Eliminar referências a atributos inexistentes e consolidar o contrato.

### Critérios de aceite

- Todos os endpoints de coletas funcionam.
- Nenhum `AttributeError`.
- Campos retornados são consistentes.
- Monitoramento e relatórios continuam compatíveis.

---

## PATCH-BE-P1-002 — Consolidar `lat/lon/lng`

### Regra proposta

```text
Entrada Mobile:
lat / lon

Saída Web:
lat / lng

GeoJSON:
[longitude, latitude]
```

### Objetivo

Documentar e aplicar conversões em uma única camada.

### Critérios de aceite

- Nenhum endpoint alterna nomes sem documentação.
- Web e Mobile mantêm compatibilidade.
- Testes cobrem serialização e desserialização.
- Não retornar objetos PostGIS crus.

---

## PATCH-BE-P1-003 — Tornar criação de pergunta e opções atômica

### Problema

Pergunta e opções podem ser confirmadas em commits separados.

### Objetivo

Usar uma única transação.

### Critérios de aceite

- Falha em uma opção desfaz pergunta e demais opções.
- Atualização de opções também é transacional.
- `proxima_pergunta_id` deve apontar para pergunta da mesma pesquisa.
- Não permitir ciclos inválidos, caso a regra já exista.

---

## PATCH-BE-P1-004 — Endpoint de perfis

### Objetivo

Eliminar IDs fixos de perfil no Frontend.

### Endpoint sugerido

```text
GET /perfis/
```

### Contrato sugerido

```json
[
  {
    "id": 1,
    "code": "AGENT",
    "nome": "Agente"
  }
]
```

### Critérios de aceite

- Web não depende de `1`, `2` ou `3`.
- Regras de autorização usam código ou nome canônico.
- Perfis internos não autorizados podem ser omitidos conforme o usuário autenticado.

---

## PATCH-BE-P1-005 — Validação de Tenant

### Escopo

- CNPJ normalizado.
- CNPJ único quando informado.
- Nome obrigatório.
- Definir comportamento de inativação.
- Impedir exclusão física de tenant com dados.
- Definir se logo será URL ou upload.

### Critérios de aceite

- CNPJ duplicado é rejeitado.
- Tenant inativo preserva histórico.
- Nenhuma exclusão em cascata destrutiva é adicionada.

---

## PATCH-BE-P1-006 — Revisar migrations

### Objetivo

Validar a cadeia completa em:

1. banco vazio;
2. banco existente antes da multitenancy;
3. banco já atualizado.

### Regras

- Não editar migration já aplicada em produção sem justificativa.
- Criar nova migration corretiva quando necessário.
- Não usar reset como solução.
- Revisar migration com grande remoção ou alteração suspeita.

### Critérios de aceite

```text
alembic upgrade head
alembic downgrade <revisao_anterior>
alembic upgrade head
```

Quando downgrade não for seguro, documentar explicitamente.

---

# FASE 4 — Frontend Web

**Workspace:**

```text
C:\Dev\pesquisa360-web
```

## PATCH-WEB-P0-001 — Configuração da API por ambiente

### Objetivo

Remover URL fixa.

### Implementação esperada

```typescript
const baseURL = import.meta.env.VITE_API_URL;
```

Adicionar:

```text
.env.example
```

Sem credenciais reais.

### Critérios de aceite

- Desenvolvimento usa URL local configurável.
- Homologação e produção não exigem alteração no código.
- Build falha ou exibe erro claro quando a configuração está ausente.

---

## PATCH-WEB-P0-002 — Finalizar gerenciamento de Tenants

### Tela existente

```text
AdminCompaniesPage
```

### Ajustes

- Máscara e validação de CNPJ.
- Busca.
- Paginação ou estratégia para listas maiores.
- Confirmação antes de inativar.
- Status correto.
- Responsividade.
- Corrigir container para permitir rolagem horizontal controlada.
- Exibir feedback sem `alert`.
- Exibir quantidade de usuários e projetos somente se o Backend oferecer dados seguros.

### Critérios de aceite

- Criar Tenant.
- Editar Tenant.
- Inativar e reativar.
- CNPJ inválido é bloqueado.
- CNPJ duplicado exibe mensagem do Backend.
- Tela funciona em largura reduzida.

---

## PATCH-WEB-P0-003 — Remover perfis fixos

### Problema

A tela usa IDs fixos para Agente, Gerente e Superadmin.

### Objetivo

Consumir `/perfis/`.

### Critérios de aceite

- Nenhum ID de perfil de banco fica hardcoded.
- Opções exibidas respeitam a permissão do usuário autenticado.
- Superadmin pode selecionar perfis globais.
- Gerente vê apenas os perfis permitidos.

---

## PATCH-WEB-P0-004 — Finalizar gerenciamento de Agentes

### Tela alvo

```text
UsersPage
```

### Funcionalidades obrigatórias

- Listar membros do tenant autenticado.
- Filtrar por nome ou e-mail.
- Criar Agente.
- Editar nome.
- Ativar ou desativar.
- Redefinir senha.
- Exibir perfil correto.
- Exibir status real.
- Estado de carregamento.
- Estado de submissão.
- Tratamento de `401`, `403`, `404`, `409` e `422`.
- Confirmação antes de inativar.
- Não exibir campo Tenant.
- Não permitir criar Superadmin.
- Não usar `alert`.

### Critérios de aceite

- Gerente administra Agente da própria empresa.
- Tenant não acessa usuário de outro tenant.
- Status exibido corresponde ao Backend.
- Edição reflete imediatamente na lista.
- Erros são exibidos na própria tela.

---

## PATCH-WEB-P1-001 — Revisar autenticação e rotas

### Objetivo

Garantir que:

- login chama `/login/token`;
- depois chama `/usuarios/me/`;
- Superadmin é redirecionado para `/admin`;
- Gerente é redirecionado para `/projetos`;
- Agente não acessa telas administrativas;
- sessão inválida é limpa;
- empresa inativa encerra acesso.

### Critérios de aceite

- Sem fallback de usuário fake.
- Sem `company_id` hardcoded.
- Rotas privadas protegidas.
- Nenhum loop de redirecionamento.

---

## PATCH-WEB-P1-002 — Testes mínimos do Web

Adicionar estrutura de testes para:

- autenticação;
- rota Superadmin;
- tela de Tenant;
- tela de Agentes;
- mensagens de erro;
- permissões.

Preferir testes focados, sem tentar cobrir toda a aplicação no primeiro patch.

---

# FASE 5 — Mobile

**Workspace:**

```text
C:\Dev\pesquisa360_app
```

## PATCH-MOB-P0-001 — API por ambiente

### Objetivo

Remover IP fixo da API e do upload.

### Estratégia recomendada

```text
--dart-define=API_BASE_URL=http://...
```

ou flavors:

```text
development
staging
production
```

### Critérios de aceite

- Nenhum IP local permanece no código de produção.
- API e upload usam a mesma base configurada.
- Configuração ausente produz erro claro.

---

## PATCH-MOB-P0-002 — Upload autenticado pelo cliente principal

### Problema

O upload utiliza `Dio()` separado.

### Objetivo

Usar o mesmo `DioClient` autenticado.

### Critérios de aceite

- JWT é enviado.
- URL vem da configuração.
- Interceptors e tratamento de erro são compartilhados.
- Upload não cria cliente HTTP paralelo.
- Tenant e usuário vêm do token.

---

## PATCH-MOB-P0-003 — Bloquear sincronização quando mídia falhar

### Regra

Uma coleta com imagem obrigatória não pode ser marcada como sincronizada se o upload da imagem falhar.

### Critérios de aceite

- Falha de upload mantém a coleta pendente.
- `syncAttempts` é incrementado.
- `lastSyncError` registra mensagem sanitizada.
- Caminho local nunca é enviado ao Backend como resposta final.
- Nova tentativa reutiliza upload concluído quando possível.

---

## PATCH-MOB-P0-004 — Adicionar `client_uuid`

### Objetivo

Gerar UUID na criação local da coleta.

### Critérios de aceite

- UUID é criado uma única vez.
- UUID permanece o mesmo em todas as tentativas.
- UUID é enviado ao Backend.
- Resposta do Backend atualiza `serverId`.
- Reenvio não duplica a coleta.

---

## PATCH-MOB-P0-005 — Filtrar dados locais pelo usuário atual

### Escopo

Revisar:

- histórico de coletas;
- contagens;
- listas;
- cache de projetos;
- pesquisas;
- perguntas;
- respostas;
- missões.

### Regra

Toda leitura operacional deve considerar, conforme aplicável:

```text
company_id
agente_id
downloaded_by_user_id
```

### Critérios de aceite

- Usuário A não vê dados do Usuário B no mesmo dispositivo.
- Pendências de outro usuário são preservadas, mas não exibidas nem enviadas.
- Troca de usuário sem pendências limpa cache operacional.
- Logout com pendências preserva somente o necessário.

---

## PATCH-MOB-P1-001 — Remover HTTP de dentro da transação Drift

### Problema

A sincronização consulta missões enquanto a transação local está aberta.

### Fluxo esperado

```text
baixar dados
baixar missões
validar payload
abrir transação
persistir snapshot
fechar transação
```

### Critérios de aceite

- Nenhuma chamada HTTP ocorre dentro da transação.
- Falha de rede não deixa transação aberta.
- Persistência permanece atômica.

---

## PATCH-MOB-P1-002 — Remover dados obsoletos no sync

### Objetivo

Refletir o snapshot atual do Backend.

### Critérios de aceite

- Pesquisa inativada deixa de aparecer.
- Pergunta removida ou inativada deixa de aparecer.
- Projeto removido do escopo deixa de aparecer.
- Coletas pendentes e respostas locais nunca são apagadas indevidamente.

---

## PATCH-MOB-P1-003 — Validar sessão contra o Backend

### Objetivo

Ao restaurar sessão e houver conexão:

```text
GET /usuarios/me/
```

### Critérios de aceite

- Token expirado limpa sessão.
- Usuário inativo limpa sessão.
- Empresa inativa limpa sessão.
- Mudança de perfil é atualizada.
- Sem conexão, comportamento offline é previsível e documentado.

---

## PATCH-MOB-P1-004 — Organizar testes

### Problema

Grande parte dos testes está concentrada em um único arquivo.

### Objetivo

Separar por domínio:

```text
test/auth/
test/database/
test/sync/
test/form/
test/widgets/
```

### Critérios de aceite

- Nenhum comportamento é perdido.
- Testes continuam passando.
- Suítes podem ser executadas isoladamente.

---

# FASE 6 — Testes automatizados e matriz multitenant

## Backend

Criar testes para:

- login válido;
- login inválido;
- usuário inativo;
- empresa inativa;
- `/usuarios/me/`;
- Superadmin;
- Gerente;
- Agente;
- criação de Tenant;
- criação de Agente pelo Gerente;
- acesso cruzado entre tenants;
- alteração cruzada de pergunta;
- upload sem token;
- upload inválido;
- criação transacional de coleta;
- idempotência de coleta;
- relatórios e monitoramento básicos.

## Web

Testar:

- rota protegida;
- rota Superadmin;
- cadastro de Tenant;
- edição de Tenant;
- cadastro de Agente;
- edição de Agente;
- erros de autorização;
- carregamento de perfis.

## Mobile

Testar:

- login e `/usuarios/me/`;
- troca de usuário;
- pendências de outro agente;
- isolamento por tenant;
- geração de `client_uuid`;
- falha de upload;
- reenvio idempotente;
- sincronização de tipos de perguntas;
- remoção de snapshot obsoleto.

## Matriz obrigatória

| Operação | Tenant A | Tenant B | Resultado esperado |
|---|---:|---:|---|
| Listar projetos A | Sim | Não | B não vê |
| Abrir pesquisa A por URL | Sim | Não | B recebe 404 |
| Alterar pergunta A | Sim | Não | B recebe 404 |
| Ver relatório A | Sim | Não | B recebe 404 |
| Ver monitoramento A | Sim | Não | B recebe 404 |
| Editar agente A | Gerente A | Gerente B | B recebe 404 |
| Sincronizar coleta A | Agente A | Agente B | B não envia como A |
| Reusar UUID no mesmo tenant | Sim | — | Não duplica |
| Reusar UUID em outro tenant | — | Sim | Sem conflito cruzado |

---

# FASE 7 — Tenant Demo e pesquisa de teste

## Pré-condições

- Patches P0 concluídos.
- Builds aprovados.
- Migrations aprovadas.
- Matriz multitenant básica aprovada.
- Arquivo da pesquisa de teste disponível.

## Dados mínimos

Criar ou confirmar:

```text
Tenant:
Tenant Demo

Usuários:
1 Gerente Demo
2 Agentes Demo
```

## Fluxo

1. Criar Tenant Demo.
2. Criar Gerente Demo.
3. Entrar como Gerente.
4. Criar Agentes.
5. Criar Projeto Demo.
6. Cadastrar ou importar Pesquisa Demo.
7. Cadastrar perguntas.
8. Configurar geofence.
9. Criar setores.
10. Vincular setores aos agentes.
11. Sincronizar pesquisas no Mobile.
12. Realizar coleta offline.
13. Realizar coleta online.
14. Realizar coleta com foto.
15. Sincronizar.
16. Confirmar monitoramento.
17. Confirmar relatórios.
18. Testar tentativa de acesso por outro tenant.

## Tipos mínimos de pergunta

```text
TEXTO
TEXTO_LONGO
NUMERO
DATA
ESCOLHA_SIMPLES
MULTIPLA_ESCOLHA
ESCALA
IMAGEM
```

## Critérios de aceite

- Todos os tipos renderizam.
- Obrigatoriedade funciona.
- Opções funcionam.
- Pulos condicionais, se utilizados, funcionam.
- Foto é enviada.
- GPS inicial e final são registrados.
- Offline é identificado.
- Agente correto aparece no monitoramento.
- Respostas aparecem no relatório.

---

# FASE 8 — Homologação ponta a ponta

## Cenário A — Coleta online

```text
Login
-> baixar pesquisa
-> abrir formulário
-> responder
-> capturar GPS
-> enviar
-> monitoramento
-> relatório
```

## Cenário B — Coleta offline

```text
Login e sync inicial online
-> perder conexão
-> responder pesquisa
-> salvar pendente
-> fechar e reabrir app
-> recuperar pendência
-> restaurar conexão
-> sincronizar
-> validar monitoramento e relatório
```

## Cenário C — Falha de upload de foto

```text
coleta com imagem
-> falhar upload
-> permanecer pendente
-> nova tentativa
-> upload concluído
-> coleta enviada uma única vez
```

## Cenário D — Troca de usuário

```text
Agente A possui pendência
-> logout
-> login Agente B
-> operação bloqueada ou isolada conforme regra
-> Agente B não vê nem envia coleta de A
```

## Cenário E — Tenant inativo

```text
Superadmin inativa Tenant
-> usuários não conseguem novo login
-> tokens existentes deixam de acessar API
-> histórico permanece preservado
```

---

# FASE 9 — Release

## Checklist técnico

### Backend

```powershell
python -m compileall pesquisa360
pytest
alembic current
alembic heads
```

### Web

```powershell
npm run lint
npm run build
```

### Mobile

```powershell
flutter analyze
flutter test
```

## Checklist Git

```powershell
git status --short
git diff --check
git log -1 --oneline
```

## Entrega

- Documentação atualizada.
- `.env.example` atualizado.
- Nenhum segredo versionado.
- Nenhuma URL local fixa.
- Nenhum arquivo temporário.
- Nenhum upload de teste indevido.
- Migrations revisadas.
- Tags equivalentes nos três repositórios.
- Registro da versão homologada.
- Plano de rollback documentado.

---

# 10. Definição de pronto

O Pesquisa360 será considerado pronto para esta etapa quando:

- Cadastro e gerenciamento de Tenant estiverem concluídos.
- Superadmin cadastrar Gerentes.
- Gerente cadastrar e gerenciar Agentes do próprio tenant.
- Nenhuma operação cruzar tenants.
- Tenant e usuário inativos forem bloqueados.
- Upload exigir autenticação e validação.
- Mobile não enviar caminho local como mídia.
- Coleta possuir `client_uuid`.
- Reenvio não gerar duplicidade.
- Mobile operar offline e sincronizar depois.
- Pesquisa Demo funcionar com todos os tipos obrigatórios.
- Coletas aparecerem no monitoramento.
- Respostas aparecerem nos relatórios.
- Builds e testes dos três workspaces estiverem aprovados.
- A matriz Tenant A x Tenant B estiver aprovada.
- O ciclo completo estiver documentado e reproduzível.

---

# 11. Próximo patch obrigatório

Iniciar por:

```text
PATCH-BE-P0-001 — Remover segredo JWT do código
```

Depois seguir estritamente a ordem deste documento.

Não iniciar alterações no Web ou Mobile antes de concluir os patches críticos de segurança e autorização do Backend.
