"""ADR-040 -- notificacao ao Gerente responsavel por acesso ao projeto.

A notificacao e consequencia de um PROJECT_ACCESS PERSISTIDO (ADR-039): a
deduplicacao de 15 min do evento e a unica que existe. Aqui o SMTP e um mock
com contador real -- e tudo passa por HTTP com o middleware de auditoria.

Reaproveita o setup (SQLite migrado, usuarios, projetos, ACL) de
`AuditoriaTests`; os testes herdados sao desligados para nao rodar em dobro.
"""
import os
import unittest
from unittest.mock import patch

from pesquisa360.services import auditoria, notificacoes
from tests.test_auditoria import (
    A1, A2, B1, AGENTE, CLIENTE, COORD, EMPRESA_A, EMPRESA_B, GERENTE, GERENTE_B, SUPER, SUPERV,
    AuditoriaTests,
)
from sqlalchemy import text

SENT, FAILED, SUPPRESSED = (
    auditoria.PROJECT_ACCESS_NOTIFICATION_SENT,
    auditoria.PROJECT_ACCESS_NOTIFICATION_FAILED,
    auditoria.PROJECT_ACCESS_NOTIFICATION_SUPPRESSED,
)


class NotificacaoAcessoProjetoTests(AuditoriaTests):
    def setUp(self):
        super().setUp()
        # SMTP "configurado" (senao a regra e SUPPRESSED smtp_not_configured) e
        # envio mockado com contador real. Nada sai de verdade.
        self.env = patch.dict(os.environ, {"SMTP_HOST": "smtp.qa.local", "WEB_BASE_URL": "https://web.qa.local"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.enviados = []
        self.smtp = patch("pesquisa360.services.email.enviar_email", side_effect=self._enviar)
        self.smtp.start()
        self.addCleanup(self.smtp.stop)
        # Por padrao o ator e o Cliente (ACL so em A1); o Gerente 1 e o
        # responsavel (coordenador_id) de A1, A2 e B1 no seed.
        self.como(CLIENTE, "Cliente")

    def _enviar(self, destinatario, assunto, corpo):
        self.enviados.append((destinatario, assunto, corpo))
        return True

    def resultados(self):
        return [(e.event_type, (e.details or {}).get("reason"), (e.details or {}).get("recipient_user_id"))
                for e in self.eventos() if e.event_type in (SENT, FAILED, SUPPRESSED)]

    def get(self, projeto_id, esperado=200):
        r = self.client.get(f"/projetos/{projeto_id}")
        self.assertEqual(r.status_code, esperado, r.text)
        return r

    # --- evento -> e-mail ------------------------------------------------------
    def test_NP01_acesso_autorizado_gera_um_email_ao_gerente_responsavel(self):
        self.get(A1)
        self.assertEqual(len(self.eventos(auditoria.PROJECT_ACCESS)), 1)
        self.assertEqual(len(self.enviados), 1)
        self.assertEqual(self.enviados[0][0], "gerente@qa.com")
        self.assertEqual(self.resultados(), [(SENT, None, GERENTE)])

    def test_NP02_repeticao_na_janela_nao_gera_segundo_email(self):
        self.get(A1)
        self.get(A1)
        self.assertEqual(len(self.eventos(auditoria.PROJECT_ACCESS)), 1)
        self.assertEqual(len(self.enviados), 1)
        self.assertEqual(len(self.resultados()), 1)

    def test_NP03_fora_da_janela_gera_novo_email(self):
        self.get(A1)
        self.envelhecer(16)
        self.get(A1)
        self.assertEqual(len(self.eventos(auditoria.PROJECT_ACCESS)), 2)
        self.assertEqual(len(self.enviados), 2)

    def test_NP04_usuario_diferente_gera_email_proprio(self):
        self.get(A1)
        self.como(SUPERV, "Supervisor")
        with self.engine.begin() as c:   # Supervisor: acesso a toda a empresa A
            c.execute(text(f"INSERT INTO usuario_empresa_acessos (usuario_id,company_id,acesso_todos_projetos,ativo,principal) VALUES ({SUPERV},{EMPRESA_A},1,1,1)"))
        self.get(A1)
        self.assertEqual(len(self.enviados), 2)
        self.assertEqual([e.user_id for e in self.eventos(auditoria.PROJECT_ACCESS)], [CLIENTE, SUPERV])

    def test_NP05_projeto_diferente_resolve_destinatario_correto(self):
        with self.engine.begin() as c:   # A2 passa a ser do Gerente B (mesmo tenant nao importa para o teste de destinatario)
            c.execute(text(f"UPDATE projetos SET coordenador_id={GERENTE_B} WHERE id={A2}"))
            c.execute(text(f"INSERT INTO usuario_projeto_acessos (usuario_id,projeto_id,ativo) VALUES ({CLIENTE},{A2},1)"))
        self.get(A1)
        self.get(A2)
        self.assertEqual([d for d, _, _ in self.enviados], ["gerente@qa.com", "gerente@b.com"])

    # --- casos especiais -------------------------------------------------------
    def test_NP06_gerente_responsavel_acessa_o_proprio_projeto_e_suprimido(self):
        self.como(GERENTE, "Gerente")
        self.get(A1)
        self.assertEqual(len(self.eventos(auditoria.PROJECT_ACCESS)), 1, "auditoria continua")
        self.assertEqual(self.enviados, [])
        self.assertEqual(self.resultados(), [(SUPPRESSED, "self_access", GERENTE)])

    def test_NP07_superadmin_acessando_notifica_o_gerente_responsavel(self):
        self.como(SUPER, "Superadmin")
        self.get(B1)
        self.assertEqual(len(self.enviados), 1)
        self.assertEqual(self.enviados[0][0], "gerente@qa.com")
        (ev,) = [e for e in self.eventos(SENT)]
        self.assertEqual((ev.user_id, ev.project_id, ev.company_id), (SUPER, B1, EMPRESA_B))

    def test_NP08_projeto_sem_responsavel_nao_quebra_e_suprime(self):
        # No modelo real `projetos.coordenador_id` e NOT NULL com FK: um projeto
        # sem responsavel nao existe via API (e `schemas.Projeto` exige
        # `coordenador`). O servico ainda assim trata o caso sem levantar.
        from types import SimpleNamespace

        projeto = SimpleNamespace(id=A1, nome="A1", company_id=EMPRESA_A, company=None, coordenador=None)
        resultado = notificacoes.notificar_acesso_projeto(self.atual, projeto)
        self.assertEqual((resultado.status, resultado.reason), ("SUPPRESSED", "no_project_manager"))
        self.assertEqual(self.enviados, [])
        self.assertEqual(self.resultados(), [(SUPPRESSED, "no_project_manager", None)])

    def test_NP09_responsavel_inativo_suprime(self):
        with self.engine.begin() as c:
            c.execute(text(f"UPDATE usuarios SET ativo=0 WHERE id={GERENTE}"))
        self.get(A1)
        self.assertEqual(self.enviados, [])
        self.assertEqual(self.resultados(), [(SUPPRESSED, "inactive_recipient", GERENTE)])

    def test_NP09b_responsavel_sem_email_suprime(self):
        # `usuarios.email` e NOT NULL e validado no cadastro; o servico cobre o
        # caso defensivamente, sem 500 e sem destinatario aleatorio.
        from types import SimpleNamespace

        responsavel = SimpleNamespace(id=GERENTE, nome="Gerente", email="sem-arroba", ativo=True)
        projeto = SimpleNamespace(id=A1, nome="A1", company_id=EMPRESA_A, company=None, coordenador=responsavel)
        resultado = notificacoes.notificar_acesso_projeto(self.atual, projeto)
        self.assertEqual((resultado.status, resultado.reason), ("SUPPRESSED", "missing_recipient_email"))
        self.assertEqual(self.enviados, [])
        self.assertEqual(self.resultados(), [(SUPPRESSED, "missing_recipient_email", GERENTE)])

    # --- SMTP ------------------------------------------------------------------
    def test_NP10_smtp_disponivel_registra_SENT(self):
        self.get(A1)
        (ev,) = self.eventos(SENT)
        self.assertEqual((ev.severity, ev.user_id, ev.project_id, ev.company_id), ("INFO", CLIENTE, A1, EMPRESA_A))
        self.assertEqual(ev.details, {"recipient_user_id": GERENTE})

    def test_NP11_smtp_falha_nao_bloqueia_o_acesso(self):
        self.smtp.stop()
        with patch("pesquisa360.services.email.enviar_email", return_value=False):
            self.get(A1)
        self.assertEqual(len(self.eventos(auditoria.PROJECT_ACCESS)), 1, "evento persistido")
        (ev,) = self.eventos(FAILED)
        self.assertEqual((ev.severity, ev.details["reason"]), ("WARNING", "smtp_error"))
        # Excecao inesperada no envio tambem nao vira 500.
        self.envelhecer(16)
        with patch("pesquisa360.services.email.enviar_email", side_effect=RuntimeError("boom")):
            self.get(A1)
        self.assertEqual(self.eventos(FAILED)[-1].details["reason"], "exception:RuntimeError")
        self.smtp.start()

    def test_NP11b_smtp_nao_configurado_e_supressao_e_nao_falha(self):
        with patch.dict(os.environ, {"SMTP_HOST": ""}):
            self.get(A1)
        self.assertEqual(self.enviados, [])
        self.assertEqual(self.resultados(), [(SUPPRESSED, "smtp_not_configured", GERENTE)])

    # --- segredos --------------------------------------------------------------
    def test_NP12_email_nao_contem_segredo(self):
        JWT = "SECRET_JWT_MARKER"
        self.client.headers["Authorization"] = f"Bearer {JWT}"
        self.get(A1)
        _, assunto, corpo = self.enviados[0]
        for segredo in (JWT, "Bearer", "Authorization", "senha", "token", "activation", "senha_hash", "cookie"):
            self.assertNotIn(segredo, assunto + corpo, segredo)
        self.assertNotIn("Resposta", corpo)   # nunca dado de coleta/entrevistado

    # --- tenant / ACL / RBAC ---------------------------------------------------
    def test_NP13_cross_tenant_404_nao_notifica(self):
        self.como(GERENTE, "Gerente")
        self.get(B1, esperado=404)
        self.assertEqual([e.event_type for e in self.eventos()], [auditoria.CROSS_TENANT_ACCESS_ATTEMPT])
        self.assertEqual(self.enviados, [])

    def test_NP14_sem_acl_404_nao_notifica(self):
        self.get(A2, esperado=404)   # Cliente so tem ACL em A1
        self.assertEqual([e.event_type for e in self.eventos()], [auditoria.ACCESS_DENIED])
        self.assertEqual(self.enviados, [])

    def test_NP15_rbac_403_nao_notifica(self):
        r = self.client.patch(f"/projetos/{A1}", json={"nome": "x"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual([e.event_type for e in self.eventos()], [auditoria.RBAC_DENIED])
        self.assertEqual(self.enviados, [])

    # --- destinatario correto ----------------------------------------------------
    def test_NP16_somente_o_gerente_responsavel_recebe(self):
        with self.engine.begin() as c:   # Gerente B passa a ser da empresa A: dois Gerentes no tenant
            c.execute(text(f"UPDATE usuarios SET company_id={EMPRESA_A} WHERE id={GERENTE_B}"))
            c.execute(text(f"UPDATE usuario_empresa_acessos SET company_id={EMPRESA_A} WHERE usuario_id={GERENTE_B}"))
        self.get(A1)
        destinatarios = [d for d, _, _ in self.enviados]
        self.assertEqual(destinatarios.count("gerente@qa.com"), 1)
        self.assertEqual(destinatarios.count("gerente@b.com"), 0)
        self.assertEqual(len(destinatarios), 1)

    # --- conteudo ----------------------------------------------------------------
    def test_NP17_conteudo_minimo_do_email(self):
        self.client.headers["User-Agent"] = "QA-Browser/2.0 (Windows)"
        self.get(A1)
        destinatario, assunto, corpo = self.enviados[0]
        self.assertEqual(assunto, "Acesso ao projeto — A1")
        for trecho in ("Olá, Gerente.", "Projeto: A1", "Cliente", "CLIENTE", "Data e hora:", "(UTC)",
                       "IP:", "testclient", "QA-Browser/2.0 (Windows)", "Empresa (tenant do projeto):", "A",
                       "https://web.qa.local/projetos/101", "aviso automático de segurança"):
            self.assertIn(trecho, corpo, trecho)
        self.assertNotIn("/api", corpo)

    # --- duplicidade -------------------------------------------------------------
    def test_NP18_dez_gets_um_evento_um_email(self):
        for _ in range(10):
            self.get(A1)
        self.assertEqual(len(self.eventos(auditoria.PROJECT_ACCESS)), 1)
        self.assertEqual(len(self.enviados), 1)
        self.assertEqual(len(self.resultados()), 1)

    # --- unidade: montar_mensagem e pura -----------------------------------------
    def test_NP19_montar_mensagem_formata_utc_e_trunca_user_agent(self):
        from datetime import datetime, timezone

        assunto, corpo = notificacoes.montar_mensagem(
            nome_destinatario="G", nome_projeto="P", projeto_id=7, nome_ator="U", perfil_ator="CLIENTE",
            nome_empresa="E", momento=datetime(2026, 8, 28, 13, 5, tzinfo=timezone.utc),
            ip_address="10.0.0.1", user_agent="X" * 500,
        )
        self.assertIn("28/08/2026 13:05 (UTC)", corpo)
        self.assertIn("X" * 160 + "...", corpo)
        self.assertNotIn("X" * 161, corpo)


# Os testes AU* ja rodam em tests/test_auditoria.py: aqui so o setup e
# reaproveitado. Os metodos herdados sao desligados e `load_tests` impede o
# loader de coletar tambem a classe importada.
for _nome in [n for n in dir(AuditoriaTests) if n.startswith("test_")]:
    setattr(NotificacaoAcessoProjetoTests, _nome, None)


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(NotificacaoAcessoProjetoTests)


if __name__ == "__main__":
    unittest.main()
