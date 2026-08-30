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
client_uuid
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
- gerar `client_uuid` uma única vez e persistir antes do primeiro upload;
- reutilizar o mesmo `client_uuid` em todos os retries;
- atribuir e persistir um `client_uuid` antes de enviar coletas legadas pendentes;
- não enviar `company_id`;
- não confiar em `agente_id` para autorização;
- backend deve vincular `agente_id=current_user.id`;
- se falhar, manter coleta como pendente;
- se sucesso, salvar `server_id` e marcar sincronizada.
- o backend usa `company_id + client_uuid` para idempotência e não duplica respostas em reenvio.

## 8.1 Tentativas de campo (abordagens)

Tabela local `tentativas_campo` (Drift, schema 7). A tentativa nasce em
**INICIAR ABORDAGEM**, com o mesmo `GeoPoint` válido do gate de localização
(lat, lon, precisão, horário), como `EM_ANDAMENTO` — nunca ao abrir o app,
escolher pesquisa/setor ou ver o mapa.

Decisão após a abordagem:

- **Iniciar entrevista** → `FormScreen(initialLocation: <mesmo ponto>)`; ao
  salvar, a tentativa vira `CONCLUIDA` e guarda `coleta_client_uuid`. Nenhuma
  captura de GPS adicional entre tentativa e coleta.
- **Registrar recusa / Não elegível / Outro encerramento** → tentativa
  encerrada com `motivo` (código) e sem coleta.
- Sair do questionário com tentativa aberta pergunta o que houve:
  `DESISTENCIA` (entrevistado) ou `INCOMPLETA` (outro motivo). Respostas
  parciais não são guardadas nesta versão.
- Fechar a decisão sem escolher → "Descartar abordagem?" Descartar apaga a
  `EM_ANDAMENTO` (toque por engano); nenhuma desistência é inventada.

Sincronização:

```http
POST /pesquisas/{pesquisa_id}/tentativas-campo/
```

- `client_uuid` gerado na criação local e estável em todo retry;
- só tentativas encerradas (`pendente`) sobem; `EM_ANDAMENTO` nunca;
- **ordem**: coletas primeiro; uma tentativa com `coleta_client_uuid` só sobe
  depois de a coleta ter `server_id` (até lá fica pendente/ignorada no lote);
- sucesso → `sincronizado` + `server_id`; `401` interrompe o lote;
  `403/404/422` registram `last_sync_error` e mantêm pendente; rede/`5xx`
  mantêm pendente para o próximo lote;
- nunca enviar `company_id` nem `agente_id`;
- tentativas não entram em `realizado`/`restante`: só coleta concluída conta.

## 8.2 Cota territorial por setor (PROMPT 04)

O snapshot oficial vem no `dados_missao` (`realizado`, `status_cota`,
`limite_atencao_realizado`, `snapshot_ate_coleta_id`, `snapshot_em`,
`agentes_atribuidos_total`). O aparelho **não** recalcula o realizado global;
só soma as suas coletas locais do setor que ainda não estão no snapshot:

```text
realizado_efetivo = realizado_snapshot
                  + coletas locais do setor com status pendente
                  + coletas locais do setor com server_id > snapshot_ate_coleta_id
```

Exemplos: snapshot 98 até id 500 + 2 pendentes → 100 (ENCERRADO); 98/500 +
server_id 501 + 1 pendente → 100; novo snapshot 100/501 + server_id 501 →
100 (não conta de novo). Nunca usa `max(backend, local)` nem relógio do
aparelho. Tentativas (recusa, desistência…) não contam.

Status local (mesma regra do Backend, helper `lib/services/cota_territorial.dart`):
`SEM_COTA` se meta ≤ 0; `ENCERRADO` se efetivo ≥ meta; `ATENCAO` se efetivo ≥
limite (do snapshot, ou `ceil(meta×0,9)` em missão antiga); senão `ABERTO`.

Gate no Dashboard (antes do GPS operacional e da tentativa):

- `ENCERRADO` → botão INICIAR ABORDAGEM desabilitado com o motivo visível, setor
  rotulado "— Cota concluída" no seletor, e nova validação dentro do método
  (nenhuma captura, nenhuma tentativa, nenhuma coleta);
- `ATENCAO` → diálogo "Setor próximo da meta" com VOLTAR/CONTINUAR; quando
  `agentes_atribuidos_total > 1` informa "Há outros agentes vinculados a este
  setor" (vinculados ≠ coletando agora: não existe presença em tempo real);
- `ABERTO`/`SEM_COTA`/missão antiga/pesquisa sem setores → fluxo normal.

Após concluir uma entrevista local o Dashboard recarrega a missão e recalcula
sem sincronizar (98 + 2 locais = 100 bloqueia a terceira). Um novo
`dados_missao` substitui o snapshot e os extras são recalculados pelo corte.
O servidor nunca rejeita uma coleta por cota cheia. Sem migration Drift
(schema continua 7).

## 8.3 Cotas de perfil (PROMPT 05) — orientativas

O `dados_missao` traz `plano_cota_perfil_ativo`, `prioridades_perfil`,
`perfil_status_territorios` e `prioridades_perfil_snapshot_em`; cada setor
traz `municipio.id`. O Dashboard mostra o card **PRIORIDADE ATUAL** para o
município do setor selecionado, com no máximo 3 linhas
("Homem · 60+ — Déficit alto"), ALTO/MEDIO escondem BAIXO, maior déficit
primeiro. Sem prioridades: "Coleta em fase inicial" (`FASE_INICIAL`) ou
"Cotas equilibradas". Card ausente sem plano, sem município resolvido ou com
setor ENCERRADO (a trava territorial prevalece).

