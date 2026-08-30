"""ADR-041 -- painel administrativo de seguranca: GET /admin/auditoria/resumo.

Agregacao SQL sobre `audit_events` (a fonte da verdade continua sendo a
trilha da ADR-039). Aqui os eventos sao semeados direto pelo servico, com IP,
e-mail tentado e horario controlados, e o resumo e conferido por HTTP.

Reaproveita o setup de `AuditoriaTests` (SQLite migrado, usuarios, projetos,
ACL); os testes herdados sao desligados.
"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import text

from pesquisa360.services import auditoria
from tests.test_auditoria import (
    A1, A2, B1, AGENTE, CLIENTE, COORD, EMPRESA_A, EMPRESA_B, GERENTE, GERENTE_B, SUPER, SUPERV,
    AuditoriaTests,
)

IP_A, IP_B, IP_OK = "203.0.113.10", "203.0.113.20", "198.51.100.5"


class PainelSegurancaTests(AuditoriaTests):
    def setUp(self):
        super().setUp()
        self.como(SUPER, "Superadmin")

    # --- helpers -----------------------------------------------------------
    def ev(self, tipo, *, ip=IP_A, company_id=EMPRESA_A, user_id=None, project_id=None, email=None, sev=None):
        ctx = auditoria.ContextoRequest(ip_address=ip, path="/x", http_method="GET")
        severidade = sev or (auditoria.SEV_HIGH if tipo == auditoria.CROSS_TENANT_ACCESS_ATTEMPT else auditoria.SEV_WARNING)
        auditoria.registrar(tipo, severidade, user_id=user_id, company_id=company_id,
                            project_id=project_id, attempted_email=email, contexto=ctx)

    def semear(self):
        """Empresa A: 3 login falhos (2 e-mails), 2 RBAC, 3 ACL, 1 cross-tenant,
        2 PROJECT_ACCESS, 1 NOTIFICATION_FAILED, 1 SUPPRESSED, 1 LOGIN_SUCCESS.
        Empresa B: 1 login falho. Global (sem tenant): 1 login falho."""
        LF, AIL = auditoria.LOGIN_FAILED, auditoria.ACCOUNT_INACTIVE_LOGIN
        self.ev(LF, email="alvo@a.com")
        self.ev(LF, email="alvo@a.com", ip=IP_B)
        self.ev(AIL, email="outro@a.com")
        self.ev(auditoria.RBAC_DENIED, user_id=CLIENTE, project_id=A1)
        self.ev(auditoria.RBAC_DENIED, user_id=CLIENTE, project_id=A1)
        self.ev(auditoria.ACCESS_DENIED, user_id=CLIENTE, project_id=A2)
        self.ev(auditoria.ACCESS_DENIED, user_id=CLIENTE, project_id=A2, ip=IP_B)
        self.ev(auditoria.ACCESS_DENIED, user_id=GERENTE, project_id=A2, ip=IP_B)
        self.ev(auditoria.CROSS_TENANT_ACCESS_ATTEMPT, user_id=GERENTE, project_id=B1)
        self.ev(auditoria.PROJECT_ACCESS, user_id=CLIENTE, project_id=A1, ip=IP_OK, sev=auditoria.SEV_INFO)
        self.ev(auditoria.PROJECT_ACCESS, user_id=GERENTE, project_id=A2, ip=IP_OK, sev=auditoria.SEV_INFO)
        self.ev(auditoria.PROJECT_ACCESS_NOTIFICATION_FAILED, user_id=CLIENTE, project_id=A1, ip=IP_OK)
        self.ev(auditoria.PROJECT_ACCESS_NOTIFICATION_SUPPRESSED, user_id=GERENTE, project_id=A2, ip=IP_OK, sev=auditoria.SEV_INFO)
        self.ev(auditoria.LOGIN_SUCCESS, user_id=GERENTE, ip=IP_OK, sev=auditoria.SEV_INFO)
        self.ev(LF, email="alvo@b.com", company_id=EMPRESA_B, ip=IP_B)
        self.ev(LF, email="ninguem@x.com", company_id=None, ip=IP_A)

    def resumo(self, **params):
        r = self.client.get("/admin/auditoria/resumo", params=params)
        return r.status_code, (r.json() if r.status_code == 200 else r.text)

    # --- PS01..PS07: escopo e autorizacao -----------------------------------
    def test_PS01_superadmin_resumo_global_com_totais_exatos(self):
        self.semear()
        status, corpo = self.resumo()
        self.assertEqual(status, 200, corpo)
        self.assertEqual(corpo["totais"], {
            "total_eventos": 16, "login_failed": 5, "access_denied": 5, "cross_tenant": 1,
            "project_access": 2, "notification_failed": 1, "high": 1,
        })
        self.assertEqual(corpo["granularidade"], "hora")
        self.assertIn("periodo", corpo)

    def test_PS02_gerente_ve_somente_a_propria_empresa(self):
        self.semear()
        self.como(GERENTE, "Gerente")
        status, corpo = self.resumo()
        self.assertEqual(status, 200, corpo)
        self.assertEqual(corpo["totais"]["login_failed"], 3)       # sem o da B e sem o global
        self.assertEqual(corpo["totais"]["total_eventos"], 14)
        self.assertEqual({c["attempted_email"] for c in corpo["top_attempted_accounts"]}, {"alvo@a.com", "outro@a.com"})

    def test_PS03_gerente_com_company_id_de_outro_tenant_nao_atravessa(self):
        self.semear()
        self.como(GERENTE, "Gerente")
        for tentativa in (EMPRESA_B, 999, 0):
            status, corpo = self.resumo(company_id=tentativa)
            self.assertEqual(status, 200)
            self.assertEqual(corpo["totais"]["login_failed"], 3, tentativa)
            self.assertNotIn("alvo@b.com", {c["attempted_email"] for c in corpo["top_attempted_accounts"]})

    def test_PS04_cliente_403(self):
        self.como(CLIENTE, "Cliente")
        self.assertEqual(self.resumo()[0], 403)

    def test_PS05_supervisor_403(self):
        self.como(SUPERV, "Supervisor")
        self.assertEqual(self.resumo()[0], 403)

    def test_PS06_coordenador_403(self):
        self.como(COORD, "Coordenador")
        self.assertEqual(self.resumo()[0], 403)

    def test_PS07_agente_403(self):
        self.como(AGENTE, "Agente")
        self.assertEqual(self.resumo()[0], 403)

    # --- PS08..PS12: cards ---------------------------------------------------
    def test_PS08_login_failed_conta_LOGIN_FAILED_e_ACCOUNT_INACTIVE(self):
        for _ in range(3):
            self.ev(auditoria.LOGIN_FAILED, email="x@a.com")
        self.ev(auditoria.LOGIN_SUCCESS, user_id=GERENTE, sev=auditoria.SEV_INFO)
        _, corpo = self.resumo()
        self.assertEqual(corpo["totais"]["login_failed"], 3)
        self.ev(auditoria.ACCOUNT_INACTIVE_LOGIN, email="y@a.com")
        self.assertEqual(self.resumo()[1]["totais"]["login_failed"], 4)

    def test_PS09_access_denied_soma_RBAC_e_ACL_sem_cross_tenant(self):
        self.ev(auditoria.RBAC_DENIED, user_id=CLIENTE)
        self.ev(auditoria.RBAC_DENIED, user_id=CLIENTE)
        for _ in range(3):
            self.ev(auditoria.ACCESS_DENIED, user_id=CLIENTE)
        self.ev(auditoria.CROSS_TENANT_ACCESS_ATTEMPT, user_id=GERENTE, project_id=B1)
        _, corpo = self.resumo()
        self.assertEqual((corpo["totais"]["access_denied"], corpo["totais"]["cross_tenant"]), (5, 1))

    def test_PS10_project_access_usa_eventos_ja_deduplicados(self):
        # 5 GETs reais: a dedup de 15 min da ADR-039 deixa 1 evento; o resumo
        # conta esse 1, sem segunda dedup e sem multiplicar.
        self.como(GERENTE, "Gerente")
        with patch("pesquisa360.services.email.enviar_email", return_value=True):
            for _ in range(5):
                self.assertEqual(self.client.get(f"/projetos/{A1}").status_code, 200)
        _, corpo = self.resumo()
        self.assertEqual(corpo["totais"]["project_access"], 1)
        self.assertEqual(len(self.eventos(auditoria.PROJECT_ACCESS)), 1)

    def test_PS11_notification_failed_nao_conta_suppressed(self):
        self.ev(auditoria.PROJECT_ACCESS_NOTIFICATION_FAILED, user_id=CLIENTE, project_id=A1)
        self.ev(auditoria.PROJECT_ACCESS_NOTIFICATION_FAILED, user_id=CLIENTE, project_id=A1)
        self.ev(auditoria.PROJECT_ACCESS_NOTIFICATION_SUPPRESSED, user_id=GERENTE, project_id=A1, sev=auditoria.SEV_INFO)
        self.ev(auditoria.PROJECT_ACCESS_NOTIFICATION_SENT, user_id=GERENTE, project_id=A1, sev=auditoria.SEV_INFO)
        self.assertEqual(self.resumo()[1]["totais"]["notification_failed"], 2)

    def test_PS12_high_conta_severidade(self):
        self.ev(auditoria.CROSS_TENANT_ACCESS_ATTEMPT, user_id=GERENTE, project_id=B1)
        self.ev(auditoria.LOGIN_FAILED, email="z@a.com", sev=auditoria.SEV_HIGH)
        self.ev(auditoria.LOGIN_FAILED, email="z@a.com")
        self.assertEqual(self.resumo()[1]["totais"]["high"], 2)

    # --- PS13..PS15: rankings ------------------------------------------------
    def test_PS13_top_ips_ordenado_por_volume(self):
        for _ in range(8):
            self.ev(auditoria.LOGIN_FAILED, email="a@a.com", ip=IP_A)
        for _ in range(3):
            self.ev(auditoria.RBAC_DENIED, user_id=CLIENTE, ip=IP_B)
        _, corpo = self.resumo()
        ips = corpo["top_ips"]
        self.assertEqual([(i["ip_address"], i["total"]) for i in ips], [(IP_A, 8), (IP_B, 3)])
        self.assertEqual((ips[0]["login_failed"], ips[1]["access_denied"]), (8, 3))
        self.assertIsNotNone(ips[0]["last_event_at"])
        self.assertLessEqual(len(ips), auditoria.LIMITE_RANKING)

    def test_PS14_eventos_normais_nao_entram_no_ranking_de_ip(self):
        for _ in range(5):
            self.ev(auditoria.LOGIN_SUCCESS, user_id=GERENTE, ip=IP_OK, sev=auditoria.SEV_INFO)
            self.ev(auditoria.PROJECT_ACCESS, user_id=GERENTE, project_id=A1, ip=IP_OK, sev=auditoria.SEV_INFO)
            self.ev(auditoria.PROJECT_ACCESS_NOTIFICATION_SENT, user_id=GERENTE, project_id=A1, ip=IP_OK, sev=auditoria.SEV_INFO)
        self.ev(auditoria.TOKEN_EXPIRED, ip=IP_B)
        _, corpo = self.resumo()
        self.assertEqual([i["ip_address"] for i in corpo["top_ips"]], [IP_B])
        self.assertEqual([p["login_failed"] + p["access_denied"] + p["cross_tenant"] for p in corpo["timeline"]], [0])

    def test_PS15_top_contas_agrupa_attempted_email(self):
        self.semear()
        _, corpo = self.resumo()
        contas = corpo["top_attempted_accounts"]
        self.assertEqual([(c["attempted_email"], c["total"]) for c in contas][:1], [("alvo@a.com", 2)])
        self.assertEqual({c["attempted_email"] for c in contas}, {"alvo@a.com", "outro@a.com", "alvo@b.com", "ninguem@x.com"})
        self.assertNotIn("senha", str(corpo))

    # --- PS16..PS19: filtros ---------------------------------------------------
    def test_PS16_periodo_exclui_eventos_fora_da_janela(self):
        self.ev(auditoria.LOGIN_FAILED, email="a@a.com")
        self.ev(auditoria.LOGIN_FAILED, email="a@a.com")
        self.envelhecer(60 * 24 * 30, tipo=auditoria.LOGIN_FAILED)   # 30 dias atras
        self.ev(auditoria.LOGIN_FAILED, email="b@a.com")
        _, corpo = self.resumo()   # default: 24h
        self.assertEqual(corpo["totais"]["login_failed"], 1)
        agora = datetime.now(timezone.utc)
        _, corpo = self.resumo(data_inicio=(agora - timedelta(days=31)).isoformat(), data_fim=agora.isoformat())
        self.assertEqual((corpo["totais"]["login_failed"], corpo["granularidade"]), (3, "dia"))
        _, corpo = self.resumo(data_inicio=(agora - timedelta(days=31)).isoformat(),
                               data_fim=(agora - timedelta(days=29)).isoformat())
        self.assertEqual(corpo["totais"]["login_failed"], 2)

    def test_PS17_filtro_por_projeto(self):
        self.semear()
        _, corpo = self.resumo(project_id=A1)
        self.assertEqual((corpo["totais"]["access_denied"], corpo["totais"]["project_access"]), (2, 1))

    def test_PS18_filtro_por_usuario_e_tenant_do_gerente_prevalece(self):
        self.semear()
        _, corpo = self.resumo(user_id=GERENTE)
        self.assertEqual((corpo["totais"]["access_denied"], corpo["totais"]["cross_tenant"], corpo["totais"]["project_access"]), (1, 1, 1))
        # Gerente da A pedindo user_id de outro tenant: o filtro de tenant
        # continua imposto -- zero eventos da B, tanto no resumo quanto na lista.
        self.ev(auditoria.RBAC_DENIED, user_id=GERENTE_B, company_id=EMPRESA_B, ip=IP_B)
        self.como(GERENTE, "Gerente")
        _, corpo = self.resumo(user_id=GERENTE_B)
        self.assertEqual(corpo["totais"]["total_eventos"], 0)
        r = self.client.get("/admin/auditoria/eventos", params={"user_id": GERENTE_B})
        self.assertEqual((r.status_code, r.json()["total"]), (200, 0))

    def test_PS19_gerente_nao_recebe_eventos_globais_sem_company_id(self):
        self.ev(auditoria.LOGIN_FAILED, email="ninguem@x.com", company_id=None)
        self.ev(auditoria.TOKEN_INVALID, company_id=None, ip=IP_B)
        self.como(GERENTE, "Gerente")
        _, corpo = self.resumo()
        self.assertEqual(corpo["totais"]["total_eventos"], 0)
        self.assertEqual((corpo["top_ips"], corpo["top_attempted_accounts"], corpo["timeline"]), ([], [], []))
        self.como(SUPER, "Superadmin")
        self.assertEqual(self.resumo()[1]["totais"]["total_eventos"], 2)

    # --- PS20: agregacao em SQL ----------------------------------------------
    def test_PS20_resumo_agrega_em_sql_sem_carregar_eventos(self):
        self.semear()
        from pesquisa360.db import models

        instanciados = []
        original = models.AuditEvent.__init__

        def espiao(self_, *a, **k):
            instanciados.append(1)
            return original(self_, *a, **k)

        with patch.object(models.AuditEvent, "__init__", espiao):
            status, corpo = self.resumo()
        self.assertEqual(status, 200)
        self.assertEqual(instanciados, [], "resumo nao deve materializar AuditEvent")
        # E o painel so le: nenhum evento novo foi gravado pela consulta.
        self.assertEqual(len(self.eventos()), 16)

    def test_PS21_resumo_e_somente_leitura_para_gerente_e_superadmin(self):
        rota = next(x for x in self.app.routes if getattr(x, "path", "") == "/admin/auditoria/resumo")
        self.assertEqual(rota.methods, {"GET"})
        self.assertIn("require_manager_or_superadmin", str(rota.dependant.dependencies))

    # --- PS22: QA com eventos reais, gerados pela API (Fase 28) ----------------
    def test_PS22_qa_cinco_eventos_controlados_aparecem_na_categoria_certa(self):
        """1 login invalido, 1 operacao 403, 1 tentativa cross-tenant, 1 acesso
        legitimo ao projeto e 1 falha simulada de notificacao -- todos por HTTP,
        com o middleware real. Depois o painel (resumo + lista) e conferido."""
        # 1. login invalido (sem usuario logado)
        self.assertEqual(self.client.post("/login/token", data={"username": "gerente@qa.com", "password": "errada1"}).status_code, 401)
        # 2. operacao 403: Cliente com ACL faz PATCH
        self.como(CLIENTE, "Cliente")
        self.assertEqual(self.client.patch(f"/projetos/{A1}", json={"nome": "x"}).status_code, 403)
        # 3. cross-tenant: Gerente da A pede projeto da B
        self.como(GERENTE, "Gerente")
        self.assertEqual(self.client.get(f"/projetos/{B1}").status_code, 404)
        # 4+5. acesso legitimo do Cliente ao A1 com SMTP recusando -> PROJECT_ACCESS + NOTIFICATION_FAILED
        self.como(CLIENTE, "Cliente")
        import os
        with patch.dict(os.environ, {"SMTP_HOST": "smtp.qa.local"}), \
             patch("pesquisa360.services.email.enviar_email", return_value=False):
            self.assertEqual(self.client.get(f"/projetos/{A1}").status_code, 200)

        self.como(SUPER, "Superadmin")
        status, corpo = self.resumo()
        self.assertEqual(status, 200, corpo)
        self.assertEqual(corpo["totais"], {
            "total_eventos": 5, "login_failed": 1, "access_denied": 1, "cross_tenant": 1,
            "project_access": 1, "notification_failed": 1, "high": 1,
        })
        self.assertEqual([c["attempted_email"] for c in corpo["top_attempted_accounts"]], ["gerente@qa.com"])
        self.assertEqual(corpo["top_ips"][0]["total"], 3, "login falho + 403 + cross-tenant; acesso normal fora")
        r = self.client.get("/admin/auditoria/eventos")
        tipos = [e["event_type"] for e in r.json()["items"]]
        self.assertEqual(tipos, [
            auditoria.PROJECT_ACCESS_NOTIFICATION_FAILED, auditoria.PROJECT_ACCESS,
            auditoria.CROSS_TENANT_ACCESS_ATTEMPT, auditoria.RBAC_DENIED, auditoria.LOGIN_FAILED,
        ], "mais recente primeiro")
        # Ver o painel nao gerou evento novo.
        self.assertEqual(self.resumo()[1]["totais"]["total_eventos"], 5)
        # Gerente da A ve os 4 do tenant; o LOGIN_FAILED tem company_id (usuario existe) e tambem entra.
        self.como(GERENTE, "Gerente")
        self.assertEqual(self.resumo()[1]["totais"]["total_eventos"], 5)
        self.como(GERENTE_B, "Gerente", company_id=EMPRESA_B)
        self.assertEqual(self.resumo()[1]["totais"]["total_eventos"], 0)



for _nome in [n for n in dir(AuditoriaTests) if n.startswith("test_")]:
    setattr(PainelSegurancaTests, _nome, None)


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(PainelSegurancaTests)


if __name__ == "__main__":
    unittest.main()
