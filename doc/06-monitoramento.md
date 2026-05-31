# Pesquisa360 — Monitoramento

**Status:** monitoramento de coletas sincronizadas validado no Web  
**Objetivo:** documentar comportamento atual e próximos passos.

## 1. Escopo atual

O monitoramento atual exibe **coletas já sincronizadas**.

Ele não mostra ainda a posição viva do agente em tempo real. Essa funcionalidade será evolução futura.

## 2. Endpoint

```http
GET /pesquisas/{pesquisa_id}/coletas/monitoramento/
```

Resposta:

```json
[
  {
    "id": 533,
    "pesquisa_id": 4,
    "agente_id": 5,
    "agente_nome": "Rafhael Ferreira",
    "data_inicio_coleta": "2026-05-03T17:38:01+00:00",
    "data_fim_coleta": "2026-05-03T17:56:22+00:00",
    "localizacao_inicio": { "lat": 0.0385, "lng": -51.1333 },
    "localizacao_fim": { "lat": 0.0384, "lng": -51.1330 },
    "inconformidade_localizacao": false,
    "status_sincronizacao": "sincronizado",
    "foi_offline": false,
    "endereco_estimado": "Avenida 6, Marabaixo II..."
  }
]
```

## 3. Semântica

| Campo | Significado |
|---|---|
| `foi_offline=false` | coleta feita com internet |
| `foi_offline=true` | coleta feita sem internet e sincronizada depois |

Mapa:

| Cor | Significado |
|---|---|
| Azul | coleta online |
| Vermelho | coleta offline |

`inconformidade_localizacao` é indicador separado de online/offline.

## 4. Interface atual

### Cabeçalho

- Voltar ao projeto.
- Filtro por agente.
- Filtro por setor.
- Total de registros.
- Botão atualizar.

### Mapa

- Exibe marcadores das coletas.
- Exibe setores.
- Mantém altura fixa/responsiva.
- Legenda: Online/Offline.
- Clicar em coleta na tabela deve focar o marcador no mapa.

### Tabela

Colunas recomendadas:

```text
ID | Agente | Modo | Início | Fim | Detalhes
```

Informações secundárias em detalhes:

- endereço;
- status de sincronização;
- GPS início/fim;
- inconformidade;
- offline/online.

### Paginação

- Padrão: 10 registros por página.
- Botão Mostrar todas.
- Mostrar todas usa scroll vertical interno.
- Evitar rolagem horizontal.

## 5. Endereço estimado

Campo:

```text
coletas.endereco_estimado
```

Função existente:

```python
pesquisa360/utils/geocoding.py
obter_endereco_por_coords(lat, lon)
```

Regra:

```text
criação/backfill -> calcula endereço
monitoramento -> retorna endereço salvo
```

Se não existir endereço:

```text
Endereço não processado
```

Evitar:

```text
Processando...
```

## 6. Filtros

- Filtro por agente.
- Filtro por setor.

Associação formal coleta-setor pode evoluir para:

```text
coleta dentro do polígono do setor -> setor provável
```

ou:

```text
mobile envia setor_id
```

## 7. Evolução tempo real

### Fase 1 — polling do painel

```text
admin liga atualização automática
web chama GET /monitoramento a cada 60s
admin desliga para reduzir consumo
```

### Fase 2 — heartbeat mobile

```text
mobile captura posição
mobile envia POST /agente/localizacao
backend armazena última posição
web consulta posições ativas
```

Pontos críticos: bateria, privacidade, consumo de API, áreas sem internet e retenção histórica.

## 8. Endpoints futuros sugeridos

### POST `/agente/localizacao/heartbeat`

```json
{
  "lat": 0.0385,
  "lng": -51.1333,
  "capturado_em": "2026-05-03T17:38:01Z",
  "precisao_metros": 8,
  "bateria_percentual": 74
}
```

### GET `/pesquisas/{pesquisa_id}/agentes/localizacao-atual/`

```json
[
  {
    "agente_id": 5,
    "agente_nome": "Rafhael Ferreira",
    "lat": 0.0385,
    "lng": -51.1333,
    "ultima_atualizacao": "2026-05-03T17:38:01Z",
    "status": "online"
  }
]
```

## 9. Checklist

- Coletas aparecem no mapa.
- Online/offline aparecem com cores corretas.
- Tabela lista coletas.
- Filtros funcionam.
- Endereço aparece quando processado.
- Coletas sem endereço mostram fallback claro.
- Paginação não estoura layout.
- Clicar na coleta foca mapa.
- Setores aparecem no mapa.
- Tenant é respeitado.
