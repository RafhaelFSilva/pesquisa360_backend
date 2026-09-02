# PLANO DE TESTES — PROMPT 05 QA E2E

**Data:** 2026-09-02  
**Escopo:** Validação de 41 testes de segurança e funcionalidade da Sprint 0

---

## MATRIZ DE TESTES — VALIDAÇÃO ESTÁTICA

### Teste T01 — Security Review: hardcoded values

**Objetivo:** Buscar anti-patterns de segurança

**Execução:**

```powershell
cd C:\Dev\pesquisa360_backend
grep -r "company_id.*=.*1" pesquisa360/ 2>/dev/null | wc -l
grep -r "user.id.*=.*1" pesquisa360/ 2>/dev/null | wc -l
grep -r "tenant_id.*=" pesquisa360/ 2>/dev/null | wc -l
grep -r "==.*all" pesquisa360/ 2>/dev/null | wc -l
```

**Esperado:** 0 ocorrências (ou documentadas como safe)

**Status:** ⏳ (Executar com grep na sessão)

---

### Teste T02 — Security Review: company_id em payload operacional

**Objetivo:** Validar que company_id NUNCA é aceito de client em operações

**Verificação de código:**

1. Endpoints operacionais (não /admin) devem rejeiir company_id de body
2. Schemas Pydantic não devem incluir company_id em modelos operacionais
3. Dependências devem validar current_user.company_id, não payload

**Status:** ⏳ (Ler schemas.py e endpoints)

---

### Teste T03 — require_module & require_feature existem

**Objetivo:** Confirmar que gates Backend estão disponíveis

**Verificação:**

- [ ] pesquisa360/api/dependencies/__init__.py contém require_module
- [ ] pesquisa360/api/dependencies/__init__.py contém require_feature
- [ ] Ambos aceitam contexto (db, current_user, projeto_id, pesquisa_id)
- [ ] Retornam 403 se sem entitlement
- [ ] Retornam 404 se recurso inválido