Regras: o app **não exibe** meta, realizado, restante, percentual nem desvio;
nada aqui desabilita botão, cria tentativa ou rejeita entrevista; não há
cálculo local nem extras — é o último snapshot do Backend, atualizado a cada
sincronização e lido offline do SQLite. Sem migration (schema 7).

## 8.4 Cobertura territorial no mapa (PROMPT 06)

Tabela Drift `cobertura_campo_cache` (schema 8, migration 7→8 aditiva) guarda o
**snapshot remoto** de `GET /agente/pesquisas/{id}/cobertura-campo/`
(unique `pesquisa_id + tipo + server_id`), substituído integralmente em uma
transação a cada sync bem-sucedido. Falha de download mantém o snapshot
anterior; a missão/coleta nunca dependem disso.

```text
cobertura_visível = cache_remoto
                  + coletas locais com GPS inicial e setor
                  + tentativas locais encerradas (não CONCLUIDA vinculada a coleta)
                  − duplicatas (chave tipo + server_id; local sem server_id usa o id local)
```
Uma entrevista concluída vira 1 ponto (a Coleta); evento local já sincronizado
e presente no cache não duplica. Eventos locais aparecem no mapa
imediatamente, sem novo download.

Mapa do Dashboard (flutter_map): polígono do setor + `CircleLayer` (raio =
distância recomendada, em metros: proximidade operacional, não área coberta)
+ `MarkerLayer` (coleta = ponto maior petróleo; abordagem = ponto laranja) +
minha posição, só do setor selecionado. Legenda: Coleta realizada · Abordagem
registrada · Área com atividade conhecida · Minha posição · "Atividade
conhecida até a última sincronização" (nunca "tempo real").

Aviso de proximidade, **depois** do GPS operacional e **antes** da tentativa:
se há evento do setor a ≤ distância recomendada (haversine, O(N)) →
"Área já visitada — Há atividade de campo registrada próxima deste ponto…"
com VOLTAR (nada é criado) / CONTINUAR (fluxo normal). Ordem dos gates:
cota ENCERRADO (bloqueia) → aviso ATENCAO → GPS → aviso de proximidade →
TentativaCampo. Nada da cobertura altera cota, perfil ou sync.

## 9. Geofence e setores

- Baixar cerca global e setores junto com missão.
- Alertar agente se GPS estiver fora da cerca.
- Enviar coleta normalmente se a regra não for bloqueante.
- Backend continua validando geofence na criação da coleta.

Estado atual: todos os setores existentes devem continuar sendo tratados como
operacionais para preservar o comportamento do Mobile.

Roadmap aprovado: quando o backend suportar finalidade territorial, o Mobile
deve baixar apenas setores `OPERACAO` ou `AMBOS` no contexto operacional.
Setores exclusivamente `RELATORIO` nao devem ser enviados ao app como missao,
setor operacional, meta/cota ou tolerancia/geofence operacional.

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

## Agente multiempresa (ADR-034)

O sync do agente (`GET /agente/pesquisas/`) passou a recortar pela **ACL**, não
pela empresa do cadastro. Um agente que atende dois clientes recebe as missões
dos dois em **um único login e um único sync** — sem trocar de empresa, sem novo
JWT.

Consequências para o app:

- **Não assumir que todas as pesquisas têm o mesmo `company_id`.** O app pode
  continuar guardando `company_id` no perfil por compatibilidade, mas cada
  Projeto/Pesquisa preserva a própria identidade de tenant. Filtrar as pesquisas
  locais pelo `company_id` do perfil esconderia as do segundo cliente.
- **Coleta offline multiempresa.** O tenant do dado é decidido pelo servidor
  (`Pesquisa → Projeto → Company`), nunca pelo perfil do agente: coleta feita no
  projeto da Empresa A sobe como Empresa A; a do projeto da Empresa B, como
  Empresa B — com o mesmo `agente_id`. O app não envia `company_id`.
- O mesmo vale para `TentativaCampo`.

### Acesso revogado com pendência local (ADR-034 §45)

Cenário: o agente coleta offline às 10h, o Superadmin revoga o acesso ao projeto
às 10h30, e o app tenta sincronizar às 11h.

Comportamento definido:

- o backend **recusa** o upload (404, pelo padrão de autorização — não se revela
  a existência do recurso);
- o app **mantém a pendência local** e mostra erro administrativo. A coleta
  **não** é descartada automaticamente: autorização é diferente de autoria, e
  perder dado legítimo do campo seria pior que manter uma pendência visível;
- não existe upload privilegiado após revogação. A resolução é administrativa:
  devolver o acesso (a pendência sobe no sync seguinte) ou tratar o caso
  manualmente.

Revogar acesso **nunca** apaga coletas, respostas, tentativas ou o histórico de
autoria já sincronizado.

## Setor → Município na missão (ADR-035-B)

O payload de `GET /agente/missao/{pesquisa_id}` já traz, por setor,
`municipio {id, nome}` e `territorio_status`, e as `prioridades_perfil` são
as do(s) município(s) dos setores do agente. Com a referência persistida no
Backend o valor passa a ser formal (não só derivado da composição). Nenhuma
mudança de contrato nem de schema Drift: o Mobile continua enviando
`setor_id` na coleta e o Backend resolve o Município.
