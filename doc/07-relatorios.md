# Pesquisa360 — Relatórios

**Status:** relatórios clássicos e Cruzamentos Estratégicos validados
**Objetivo:** documentar contratos, regras e limites dos relatórios.

## 1. Escopo atual

O produto mantém dois contextos complementares:

1. **Central de Relatórios:** Resumo das Respostas e Cruzamento de Dados / Crosstab 2D.
2. **Central de Inteligência:** Cruzamentos Estratégicos nos modos Explorar e Relatório.

O crosstab 2D e o cruzamento multidimensional são recursos distintos. O
primeiro compara pares; o segundo percorre de 2 a N dimensões categóricas
ordenadas sem alterar o contrato clássico.

Tambem esta aprovado para roadmap futuro um modulo separado chamado `Mapas
Estrategicos`. Ele nao faz parte do escopo atual validado de relatorios simples
e crosstab 2D.

## 2. Central de relatórios

Rota Web:

```text
/projetos/:projectId/pesquisas/:surveyId/relatorios
```

Cards:

```text
Resumo das Respostas
Cruzamento de Dados
```

## 3. Resumo das Respostas

Endpoint:

```http
GET /relatorios/pesquisas/{pesquisa_id}/simples/
```

Alias:

```http
GET /pesquisas/{pesquisa_id}/simples/
```

Objetivo: mostrar contagens e percentuais por pergunta.

Resposta esperada:

```json
{
  "pesquisa_id": 4,
  "total_entrevistas": 100,
  "perguntas": [
    {
      "pergunta_id": 13,
      "texto_pergunta": "Em quem você votaria para Governador?",
      "tipo_pergunta": "escolha_simples",
      "total_respostas": 100,
      "dados": [
        {
          "resposta": "Candidato A",
          "total": 43,
          "percentual": 43.0
        }
      ]
    }
  ]
}
```

Visualizações:

- Pizza/Rosca.
- Barras.
- Histograma para perguntas numéricas.
- Nuvem de palavras em evolução futura.

## 4. Crosstab 2D

Endpoint:

```http
POST /relatorios/pesquisas/{pesquisa_id}/crosstab/
```

Alias:

```http
POST /pesquisas/{pesquisa_id}/crosstab/
```

Payload:

```json
{
  "pergunta_linha_id": 13,
  "pergunta_coluna_id": 14
}
```

Resposta:

```json
{
  "pergunta_linha": "Em quem você votaria para Governador?",
  "pergunta_coluna": "Em quem você votaria para Presidente?",
  "dados": [
    {
      "valor_linha": "Candidato A",
      "celulas": [
        {
          "valor_coluna": "Candidato A",
          "contagem": 20,
          "percentual": 46.51
        }
      ]
    }
  ]
}
```

## 5. Perguntas elegíveis

Crosstab aceita perguntas categóricas:

```text
escolha_simples
multipla_escolha
multipla_escolha_unica
multipla_escolha_multipla
MultiplaEscolha_Unica
MultiplaEscolha_Multipla
```

Campo correto de texto:

```ts
question.texto_pergunta
```

## 6. Exemplos

Sexo x Governador:

```json
{
  "pergunta_linha_id": 15,
  "pergunta_coluna_id": 13
}
```

Faixa etária x Governador:

```json
{
  "pergunta_linha_id": 16,
  "pergunta_coluna_id": 13
}
```

Escolaridade x Presidente:

```json
{
  "pergunta_linha_id": 17,
  "pergunta_coluna_id": 14
}
```

## 7. Crosstab 2D versus Cruzamentos Estratégicos

Crosstab atual:

```text
Pergunta A x Pergunta B
```

Cruzamento estratégico:

```text
Sexo -> Faixa Etária -> Governador -> Avaliação
```

Os dois fluxos permanecem separados: o crosstab gera comparações 2 a 2; o
cruzamento estratégico preserva a ordem escolhida e produz uma árvore de
caminhos observados.

## 8. Backend

Regras:

- validar tenant antes de calcular;
- buscar perguntas da mesma pesquisa;
- retornar dados serializáveis;
- não retornar objetos Pandas/SQLAlchemy;
- manter schema e retorno alinhados.

Erro comum evitado:

```json
{ "data": [] }
```

quando o schema espera:

```json
{ "dados": [] }
```

gera `ResponseValidationError`.

## 9. Frontend

Se 2 perguntas selecionadas:

```text
gera 1 gráfico
```

Se 5 perguntas selecionadas:

```text
gera combinações 2 a 2
5 perguntas = 10 gráficos
```

Essa combinação 2 a 2 pertence ao crosstab clássico. Para análise hierárquica
de 2 a N dimensões, usar a Central de Inteligência.

## 10. Testes mínimos

- Resumo carrega 5 perguntas.
- Cada pergunta obrigatória soma 100 respostas no seed.
- Percentuais totalizam próximo de 100%.
- Crosstab Governador x Presidente.
- Crosstab Sexo x Governador.
- Crosstab Escolaridade x Faixa etária.
- Usuário de outro tenant não acessa relatório.

