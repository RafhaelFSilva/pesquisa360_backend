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
