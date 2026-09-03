# INTELIGÊNCIA ELEITORAL
# MVP 1 — POTENCIAL DE CRESCIMENTO
# VISUALIZAÇÕES E INTERPRETAÇÃO

**Data:** 2026-09-02
**Status:** Prompt 06 implementado — camada de leitura analítica sobre o
resultado do Prompt 05. Rodada exclusivamente Web; Backend intocado; feature
`potencial_crescimento` segue INATIVA no catálogo real.

## 1–2. Objetivo e princípios

Transformar o `GrowthAnalysisResponse` em exploração estratégica que responde
"onde aparecem sinais relativamente mais favoráveis, sustentados por quê, com
que escala e com quais limitações" — sem responder "quem vai converter" ou
"quantos votos". Princípios invariáveis: o frontend VISUALIZA/ORGANIZA/
EXPLICA sem criar semântica estatística; resultado da API é imutável
(derivações via helpers puros/useMemo); **interpretação determinística por
templates rastreáveis às evidências, sem LLM** (ADR-073); sem score, sem
ranking automático, sem projeção.

Arquitetura (repo Web): domínio visual puro em `src/lib/growthPotential.ts`
(interpretação, ordenação, filtros de visualização, dados de gráfico, copy
centralizada — testável no harness); componentes em
`src/components/growth-potential/` (`GrowthExecutiveSummary`,
`GrowthSignalSelector`, `GrowthDeltaChart`, `GrowthScaleScatter`,
`GrowthChartTooltip`, `GrowthSegmentDetail`, `GrowthTerritoryView`);
integração em `GrowthPotentialPage` com view state separado do
`analysisResult` (selectedSignal, selectedSegmentKey, sort, filtro) — o view
state nunca altera o resultado e é remontado a cada nova execução (`key` por
`executed_at`).

## 3. Visão executiva

Cards estruturais: universo elegível, segmentos analisados/disponíveis/
suprimidos por base, sinais configurados, elegíveis fora dos segmentos.
Deliberadamente NÃO existem "potencial geral", "score médio" ou "votos
potenciais". A quantidade de findings não é inferência de qualidade
eleitoral.

**Barra metodológica** sempre visível (fora de accordion): Análise não
ponderada · IC de Wilson sob hipótese AAS · Referência inclui o próprio
segmento · Análise exploratória de múltiplas comparações (tooltips com a
copy completa).

## 4. Seletor de sinal

"Leitura por sinal" apenas entre sinais efetivamente presentes
(SECOND_OPTION/REJECTION/VOTE_DECISION); sinal ausente nunca aparece como
métrica zero. Labels de exibição: "Segunda opção para a candidatura",
"Rejeição à candidatura", "Voto ainda móvel". A direção favorável acompanha
os gráficos: maior=favorável para segunda opção/mobilidade; **menor=favorável
para rejeição**, com o texto "valores negativos indicam rejeição abaixo da
referência".

## 5. Visualização 1 — Diferença vs referência

Barras horizontais (Recharts) com o **delta_pp REAL da API** por segmento,
linha de referência em 0 pp, unidade explícita ("pp"). **Rejeição não é
invertida**: −10 pp permanece barra negativa (favorável, explicado na
legenda) — nenhum sinal matemático é trocado para "favorável à direita".
Evidência suprimida/null **não recebe barra de 0**: entra numa lista à parte
("Base insuficiente"/"Indisponível — não plotado"). Default = ordem recebida
do motor; muitos segmentos usam scroll interno (altura por linha), sem
truncar dados e sem "Top N". Clique na barra seleciona o segmento.

## 6. Visualização 2 — Escala × diferença

