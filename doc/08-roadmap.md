# Pesquisa360 — Roadmap Técnico e Produto

**Status:** roadmap pós-estabilização das funcionalidades existentes  
**Objetivo:** orientar próximas implementações sem quebrar o produto validado.

## 1. Marco atual

Funcionalidades validadas:

- login;
- multitenancy backend;
- CRUD de projeto;
- CRUD de pesquisa;
- CRUD de pergunta;
- soft delete;
- cerca global;
- setores e agentes;
- relatórios simples;
- crosstab 2D;
- monitoramento de coletas;
- online/offline;
- endereço estimado;
- layout operacional de monitoramento.

## 2. Princípio de evolução

Toda nova feature deve seguir:

```text
Plan -> Do -> Check -> Act
```

E passar por:

```text
contrato backend
service frontend
tela frontend
teste manual
checkpoint git
documentação
```

## 3. Prioridade alta

### Mobile multitenancy/sync

- login + `/usuarios/me/`;
- remover `agente_id` hardcoded;
- salvar perfil local;
- limpar dados ao trocar usuário;
- upload de coleta com usuário real;
- validar offline/online;
- validar geofence no upload;
- testar coleta aparecendo no monitoramento.

### Testes mínimos Backend

- login;
- `/usuarios/me/`;
- isolamento de projetos por tenant;
- criação de coleta com agente autenticado;
- relatório simples;
- crosstab;
- monitoramento.

### Contratos geoespaciais

Consolidar:

```text
lat/lng para Web e leituras
compatibilidade com lon no POST legado
GeoJSON apenas quando explicitamente documentado
```

### Geocoding controlado

- Não chamar Nominatim em GETs de tela.
- Preencher endereço na criação da coleta.
- Criar script de backfill.
- Adicionar delay/rate limit.

## 4. Prioridade média

### Monitoramento tempo real — fase 1

Polling no Web:

- botão liga/desliga;
- intervalo padrão 60s;
- atualização manual;
- persistência da preferência do admin.

### Monitoramento tempo real — fase 2

Heartbeat mobile:

- endpoint de localização atual;
- envio periódico pelo app;
- controle de bateria;
- controle liga/desliga por pesquisa;
- status online/ausente.

### Setores e cotas

- concluido: importacao administrativa de Shapefile por CLI, restrita a
  `Polygon` EPSG:4326 e protegida pelas regras de tenant existentes;
- associar coleta ao setor;
- calcular progresso por setor;
- alertar cota atingida;
- dashboard de cobertura territorial.

### Exportação PDF

- relatório com capa;
- gráficos;
- resumo executivo;
- filtros aplicados;
- anexar mapa de cobertura.

## 5. Futuro

- Análise multivariável.
- Heatmap.
- Cluster de marcadores.
- Portal do cliente.
- Branding por empresa.

## 6. Débitos técnicos conhecidos

| Débito | Risco | Prioridade |
|---|---:|---:|
| Contrato `lng/lon` inconsistente | bugs de mapa/mobile | Alta |
| Setores alternando `geometria/poligono` | bugs de renderização | Alta |
| Mobile não revalidado pós-multitenancy | vazamento operacional | Alta |
| Geocoding órfão/parcial | endereço ausente | Média |
| Poucos testes automatizados | regressão | Alta |
| Scripts seed no repo | sujeira operacional | Baixa |
| Deletes físicos em setores | perda histórica | Média |

## 7. Não fazer agora

- Reescrever backend inteiro.
- Migrar stack.
- Implementar multivariável antes do mobile.
- Chamar OpenStreetMap em massa na tela.
- Criar tempo real antes do monitoramento básico estar estável.
- Mudar contratos validados sem versionamento.

## 8. Critério para próxima versão estável

- Backend com testes mínimos.
- Web sem erros no console nos fluxos principais.
- Mobile sincronizando com agente real.
- Documentação atualizada.
- Checkpoint/tag nos três repositórios.
- Matriz Empresa A x Empresa B validada.
