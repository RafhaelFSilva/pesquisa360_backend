# Pesquisa360 — Relatórios

**Status:** resumo simples e crosstab 2D validados  
**Objetivo:** documentar contratos, regras e limites dos relatórios.

## 1. Escopo atual

O módulo de relatórios possui:

1. Resumo das Respostas.
2. Cruzamento de Dados / Crosstab.

A análise multivariável com 3 ou mais perguntas fica para roadmap futuro.

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

## 7. Crosstab vs multivariável

Crosstab atual:

```text
Pergunta A x Pergunta B
```

Multivariável futura:

```text
Sexo + Faixa Etária x Governador
```

Não misturar no mesmo endpoint sem redesenhar contrato.

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

Essa combinação 2 a 2 não é análise multivariável verdadeira.

## 10. Testes mínimos

- Resumo carrega 5 perguntas.
- Cada pergunta obrigatória soma 100 respostas no seed.
- Percentuais totalizam próximo de 100%.
- Crosstab Governador x Presidente.
- Crosstab Sexo x Governador.
- Crosstab Escolaridade x Faixa etária.
- Usuário de outro tenant não acessa relatório.
