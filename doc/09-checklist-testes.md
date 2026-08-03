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
- [ ] Documentação atualizada.
- [ ] Scripts temporários removidos ou documentados.