Scatter: **X = participation_rate** (% do universo elegível), **Y =
delta_pp** do sinal selecionado; linha zero no Y (referência matemática);
**sem limiar vertical de escala** (não existe threshold metodológico
aprovado) e **sem quadrantes semânticos** (nada de "priorizar/defender/
descartar"); pontos de tamanho constante (participação já está no eixo X).
Rejeição mantém delta cru. Clique seleciona o segmento.

Tooltips (compartilhados): segmento, n bruto, participação, taxa do
segmento e da referência com **bases válidas**, delta pp, lift e direção
observada — sempre com unidades (%, pp, ×); nunca score.

## 7–8. Segmento vs referência e intervalos

`GrowthSegmentDetail` (segmento selecionado): para cada evidência
disponível, barras Segmento × Universo elegível com numerador/base, taxa,
**IC de Wilson de ambas as taxas**, delta pp, lift e direção observada;
warnings POR EVIDÊNCIA visíveis (o warning global não engole o local).
Nota fixa: "Intervalo de Wilson 95%, aproximação sob hipótese de amostragem
aleatória simples" — nunca "95% de certeza" nem "estatisticamente
significativo" (mesmo sem sobreposição de intervalos).

## 9–15. Interpretação determinística

`buildEvidenceInterpretation(evidence)` → `{signal, status, direction,
headline, detail, caveats[]}`; `buildSegmentReading(finding)` → listas
favoráveis/desfavoráveis/neutros/indisponíveis + alertas. Templates (com os
números reais formatados):

- **Segunda opção FAVORABLE**: "Neste segmento, a candidatura aparece como
  segunda opção em 30,0% das respostas válidas, contra 15,0% no universo
  elegível (+15,0 pp). O sinal aparece relativamente acima da referência.";
  UNFAVORABLE: "...aparece abaixo da referência...".
- **Rejeição FAVORABLE (delta negativo)**: "A rejeição à candidatura é de
  12,0% neste segmento, contra 22,0% no universo elegível (−10,0 pp). A
  rejeição observada está relativamente abaixo da referência." — nunca
  "88% de aceitação", nunca "−10,0%"; UNFAVORABLE sem "segmento hostil".
- **Mobilidade**: "A proporção de respostas classificadas como voto móvel..."
  — nunca "eleitores fáceis de converter".
- **NEUTRAL**: "A taxa observada coincide com a referência analisada." —
  nunca "sem potencial".
- **SMALL_BASE** acrescenta "Leitura com base reduzida; interpretar com
  cautela."; **suprimida** não gera interpretação direcional ("Base
  insuficiente para interpretar este sinal."); **SIGNAL_UNAVAILABLE**
  não vira neutro ("Sinal indisponível nesta execução." + warnings).

"Leitura do segmento": apenas dimensão/n/participação/evidências/warnings;
**sem contagem agregada ("3 de 4 sinais")** e **sem conclusão global**
("este segmento é uma oportunidade" não existe). Copy centralizada na lib
(labels, warnings, templates, notas metodológicas) para o QA do Prompt 07.

Linguagem proibida testada nos templates: "vai votar/converter", "chance de
conversão", "probabilidade de voto", "garantido", "comprovado", "votos
potenciais", "ganhará", "melhor oportunidade", "significância/p-valor",
score/ranking.

## 16–19. Warnings, diagnostics, seleção, ordenação e filtros

- Warnings por segmento (badge "N alerta(s)") e por evidência; código
  desconhecido usa fallback da `message` da API (sem quebrar);
- Diagnostics permanecem em "Qualidade e cobertura dos dados", separados dos
  segmentos; hashes em "Detalhes técnicos" (nunca KPI);
- **Seleção única**: tabela, barra, scatter e mapa atualizam o MESMO
  `selectedSegmentKey` (sem estados independentes); sem deep link;
- **Ordenação explícita**: métrica (ordem original/n/participação/delta/
  lift) + direção, com o texto "Ordenado por: ..." sempre visível; default
  ordem original; nenhuma métrica "potential"; nunca "Ranking";
- **Filtros de visualização** (texto e "somente disponíveis") com o aviso
  "alteram somente a visualização do resultado já calculado" — nenhum novo
  POST /analisar (provado: uma única chamada `analyze` na página).

## 20–24. Território e mapa

**Auditoria de viabilidade**: geometria de SETOR está disponível pelo
contrato Web existente (`projectsService.getSetores` devolve
`Setor.geometria`; conversão pelo helper já exportado
`anelExternoParaLatLng` de `lib/lideranca.ts`; Leaflet já é padrão).
Geometria de MUNICÍPIO **não** é exposta por nenhum contrato Web atual.

Implementado (`GrowthTerritoryView`):

- **Regra antiagregação (crítica)**: a leitura territorial exige a seleção
  de UMA combinação completa das dimensões de perfil
  (`territoryFindingsForCombination`) — um finding por território; o Web
  NUNCA soma, faz média ou reponderia findings ("se o motor não entregou o
  agregado, ele não existe"). Combinação única é auto-selecionada (visível);
- **Tabela territorial sempre presente** (alternativa acessível): território,
  n, participação, taxa·delta do sinal, eleitorado apto ("Contexto eleitoral
  do território", tooltip "não é projeção de votos"; null → "Contexto
  eleitoral indisponível", nunca 0);
- **Mapa (nível SETOR)**: polígonos Leaflet coloridos pelo delta_pp do sinal
  selecionado (um sinal por vez), **escala contínua centrada em 0 pp**
  (azul acima/laranja abaixo, intensidade pela magnitude; sem thresholds
  ALTO/MÉDIO/BAIXO, sem rótulos "ótimo/ruim"); rejeição não invertida
  (legenda explica); sem evidência → cinza tracejado (nunca cor de valor);
  cor jamais mistura eleitorado apto; tooltip completo; clique seleciona;
- **Mapa de MUNICÍPIO: ADIADO POR CONTRATO DE GEOMETRIA AUSENTE** no Web
  (mensagem na UI; leitura tabular cobre a informação). Gap registrado para
  avaliação posterior — sem endpoint novo nesta rodada.

## 25–27. Responsividade, acessibilidade, performance

Gráficos lado a lado em xl, empilhados abaixo; barras com scroll interno;
tabela sem scroll horizontal da página. Toda informação visual tem
alternativa textual/tabular (mapa e cores nunca são o único canal);
`aria-pressed` no seletor, legendas explícitas (métrica/unidade/referência/
direção). Derivações memoizadas (`useMemo`); sem virtualização antecipada
(sem evidência de necessidade); dados nunca amostrados/truncados.

## 28. Testes

- `tests/growthVisualization.test.mjs` — 27 testes puros: labels/direções,
  determinismo, templates V08–V15 (incl. **oráculo segunda opção
  30%/15%/+15 pp** e **oráculo rejeição 12%/22%/−10 pp FAVORABLE** sem "88%"
  e sem "−10,0%"), linguagem proibida em todas as variantes, dados do delta
  chart (null/suprimido sem barra, rejeição −10 crua, ordem preservada),
  ordenação explícita (nulls por último; sem métrica "potential"), scatter
  X/Y, leitura estruturada sem contagem/conclusão, sumário executivo,
  filtro puro/imutável, antiagregação territorial e escala de cor contínua;
- `tests/growthVisualizationPage.test.mjs` — 27 verificações estáticas:
  visão executiva, barra metodológica, fallback de warning, seletor,
  delta chart (delta_pp, zero line, sem inversão, sem sort, sem Top N),
  scatter (eixos, zero line, sem limiar vertical, sem quadrantes, raio
  constante), tooltip com denominadores, detalhe com IC/warnings, leitura
  sem agregação, território (combinação obrigatória, sem soma/média, um
  sinal por vez, contexto eleitoral não-projeção, contratos existentes,
  município adiado), seleção única, ordenação visível, filtro sem recálculo,
  remontagem por execução, provas negativas (score/ranking/projeção) e
  ausência de LLM.

Suíte Web completa: **1229 passed, 0 fail** (baseline 1175 + 54); build
PASS; lint com os mesmos 10 erros preexistentes (zero novo).

## 29. QA visual manual

**EXECUTADO no Prompt 07** em browser real (Chrome/CDP contra Backend
PostgreSQL descartável — `tests/growthPotential.e2e.cdp.mjs`, PASS): fluxo
completo configurar→validar→analisar→explorar; funil idêntico ao oráculo;
gráficos delta/scatter; detalhe do segmento com números do oráculo na tela
(incl. rejeição com delta cru); barra metodológica e warnings visíveis;
viewports desktop (1440) e intermediário (900) sem scroll horizontal; troca
A→B fail-closed sem resíduo; zero 4xx/5xx. Detalhes e achados:
`doc/20-inteligencia-eleitoral-potencial-crescimento-qa.md`.

## 30. Fronteira com o Prompt 07

O Prompt 06 valida a REPRESENTAÇÃO. O Prompt 07 fará QA metodológico +
estatístico + E2E real + stress + casos extremos (incluindo o QA manual
ponta a ponta contra Backend real em banco descartável, pendente desde o
Prompt 05).