**Status:** ⏳ (Ler dependencies/*)

---

### Teste T04 — ModuleGate existe no Web

**Objetivo:** Componente reutilizável implementado

**Verificação:**

- [ ] src/components/ModuleGate.tsx existe
- [ ] Implementa fail-closed (loading/error bloqueado)
- [ ] usa modulesStore
- [ ] recebe fallback e loadingFallback

**Status:** ⏳ (Ler ModuleGate.tsx)

---

### Teste T05 — FeatureGate existe no Web

**Objetivo:** Gate de feature implementado

**Verificação:**

- [ ] src/components/FeatureGate.tsx existe
- [ ] Valida moduleKey + featureKey
- [ ] fail-closed
- [ ] usa modulesStore.hasFeature

**Status:** ⏳ (Ler FeatureGate.tsx)

---

### Teste T06 — modulesService não envia company_id

**Objetivo:** Service Web nunca envia tenant para API

**Verificação:**

```typescript
// modulesService deve apenas fazer:
GET /usuarios/me/modulos/
// com Authorization header (via apiClient)
```

**Checklist:**

- [ ] Não monta query params
- [ ] Não envia body
- [ ] Usa apiClient autenticado
- [ ] Nunca refere company_id

**Status:** ⏳ (Ler modulesService.ts)

---

### Teste T07 — Potencial de Crescimento marcado INATIVO

**Objetivo:** Feature não pode ser concedida nesta fase

**Verificação:**

```python
# No catálogo seed ou migration:
INSERT INTO modulo_funcionalidades VALUES (
  ..., chave='potencial_crescimento', ativo=FALSE
)
```

**Checklist:**

- [ ] Feature existe no catálogo
- [ ] ativo = FALSE
- [ ] Não aparece em GET /usuarios/me/modulos/ a menos que explicitamente ativada
- [ ] Web FeatureGate bloqueia mesmo que concessão existisse

**Status:** ⏳ (Ler models.py seed)

---

### Teste T08 — logout() chama resetModules()

**Objetivo:** Limpeza completa de capabilities ao logout

**Verificação:**

```typescript
// authStore.logout() deve fazer:
logout: () => {
  set({ token: null, user: null })
  useModulesStore.getState().resetModules()  // ← CRÍTICO
}
```

**Status:** ⏳ (Ler authStore.ts)

---

### Teste T09 — POST /admin/* rejeita usuario comum

**Objetivo:** Autoelevação bloqueada

**Verificação:**

Usuário COORDENADOR tenta:

```
POST /admin/empresas/A/entitlements
PATCH /admin/empresas/A/entitlements/1
```

**Esperado:** 403 FORBIDDEN (não 200, 405 ou erro genérico)

**Status:** ⏳ (Ler modulos_admin.py require_superadmin)

---

### Teste T10 — 404 antes de 403

**Objetivo:** CRÍTICO — não revelar licença por ACL

**Cenário:**

- Usuário Empresa A (SEM Inteligência Eleitoral)
- Tenta acessar recurso Empresa B (com ou sem módulo)

**Esperado:** 404 recurso, NUNCA 403 módulo

**Verificação de código:**

```python
# Endpoint deve fazer:
1. Validar ACL (current_user.company_id vs recurso.company_id)
2. Se falha ACL → 404
3. Se passa ACL, validar entitlement
4. Se falha entitlement → 403
```

**Status:** ⏳ (Ler arquivos de endpoint que usam gates)

---

## TESTES FUNCIONAIS — VALIDAÇÃO DE CÓDIGO

### Teste T11 — Admin GET /admin/modulos/

**Objetivo:** SuperAdmin lista catálogo

**Verificação:**

- [ ] Rota existe
- [ ] require_superadmin validado
- [ ] Retorna schema ModulosResponse com `modulos: [...]`
- [ ] Features aparecem em cada módulo
- [ ] ativo = false não filtra (catálogo completo)

**Status:** ⏳

---

### Teste T12 — Admin POST /admin/empresas/{company_id}/entitlements

**Objetivo:** Criar entitlement

**Verificação de schema:**

```python
# Esperado aceitar:
{
  "modulo_id": int,
  "escopo": "EMPRESA" | "PROJETO" | "PESQUISA",
  "projeto_id": int | null,
  "pesquisa_id": int | null,
  "inicia_em": datetime,
  "expira_em": datetime,
  "status": "ATIVO" | "SUSPENSO" | "CANCELADO",
  "funcionalidades": [feature_id, ...]
}

# Nunca aceitar:
- company_id (derivado de URL)
- user_id
- criado_em
```

**Status:** ⏳ (Ler schemas.py)

---

### Teste T13 — Validação de escopo

**Objetivo:** Rejeitar combinações inválidas

**Casos:**

| Escopo | projeto_id | pesquisa_id | Esperado |
|---|---|---|---|
| EMPRESA | null | null | ✅ ACEITA |
| EMPRESA | 123 | null | ❌ REJEITA 400 |
| PROJETO | 123 | null | ✅ ACEITA |
| PROJETO | null | null | ❌ REJEITA 400 |
| PESQUISA | null | null | ❌ REJEITA 400 |
| PESQUISA | 123 | 456 | ✅ ACEITA (pesquisa domina) |

**Status:** ⏳ (Ler validação em crud/schemas)

---

### Teste T14 — Validação de datas

**Objetivo:** Rejeitar datas inválidas

**Casos:**

| inicia_em | expira_em | Resultado |
|---|---|---|
| hoje | amanhã | ✅ OK |
| amanhã | daqui 2 dias | ✅ OK (futuro válido) |
| daqui 1 ano | hoje | ❌ REJEITA 400 |
| amanhã | amanhã | ❌ REJEITA 400 (precisa > 0) |
| None | amanhã | ❌ REJEITA 400 |

**Status:** ⏳ (Ler validação Pydantic)

---

### Teste T15 — Duplicidade bloqueada

**Objetivo:** Mesmo escopo + empresa = 409 Conflict

**Cenário:**

```
POST /admin/empresas/A/entitlements { modulo_id: 1, escopo: EMPRESA }
POST /admin/empresas/A/entitlements { modulo_id: 1, escopo: EMPRESA }
```

**Esperado:** 2º tenta retorna 409 (não 500 IntegrityError cru)

**Verificação:**

- [ ] pesquisa360.crud trata IntegrityError
- [ ] Retorna response coerente (409 ou custom error)
- [ ] Mensagem clara

**Status:** ⏳ (Ler crud.py try/except)

---

### Teste T16 — PATCH entitlement (status)

**Objetivo:** Suspender/reativar sem apagar

**Verificação:**

```python
# PATCH /admin/empresas/A/entitlements/X { status: SUSPENSO }
# Banco:
entitlements.id = X
entitlements.status = SUSPENSO
entitlements.atualizado_em = NOW()
# NÃO DELETE
```

**Status:** ⏳ (Ler modulos_admin.py PATCH)

---

### Teste T17 — PUT entitlement/funcionalidades

**Objetivo:** Adicionar/remover features

**Verificação:**

```python
# PUT /admin/empresas/A/entitlements/X/funcionalidades
# { funcionalidades: [feature_id_1, feature_id_2] }
# Resultado:
# - feature_id_1 criada em modulo_entitlement_funcionalidades
# - feature_id_2 criada
# - features antigas removidas
```

**Status:** ⏳ (Ler PUT handler)

---

### Teste T18 — GET /admin/empresas/{id}/entitlements

**Objetivo:** SuperAdmin lista all entitlements de empresa

**Verificação:**

- [ ] Paginado
- [ ] Inclui histórico (CANCELADO, SUSPENSO, ATIVO)
- [ ] Retorna funcionalidades (features)
- [ ] Valida company_id em URL

**Status:** ⏳

---

### Teste T19 — GET /usuarios/me/modulos/

**Objetivo:** Usuário comum consulta capabilities

**Verificação:**

```json
{
  "modulos": [
    {
      "chave": "inteligencia_eleitoral",
      "nome": "Inteligência Eleitoral",
      "funcionalidades": [
        { "chave": "potencial_crescimento", "nome": "..." }
      ]
    }
  ]
}
```

**Status:** ⏳

---

### Teste T20 — Auditoria de eventos

**Objetivo:** Toda mutação admin registrada

**Verificação:**

Para cada CREATE/UPDATE/DELETE de entitlement:

- [ ] Evento persiste em audit_events
- [ ] actor capturado (current_user.id)
- [ ] company_id alvo registrado
- [ ] timestamp
- [ ] antes/depois se UPDATE
- [ ] Sem token/senha

**Status:** ⏳ (Ler services/auditoria.py)

---

## TESTES DE INTEGRAÇÃO — VALIDAÇÃO E2E

### Teste T21–T32 — Cenários Empresa A × Empresa B

**Requisito:** Banco de testes com 2 empresas, 2 projetos, 2 pesquisas

**Pré-requisito:** Docker PostgreSQL disponível

**Status:** ⏳ BLOQUEADO — Sem acesso a PostgreSQL

---

## RESUMO DE TESTES

| Categoria | Quantidade | Status |
|---|---|---|
| Validação estática (T01–T10) | 10 | ⏳ |
| Funcional código (T11–T20) | 10 | ⏳ |
| E2E banco (T21–T32) | 12 | ⏳ BLOQUEADO |
| **TOTAL** | **32** | — |

---

## PRÓXIMOS PASSOS

1. Executar T01–T10 (análise estática)
2. Ler código T11–T20 (validação de schema)
3. Documentar disponibilidade PostgreSQL
4. Se PostgreSQL disponível: E2E
5. Se PostgreSQL indisponível: Documentar como pendência, prosseguir com hardening

---