## 11. Roadmap - Mapas Estrategicos

Modulo futuro aprovado, nao concluido:

- Cobertura das Coletas.
- Resultado por Setor.
- Lideranca por Setor.
- Distribuicao de Coletas.
- escolha de setores.
- filtros.
- previa.
- Relatorio Executivo de Mapas.

Dependencias de dominio:

- usar setores com finalidade `RELATORIO` ou `AMBOS` para analise territorial;
- nao enviar setores exclusivamente `RELATORIO` ao Mobile como setores
  operacionais;
- classificar entrevistas no Backend/PostGIS, preferencialmente com
  `ST_Covers`;
- usar localizacao inicial da coleta como referencia principal e localizacao
  final como fallback;
- retornar/representar `SEM_SETOR` quando nao houver setor analitico;
- resolver sobreposicao entre setores analiticos sem duplicar entrevistas.

## 12. Motor dos Cruzamentos Estratégicos

O crosstab 2D permanece inalterado. O novo motor recebe duas ou mais perguntas
categóricas ordenadas e cruza exclusivamente respostas da mesma `coleta_id`.
Ele materializa apenas caminhos observados e retorna os prefixos de todos os
níveis para navegação hierárquica.

- `total_entrevistas`: coletas distintas da pesquisa segundo a política atual.
- `base_valida`: coletas com valor utilizável em todas as dimensões processadas;
  com `incluir_sem_resposta=true`, inclui a categoria `__SEM_RESPOSTA__`.
- `base_pai`: entrevistas do prefixo anterior; no nível 1, a base válida.
- `percentual_pai`: contagem do nodo dividida pela base pai.
- `percentual_total`: contagem do nodo dividida pela base válida.

Arrays JSON são expandidos somente em perguntas de múltipla escolha. A entrevista
é contada no máximo uma vez por caminho, mas pode pertencer a vários filhos;
portanto, percentuais de filhos podem exceder 100%. Strings legadas permanecem
uma categoria e não há fuzzy matching.

Limites configuráveis: `P360_CROSS_MAX_DIMENSIONS` (8),
`P360_CROSS_MAX_NODES` (5000) e `P360_CROSS_MAX_COMBINATIONS` (100000).
O tenant sempre vem de `current_user.company_id`, validado por
`Pesquisa -> Projeto.company_id` e pelas coletas consultadas.

O motor entrega evidência descritiva, não inferência causal nem interpretação
eleitoral automática. A UI Web consome esse contrato nos modos Explorar e
Relatório; a impressão é feita pelo navegador com HTML, SVG e CSS para A4, sem
geração de PDF no backend.

### Respostas espontâneas

Respostas espontâneas são transformadas por `get_active_spontaneous_mapping_for_report` e `resolve_reportable_response_value`. Na modelagem atual, a categorização é ativa e vinculada à pesquisa; respostas sem mapeamento seguem a semântica comum dos relatórios e aparecem como “Não categorizada”. A seleção de categorias é apenas um recorte de exibição/ramificação e preserva os denominadores brutos calculados.

### Filtros e denominadores

`filtros_respostas` seleciona os ramos exibidos depois do cálculo de bases,
contagens e percentuais. Não há renormalização: em uma base de 100 entrevistas,
se A = 40, B = 30 e C = 30, exibir apenas A e B mantém 40% e 30%.

O Web envia `incluir_sem_resposta=true`. A categoria técnica usa a chave
`__SEM_RESPOSTA__` e o rótulo “Sem resposta”. O endpoint aceita o valor legado
`false` para compatibilidade.

### Interface Web

- rota contextual: `/projetos/:projectId/pesquisas/:surveyId/inteligencia/cruzamentos`;
- seletor compartilhado com checkboxes, ordem ajustável, detalhes e filtros por resposta;
- modo Explorar com navegação progressiva, breadcrumb e cache das profundidades já carregadas;
- gráficos de barras, pizza ou rosca, usando `percentual_pai` como métrica principal e sem nova consulta ao trocar a visualização;
- bases do segmento e total permanecem disponíveis junto às contagens e percentuais retornados pelo backend;
- modo Relatório com parametrização prévia de profundidade, respostas, segmentos, gráfico e detalhes;
- nodos agrupados por caminho pai em seções, em vez de um gráfico independente por nodo;
- detalhes ocultos por padrão; quando abertos, usam tabela no desktop e cards no mobile, sem scroll interno;
- o gráfico ocupa a largura disponível quando os detalhes estão ocultos;
- impressão pelo navegador, com `window.print()`, gráficos SVG, tabelas condicionais, controles administrativos ocultos e CSS A4;
- o fluxo não depende de `jsPDF` ou `html2canvas`;
- a rota global `/inteligencia` continua dedicada à inteligência territorial.
