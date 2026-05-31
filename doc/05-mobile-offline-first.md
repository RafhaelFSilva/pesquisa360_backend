# Pesquisa360 — Mobile Offline-First

**Status:** diretriz para revisão do app Flutter  
**Escopo:** autenticação, sincronização, coletas, GPS e multitenancy

## 1. Papel do Mobile

O aplicativo Flutter é usado pelo agente para:

- autenticar;
- baixar pesquisas atribuídas;
- responder formulários;
- coletar GPS de início/fim;
- operar sem internet;
- armazenar coletas localmente;
- sincronizar coletas com o backend.

## 2. Princípio offline-first

```text
Download prévio -> Coleta offline -> Armazenamento local -> Sincronização posterior
```

A leitura operacional deve acontecer do banco local. A API é usada em momentos explícitos: login, download/sync de missões e upload de coletas pendentes.

## 3. Stack

- Flutter / Dart
- Dio
- Drift / SQLite
- BLoC / Cubit
- flutter_secure_storage
- get_it
- geolocator

## 4. Autenticação

Fluxo correto:

```text
1. POST /login/token
2. Salvar token no secure storage
3. GET /usuarios/me/
4. Salvar perfil local: id, nome, email, perfil_id, company_id
```

Proibido:

- `agente_id=1`;
- `company_id=1`;
- usuário fixo;
- upload sem token válido;
- dados locais vazando entre usuários.

## 5. Banco local

Entidades mínimas:

```text
usuarios_local
projetos_local
pesquisas_local
perguntas_local
opcoes_local
coletas_local
respostas_local
sync_queue
```

Campos críticos em coletas locais:

```text
id_local
server_id
pesquisa_id
agente_id
company_id
data_inicio_coleta
data_fim_coleta
localizacao_inicio_lat
localizacao_inicio_lng
localizacao_fim_lat
localizacao_fim_lng
foi_offline
status_sincronizacao
sync_attempts
last_sync_error
```

## 6. Download de pesquisas

Endpoint:

```http
GET /agente/pesquisas/
```

Regras:

- baixar somente dados ativos;
- persistir perguntas e opções localmente;
- atualizar estruturas sem apagar coletas pendentes;
- controlar data da última sincronização.

## 7. Coleta offline

Ao iniciar:

- capturar horário;
- capturar GPS;
- marcar se havia internet.

```text
foi_offline = !temConexaoNoMomentoDaColeta
```

Ao finalizar:

- capturar horário final;
- capturar GPS final;
- validar perguntas obrigatórias;
- salvar respostas localmente;
- inserir coleta na fila de sincronização.

## 8. Upload de coletas

Endpoint:

```http
POST /pesquisas/{pesquisa_id}/coletas/
```

Regras:

- enviar com token JWT;
- não enviar `company_id`;
- não confiar em `agente_id` para autorização;
- backend deve vincular `agente_id=current_user.id`;
- se falhar, manter coleta como pendente;
- se sucesso, salvar `server_id` e marcar sincronizada.

## 9. Geofence e setores

- Baixar cerca global e setores junto com missão.
- Alertar agente se GPS estiver fora da cerca.
- Enviar coleta normalmente se a regra não for bloqueante.
- Backend continua validando geofence na criação da coleta.

## 10. Troca de usuário

Ao fazer logout:

```text
1. parar sincronização em andamento
2. remover token
3. remover perfil local
4. limpar cache sensível
5. impedir upload pendente sem usuário
```

Para MVP, preferir limpar banco local ao trocar usuário.

## 11. Monitoramento em tempo real

O monitoramento atual é baseado em coletas sincronizadas. A funcionalidade “estilo Uber” exige outro fluxo:

```text
mobile envia heartbeat de localização
backend armazena última posição
web consulta periodicamente
admin controla ligado/desligado
```

## 12. Checklist mobile

- Login.
- `/usuarios/me/`.
- Download de pesquisas.
- Abrir formulário offline.
- Preencher coleta offline.
- Capturar GPS início/fim.
- Sincronizar.
- Coleta aparece no Web.
- Agente correto.
- `foi_offline=true` quando sem internet.
- Logout limpa dados sensíveis.
