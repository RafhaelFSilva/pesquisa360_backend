"""ADR-039 -- auditoria central de seguranca e acesso.

Cada evento nasce no ponto do Backend onde a decisao e tomada, em sessao
propria (sobrevive ao rollback de um 403/404), sem segredo nenhum e sem nunca
derrubar a request. Tudo aqui e exercitado por HTTP, com o middleware real.
"""
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-auditoria-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.api.endpoints import login as rotas_login
from pesquisa360.api.endpoints import projetos as rotas_projetos
from pesquisa360.api.endpoints import usuarios as rotas_usuarios
from pesquisa360.core import security
from pesquisa360.core.dependencies import get_current_user, get_db, require_manager_or_superadmin, require_superadmin
from pesquisa360.db import models
from pesquisa360.services import ativacao, auditoria
from tests.test_base_eleitoral_import import run_alembic_upgrade

EMPRESA_A, EMPRESA_B = 10, 20
A1, A2, B1 = 101, 102, 201
GERENTE, CLIENTE, SUPER, INATIVO = 1, 5, 9, 7
GERENTE_B, AGENTE, COORD, SUPERV, CONVIDADO = 21, 22, 23, 24, 30
PERFIS = {"Gerente": 1, "Cliente": 5, "Superadmin": 3, "Agente": 2, "Coordenador": 4, "Supervisor": 6}


def usuario(user_id, perfil, company_id=EMPRESA_A, ativo=True):
    return SimpleNamespace(
        id=user_id, email=f"u{user_id}@qa.com", nome=perfil, ativo=ativo, company_id=company_id,
        perfil_id=PERFIS[perfil], perfil=SimpleNamespace(nome=perfil), perfil_nome=perfil,
    )


class AuditoriaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._dir = TemporaryDirectory()
        cls.db_path = Path(cls._dir.name) / "aud.sqlite"
        run_alembic_upgrade(f"sqlite:///{cls.db_path.as_posix()}")

    @classmethod
    def tearDownClass(cls):
        auditoria.configurar_sessao(None)
        cls._dir.cleanup()

    def setUp(self):
        self.engine = create_engine(f"sqlite:///{self.db_path.as_posix()}")

        @event.listens_for(self.engine, "connect")
        def _stubs(conexao, _):
            for nome in ("AsEWKB", "ST_AsEWKB", "ST_AsGeoJSON", "AsGeoJSON", "ST_X", "ST_Y"):
                conexao.create_function(nome, 1, lambda v: None)
            conexao.create_function("GeomFromEWKT", 1, lambda v: v)

        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        # A auditoria grava na PROPRIA sessao, no mesmo banco dos testes.
        auditoria.configurar_sessao(self.Session)
        with self.engine.begin() as c:
            for t in ("audit_events", "user_activation_tokens", "usuario_projeto_acessos", "usuario_empresa_acessos",
                      "pesquisas", "projetos", "usuarios", "perfis", "companies"):
                c.execute(text(f"DELETE FROM {t}"))
            c.execute(text(f"INSERT INTO companies (id,name,is_active,created_at) VALUES ({EMPRESA_A},'A',1,'2026-01-01'),({EMPRESA_B},'B',1,'2026-01-01')"))
            for nome, pid in PERFIS.items():
                c.execute(text(f"INSERT INTO perfis (id,nome) VALUES ({pid},'{nome}')"))
            senha = security.get_password_hash("segredo1")
            c.execute(text(
                "INSERT INTO usuarios (id,email,nome,senha_hash,ativo,perfil_id,company_id) VALUES "
                f"({GERENTE},'gerente@qa.com','Gerente','{senha}',1,1,{EMPRESA_A}),"
                f"({CLIENTE},'cliente@qa.com','Cliente','{senha}',1,5,{EMPRESA_A}),"
                f"({SUPER},'root@qa.com','Root','{senha}',1,3,{EMPRESA_A}),"
                f"({INATIVO},'inativo@qa.com','Inativo','{senha}',0,1,{EMPRESA_A}),"
                f"({GERENTE_B},'gerente@b.com','GerenteB','{senha}',1,1,{EMPRESA_B}),"
                f"({AGENTE},'agente@qa.com','Agente','{senha}',1,2,{EMPRESA_A}),"
                f"({COORD},'coord@qa.com','Coord','{senha}',1,4,{EMPRESA_A}),"
                f"({SUPERV},'superv@qa.com','Superv','{senha}',1,6,{EMPRESA_A}),"
                f"({CONVIDADO},'convidado@qa.com','Convidado','',0,5,{EMPRESA_A})"))
            c.execute(text(
                "INSERT INTO projetos (id,nome,status,coordenador_id,company_id) VALUES "
                f"({A1},'A1','Ativo',1,{EMPRESA_A}),({A2},'A2','Ativo',1,{EMPRESA_A}),({B1},'B1','Ativo',1,{EMPRESA_B})"))
            # Gerente: tudo da A. Cliente: so A1.
            c.execute(text(f"INSERT INTO usuario_empresa_acessos (usuario_id,company_id,acesso_todos_projetos,ativo,principal) VALUES ({GERENTE},{EMPRESA_A},1,1,1),({CLIENTE},{EMPRESA_A},0,1,1),({INATIVO},{EMPRESA_A},1,1,1),({GERENTE_B},{EMPRESA_B},1,1,1)"))
            c.execute(text(f"INSERT INTO usuario_projeto_acessos (usuario_id,projeto_id,ativo) VALUES ({CLIENTE},{A1},1)"))
        self.db = self.Session()
        self.addCleanup(self.db.close)

        # App com o MESMO middleware do main.py (copiado para nao importar main,
        # que exige UPLOAD_DIRECTORY e monta static).
        import uuid

        self.app = FastAPI()

        @self.app.middleware("http")
        async def contexto(request: Request, call_next):
            rid = uuid.uuid4().hex
            token = auditoria.definir_contexto(auditoria.contexto_de_request(request, rid))
            try:
                resposta = await call_next(request)
            finally:
                auditoria.limpar_contexto(token)
            resposta.headers["X-Request-ID"] = rid
            return resposta

        self.app.include_router(rotas_login.router, prefix="/login")
        self.app.include_router(rotas_projetos.router)
        self.app.include_router(rotas_usuarios.admin_router)
        self.app.include_router(rotas_usuarios.auditoria_router)
        self.app.include_router(rotas_usuarios.router, prefix="/usuarios")
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.atual = usuario(GERENTE, "Gerente")
        self.app.dependency_overrides[get_current_user] = lambda: self.atual
        self.app.dependency_overrides[require_superadmin] = lambda: self.atual
        self.client = TestClient(self.app, raise_server_exceptions=False, headers={"User-Agent": "QA-Agent/1.0"})
        self.addCleanup(self.client.close)

    # --- helpers ---------------------------------------------------------
    def eventos(self, tipo=None):
        q = self.db.query(models.AuditEvent).order_by(models.AuditEvent.id)
        if tipo:
            q = q.filter(models.AuditEvent.event_type == tipo)
        self.db.expire_all()
        return q.all()

    def como(self, user_id, perfil, company_id=EMPRESA_A):
        self.atual = usuario(user_id, perfil, company_id)

    # --- login -----------------------------------------------------------
    def test_AU01_login_invalido_e_registrado_com_email_tentado_e_sem_senha(self):
        r = self.client.post("/login/token", data={"username": "ninguem@qa.com", "password": "errada"})
        self.assertEqual(r.status_code, 401)
        (ev,) = self.eventos(auditoria.LOGIN_FAILED)
        self.assertEqual(ev.attempted_email, "ninguem@qa.com")
        self.assertIsNone(ev.user_id)
        self.assertEqual(ev.details["motivo"], "usuario_inexistente")
        self.assertEqual(ev.severity, auditoria.SEV_WARNING)
        self.assertEqual(ev.status_code, 401)
        self.assertEqual((ev.http_method, ev.path), ("POST", "/login/token"))
        self.assertNotIn("errada", str(ev.details))
        # Senha errada de usuario existente: motivo distinto, user_id preenchido.
        self.client.post("/login/token", data={"username": "gerente@qa.com", "password": "errada"})
        ev2 = self.eventos(auditoria.LOGIN_FAILED)[-1]
        self.assertEqual((ev2.user_id, ev2.details["motivo"]), (GERENTE, "senha_invalida"))

    def test_AU02_conta_inativa_e_login_valido(self):
        r = self.client.post("/login/token", data={"username": "inativo@qa.com", "password": "segredo1"})
        self.assertEqual(r.status_code, 401)
        (ev,) = self.eventos(auditoria.ACCOUNT_INACTIVE_LOGIN)
        self.assertEqual((ev.user_id, ev.severity, ev.details["motivo"]), (INATIVO, "WARNING", "conta_inativa"))
        self.assertEqual(self.eventos(auditoria.LOGIN_FAILED), [])
        self.assertEqual(r.json()["detail"], "E-mail ou senha incorretos")  # externo inalterado

        r = self.client.post("/login/token", data={"username": "gerente@qa.com", "password": "segredo1"})
        self.assertEqual(r.status_code, 200, r.text)
        (ok,) = self.eventos(auditoria.LOGIN_SUCCESS)
        self.assertEqual((ok.user_id, ok.company_id, ok.status_code), (GERENTE, EMPRESA_A, 200))
        # O cliente continua vendo a mensagem generica: motivo so na trilha.
        self.assertEqual(r.json().get("token_type"), "bearer")

    # --- contexto HTTP ---------------------------------------------------
    def test_AU03_ip_user_agent_e_request_id_vem_do_middleware(self):
        r = self.client.post("/login/token", data={"username": "x@qa.com", "password": "y"})
        rid = r.headers.get("X-Request-ID")
        self.assertTrue(rid and len(rid) == 32)
        (ev,) = self.eventos(auditoria.LOGIN_FAILED)
        self.assertEqual(ev.request_id, rid, "request_id do evento == header da resposta")
        self.assertEqual(ev.user_agent, "QA-Agent/1.0")
        self.assertEqual(ev.ip_address, "testclient")

    def test_AU04_x_forwarded_for_so_vale_com_proxy_confiavel(self):
        cabecalho = {"X-Forwarded-For": "203.0.113.9, 10.0.0.1"}
        self.client.post("/login/token", data={"username": "a@qa.com", "password": "b"}, headers=cabecalho)
        self.assertEqual(self.eventos()[-1].ip_address, "testclient", "sem proxy declarado o header e ignorado")
        with patch.dict(os.environ, {"AUDIT_TRUST_PROXY": "true"}):
            self.client.post("/login/token", data={"username": "a@qa.com", "password": "b"}, headers=cabecalho)
        self.assertEqual(self.eventos()[-1].ip_address, "203.0.113.9", "primeiro IP da cadeia")

    # --- RBAC / ACL ------------------------------------------------------
    def test_AU05_rbac_negado_registra_permissao_exigida_e_papel(self):
        self.como(CLIENTE, "Cliente")
        r = self.client.patch(f"/projetos/{A1}", json={"nome": "x"})
        self.assertEqual(r.status_code, 403)
        (ev,) = self.eventos(auditoria.RBAC_DENIED)
        self.assertEqual((ev.user_id, ev.status_code), (CLIENTE, 403))
        self.assertEqual(ev.details["permissoes_exigidas"], ["PROJETO_EDITAR"])
        self.assertEqual(ev.details["papel"], "CLIENTE")
        self.assertEqual((ev.http_method, ev.path), ("PATCH", f"/projetos/{A1}"))

    def test_AU06_negacao_de_projeto_e_classificada_internamente(self):
        self.como(CLIENTE, "Cliente")
        # Mesmo tenant, sem ACL.
        self.assertEqual(self.client.get(f"/projetos/{A2}").status_code, 404)
        ev = self.eventos()[-1]
        self.assertEqual(ev.event_type, auditoria.ACCESS_DENIED)
        self.assertEqual(ev.details["motivo"], "sem_acl")
        self.assertEqual(ev.project_id, A2)
        # Outro tenant: severidade alta e tipo proprio.
        self.assertEqual(self.client.get(f"/projetos/{B1}").status_code, 404)
        ev = self.eventos()[-1]
        self.assertEqual(ev.event_type, auditoria.CROSS_TENANT_ACCESS_ATTEMPT)
        self.assertEqual(ev.severity, auditoria.SEV_HIGH)
        self.assertEqual(ev.project_id, B1)
        # Inexistente: sem project_id (nao ha FK para apontar).
        self.assertEqual(self.client.get("/projetos/999999").status_code, 404)
        ev = self.eventos()[-1]
        self.assertEqual((ev.event_type, ev.details["motivo"], ev.project_id), (auditoria.ACCESS_DENIED, "inexistente", None))

    def test_AU07_acesso_ao_projeto_e_registrado_no_backend(self):
        self.como(CLIENTE, "Cliente")
        self.assertEqual(self.client.get(f"/projetos/{A1}").status_code, 200)
        (ev,) = self.eventos(auditoria.PROJECT_ACCESS)
        self.assertEqual((ev.user_id, ev.project_id, ev.company_id, ev.status_code), (CLIENTE, A1, EMPRESA_A, 200))
        self.assertEqual(ev.http_method, "GET")
        # Nenhum evento veio de sinal do Frontend: a rota e a do proprio Backend.

    def test_AU08_evento_sobrevive_ao_rollback_da_request(self):
        """O 403 aborta a transacao da request; a trilha usa sessao propria."""
        self.como(CLIENTE, "Cliente")
        self.client.patch(f"/projetos/{A1}", json={"nome": "x"})
        outra = self.Session()
        try:
            self.assertEqual(outra.query(models.AuditEvent).filter_by(event_type=auditoria.RBAC_DENIED).count(), 1)
        finally:
            outra.close()

    # --- robustez --------------------------------------------------------
    def test_AU09_falha_na_auditoria_nunca_derruba_a_request(self):
        with patch.object(auditoria, "_abrir_sessao", side_effect=RuntimeError("banco de auditoria fora")):
            r = self.client.post("/login/token", data={"username": "gerente@qa.com", "password": "segredo1"})
        self.assertEqual(r.status_code, 200, "login segue funcionando sem auditoria")
        self.assertEqual(self.eventos(auditoria.LOGIN_SUCCESS), [])

    def test_AU10_details_nunca_carrega_segredo(self):
        ok = auditoria.registrar(
            "TESTE", auditoria.SEV_INFO,
            details={"senha": "x", "password": "y", "token": "z", "Authorization": "Bearer q",
                     "motivo": "ok", "aninhado": {"refresh_token": "r", "campo": "v"}},
        )
        self.assertTrue(ok)
        ev = self.eventos("TESTE")[-1]
        self.assertEqual(ev.details, {"motivo": "ok", "aninhado": {"campo": "v"}})
        for proibido in ("senha", "password", "token", "Authorization"):
            self.assertNotIn(proibido, str(ev.details))

    def test_AU11_registro_fora_de_request_funciona_sem_contexto(self):
        self.assertTrue(auditoria.registrar(auditoria.ACL_CHANGED, user_id=GERENTE, company_id=EMPRESA_A))
        ev = self.eventos(auditoria.ACL_CHANGED)[-1]
        self.assertIsNone(ev.request_id)
        self.assertIsNone(ev.ip_address)
        self.assertEqual(ev.user_id, GERENTE)

    # --- leitura ---------------------------------------------------------
    def test_AU12_leitura_e_so_do_superadmin_e_filtra(self):
        self.como(CLIENTE, "Cliente")
        self.client.get(f"/projetos/{A1}")
        self.client.get(f"/projetos/{B1}")
        self.como(SUPER, "Superadmin")
        r = self.client.get("/admin/auditoria/eventos")
        self.assertEqual(r.status_code, 200, r.text)
        tipos = {e["event_type"] for e in r.json()["items"]}
        self.assertIn(auditoria.PROJECT_ACCESS, tipos)
        self.assertIn(auditoria.CROSS_TENANT_ACCESS_ATTEMPT, tipos)
        r = self.client.get("/admin/auditoria/eventos", params={"event_type": auditoria.CROSS_TENANT_ACCESS_ATTEMPT})
        self.assertEqual([e["event_type"] for e in r.json()["items"]], [auditoria.CROSS_TENANT_ACCESS_ATTEMPT])
        self.assertEqual(r.json()["items"][0]["project_id"], B1)
        # A rota exige Gerente/Superadmin de verdade: dependency declarada.
        rota = next(x for x in rotas_usuarios.auditoria_router.routes if x.path.endswith("/eventos"))
        self.assertIn("require_manager_or_superadmin", str(rota.dependant.dependencies))

    def test_AU13_trilha_e_append_only_e_sem_cascade(self):
        """FKs nullable, sem ondelete: apagar o usuario nao apaga o rastro."""
        tabela = models.AuditEvent.__table__
        for coluna in ("user_id", "company_id", "project_id"):
            fk = next(iter(tabela.c[coluna].foreign_keys))
            self.assertIsNone(fk.ondelete)
            self.assertTrue(tabela.c[coluna].nullable)
        fonte = open("pesquisa360/services/auditoria.py", encoding="utf-8").read()
        self.assertNotIn(".delete(", fonte)
        self.assertNotIn(".update(", fonte)

    # --- PROJECT_ACCESS: deduplicacao ---------------------------------------
    def envelhecer(self, minutos, tipo=auditoria.PROJECT_ACCESS):
        """Empurra occurred_at para o passado: nao se espera 15 min em teste."""
        with self.engine.begin() as c:
            c.execute(text(f"UPDATE audit_events SET occurred_at = datetime('now','-{minutos} minutes') WHERE event_type='{tipo}'"))

    def test_AU14_get_projeto_autorizado_cria_PROJECT_ACCESS(self):
        self.assertEqual(self.client.get(f"/projetos/{A1}").status_code, 200)
        (ev,) = self.eventos(auditoria.PROJECT_ACCESS)
        self.assertEqual((ev.user_id, ev.project_id, ev.company_id, ev.status_code), (GERENTE, A1, EMPRESA_A, 200))
        self.assertEqual((ev.http_method, ev.path), ("GET", f"/projetos/{A1}"))
        for campo in ("occurred_at", "ip_address", "user_agent", "request_id"):
            self.assertIsNotNone(getattr(ev, campo), campo)

    def test_AU15_repeticao_dentro_de_15_min_nao_duplica(self):
        for _ in range(3):
            self.assertEqual(self.client.get(f"/projetos/{A1}").status_code, 200)
        self.assertEqual(len(self.eventos(auditoria.PROJECT_ACCESS)), 1)

    def test_AU16_fora_da_janela_gera_novo_evento(self):
        self.client.get(f"/projetos/{A1}")
        self.envelhecer(14)
        self.client.get(f"/projetos/{A1}")
        self.assertEqual(len(self.eventos(auditoria.PROJECT_ACCESS)), 1, "14 min: ainda na janela")
        self.envelhecer(16)
        self.client.get(f"/projetos/{A1}")
        self.assertEqual(len(self.eventos(auditoria.PROJECT_ACCESS)), 2, "16 min: janela vencida")

    def test_AU17_usuario_diferente_gera_evento(self):
        self.client.get(f"/projetos/{A1}")
        self.como(CLIENTE, "Cliente")
        self.client.get(f"/projetos/{A1}")
        self.assertEqual([e.user_id for e in self.eventos(auditoria.PROJECT_ACCESS)], [GERENTE, CLIENTE])

    def test_AU18_projeto_diferente_gera_evento(self):
        self.client.get(f"/projetos/{A1}")
        self.client.get(f"/projetos/{A2}")
        self.client.get(f"/projetos/{A1}")
        self.assertEqual([e.project_id for e in self.eventos(auditoria.PROJECT_ACCESS)], [A1, A2])

    def test_AU19_superadmin_em_outro_tenant_registra_tenant_do_projeto(self):
        self.como(SUPER, "Superadmin", company_id=EMPRESA_A)
        self.assertEqual(self.client.get(f"/projetos/{B1}").status_code, 200)
        (ev,) = self.eventos(auditoria.PROJECT_ACCESS)
        self.assertEqual((ev.user_id, ev.project_id, ev.company_id), (SUPER, B1, EMPRESA_B))

    # --- API admin: matriz de acesso ----------------------------------------
    def semear_eventos(self):
        """Um evento em cada tenant + um sem tenant, com IP e horario controlados."""
        ctx_a = auditoria.ContextoRequest(ip_address="10.0.0.1", path="/x", http_method="GET")
        ctx_b = auditoria.ContextoRequest(ip_address="10.0.0.2", path="/y", http_method="GET")
        auditoria.registrar(auditoria.PROJECT_ACCESS, user_id=GERENTE, company_id=EMPRESA_A, project_id=A1, contexto=ctx_a)
        auditoria.registrar(auditoria.RBAC_DENIED, auditoria.SEV_WARNING, user_id=CLIENTE, company_id=EMPRESA_A, contexto=ctx_a)
        auditoria.registrar(auditoria.CROSS_TENANT_ACCESS_ATTEMPT, auditoria.SEV_HIGH, user_id=GERENTE_B, company_id=EMPRESA_B, project_id=A1, contexto=ctx_b)
        auditoria.registrar(auditoria.LOGIN_FAILED, auditoria.SEV_WARNING, attempted_email="x@y.com", contexto=ctx_b)

    def listar(self, **params):
        r = self.client.get("/admin/auditoria/eventos", params=params)
        return r.status_code, (r.json() if r.status_code == 200 else r.text)

    def test_AU20_superadmin_lista_eventos_globais(self):
        self.semear_eventos()
        self.como(SUPER, "Superadmin")
        status, corpo = self.listar()
        self.assertEqual(status, 200)
        self.assertEqual(corpo["total"], 4)
        self.assertEqual({e["company_id"] for e in corpo["items"]}, {EMPRESA_A, EMPRESA_B, None})
        self.assertEqual((corpo["limit"], corpo["offset"]), (50, 0))

    def test_AU21_superadmin_filtra_company_id(self):
        self.semear_eventos()
        self.como(SUPER, "Superadmin")
        _, corpo = self.listar(company_id=EMPRESA_B)
        self.assertEqual([e["company_id"] for e in corpo["items"]], [EMPRESA_B])

    def test_AU22_gerente_lista_so_o_proprio_tenant(self):
        self.semear_eventos()
        status, corpo = self.listar()
        self.assertEqual(status, 200)
        self.assertEqual(corpo["total"], 2)
        self.assertEqual({e["company_id"] for e in corpo["items"]}, {EMPRESA_A})

    def test_AU23_gerente_com_company_id_de_outro_tenant_nao_vaza(self):
        self.semear_eventos()
        for tentativa in (EMPRESA_B, 999, 0):
            status, corpo = self.listar(company_id=tentativa)
            self.assertEqual(status, 200)
            self.assertEqual({e["company_id"] for e in corpo["items"]}, {EMPRESA_A}, tentativa)
            self.assertEqual(corpo["total"], 2)

    def test_AU24_evento_de_outro_tenant_nao_aparece_para_gerente(self):
        self.semear_eventos()
        self.como(GERENTE_B, "Gerente", company_id=EMPRESA_B)
        _, corpo = self.listar()
        self.assertEqual([e["event_type"] for e in corpo["items"]], [auditoria.CROSS_TENANT_ACCESS_ATTEMPT])
        # Evento sem tenant (login de e-mail inexistente) tambem nao: e global.
        self.assertNotIn(auditoria.LOGIN_FAILED, {e["event_type"] for e in corpo["items"]})

    def _403(self, user_id, perfil):
        self.como(user_id, perfil)
        status, _ = self.listar()
        self.assertEqual(status, 403, perfil)

    def test_AU25_coordenador_403(self):
        self._403(COORD, "Coordenador")

    def test_AU26_supervisor_403(self):
        self._403(SUPERV, "Supervisor")

    def test_AU27_cliente_403(self):
        self._403(CLIENTE, "Cliente")

    def test_AU28_agente_403(self):
        self._403(AGENTE, "Agente")

    # --- API admin: filtros -------------------------------------------------
    def test_AU29_filtro_event_type(self):
        self.semear_eventos()
        self.como(SUPER, "Superadmin")
        _, corpo = self.listar(event_type=auditoria.RBAC_DENIED)
        self.assertEqual([e["event_type"] for e in corpo["items"]], [auditoria.RBAC_DENIED])
        self.assertEqual(corpo["total"], 1)

    def test_AU30_filtro_severity(self):
        self.semear_eventos()
        self.como(SUPER, "Superadmin")
        _, corpo = self.listar(severity="HIGH")
        self.assertEqual([e["severity"] for e in corpo["items"]], ["HIGH"])

    def test_AU31_filtro_project_id(self):
        self.semear_eventos()
        self.como(SUPER, "Superadmin")
        _, corpo = self.listar(project_id=A1)
        self.assertEqual(corpo["total"], 2)
        self.assertTrue(all(e["project_id"] == A1 for e in corpo["items"]))

    def test_AU32_filtro_user_id(self):
        self.semear_eventos()
        self.como(SUPER, "Superadmin")
        _, corpo = self.listar(user_id=CLIENTE)
        self.assertEqual([e["user_id"] for e in corpo["items"]], [CLIENTE])

    def test_AU33_filtro_ip_address(self):
        self.semear_eventos()
        self.como(SUPER, "Superadmin")
        _, corpo = self.listar(ip_address="10.0.0.2")
        self.assertEqual(corpo["total"], 2)
        self.assertEqual({e["ip_address"] for e in corpo["items"]}, {"10.0.0.2"})

    def test_AU34_filtro_por_periodo(self):
        from datetime import datetime, timedelta, timezone

        self.semear_eventos()
        self.como(SUPER, "Superadmin")
        agora = datetime.now(timezone.utc)
        h = lambda d: (agora + timedelta(hours=d)).isoformat()
        self.assertEqual(self.listar(data_inicio=h(-1), data_fim=h(1))[1]["total"], 4)
        self.assertEqual(self.listar(data_fim=h(-1))[1]["total"], 0)
        self.assertEqual(self.listar(data_inicio=h(1))[1]["total"], 0)
        # Datas naive sao tratadas como UTC.
        self.assertEqual(self.listar(data_inicio=(agora - timedelta(hours=1)).replace(tzinfo=None).isoformat())[1]["total"], 4)

    def test_AU35_mais_recente_primeiro(self):
        self.semear_eventos()
        self.envelhecer(30, tipo=auditoria.PROJECT_ACCESS)   # o 1o inserido vira o mais antigo
        self.envelhecer(90, tipo=auditoria.LOGIN_FAILED)     # o ultimo inserido vira o mais antigo de todos
        self.como(SUPER, "Superadmin")
        _, corpo = self.listar()
        tipos = [e["event_type"] for e in corpo["items"]]
        self.assertEqual(tipos[-1], auditoria.LOGIN_FAILED)
        self.assertEqual(tipos[-2], auditoria.PROJECT_ACCESS)
        datas = [e["occurred_at"] for e in corpo["items"]]
        self.assertEqual(datas, sorted(datas, reverse=True))

    def test_AU36_limite_maximo_da_pagina_e_100(self):
        self.como(SUPER, "Superadmin")
        self.assertEqual(self.listar(limit=500)[0], 422)
        self.assertEqual(self.listar(limit=101)[0], 422)
        self.assertEqual(self.listar(limit=0)[0], 422)
        status, corpo = self.listar(limit=100, offset=0)
        self.assertEqual((status, corpo["limit"]), (200, 100))
        # O servico tambem limita por conta propria, mesmo chamado direto.
        self.assertEqual(auditoria.LIMITE_MAX_LISTAGEM, 100)
        self.assertLessEqual(len(auditoria.listar_eventos(self.db, limit=500)[0]), 100)

    # --- segredos: prova direta na tabela -----------------------------------
    def tabela_serializada(self):
        import json

        self.db.expire_all()
        linhas = self.db.execute(text("SELECT * FROM audit_events")).mappings().all()
        return json.dumps([dict(l) for l in linhas], default=str)

    def test_AU37_nenhum_segredo_persiste_em_audit_events(self):
        from datetime import datetime, timedelta, timezone

        SENHA, NOVA_SENHA, TOKEN_ATIV = "SECRET_PASSWORD_MARKER", "SECRET_NEWPASS_MARKER1", "SECRET_ACTIVATION_MARKER"
        # login invalido com senha marcador
        self.assertEqual(self.client.post("/login/token", data={"username": "gerente@qa.com", "password": SENHA}).status_code, 401)
        # login valido que emite JWT
        r = self.client.post("/login/token", data={"username": "gerente@qa.com", "password": "segredo1"})
        jwt_emitido = r.json()["access_token"]
        # ativacao com token marcador
        self.db.add(models.UserActivationToken(
            usuario_id=CONVIDADO, token_hash=ativacao.hash_token(TOKEN_ATIV),
            expira_em=datetime.now(timezone.utc) + timedelta(hours=1)))
        self.db.commit()
        r = self.client.post("/usuarios/ativacao", json={"token": TOKEN_ATIV, "senha": NOVA_SENHA})
        self.assertEqual(r.status_code, 200, r.text)
        # requisicao com Authorization Bearer (vale como token real, via middleware)
        self.assertEqual(self.client.get(f"/projetos/{A1}", headers={"Authorization": f"Bearer {jwt_emitido}"}).status_code, 200)
        url_ativacao = ativacao.url_ativacao(TOKEN_ATIV)

        tipos = {e.event_type for e in self.eventos()}
        self.assertTrue({auditoria.LOGIN_FAILED, auditoria.LOGIN_SUCCESS, auditoria.ACCOUNT_ACTIVATED, auditoria.PROJECT_ACCESS} <= tipos, tipos)
        dump = self.tabela_serializada()
        for segredo in (SENHA, NOVA_SENHA, TOKEN_ATIV, jwt_emitido, f"Bearer {jwt_emitido}", url_ativacao, "activation_url"):
            self.assertEqual(dump.count(segredo), 0, segredo)

    def test_AU38_query_string_nunca_e_persistida(self):
        # Rota de ativacao com token na query: sem evento, e sem rastro do token.
        self.client.get("/usuarios/ativacao/validar", params={"token": "SEGREDO_QUERY"})
        # Rota auditada com query string: o evento guarda so o path.
        r = self.client.get("/projetos/999", params={"token": "SEGREDO_QUERY", "x": "SEGREDO_QUERY"})
        self.assertEqual(r.status_code, 404)
        (ev,) = self.eventos(auditoria.ACCESS_DENIED)
        self.assertEqual(ev.path, "/projetos/999")
        self.assertEqual(self.tabela_serializada().count("SEGREDO_QUERY"), 0)

    # --- token: expirado x invalido, sem parse duplicado --------------------
    def test_AU39_token_expirado_e_invalido_sao_eventos_distintos(self):
        from datetime import timedelta

        self.app.dependency_overrides.pop(get_current_user)   # fluxo JWT real
        expirado = security.create_access_token({"sub": "gerente@qa.com"}, expires_delta=timedelta(seconds=-5))
        self.assertEqual(self.client.get(f"/projetos/{A1}", headers={"Authorization": f"Bearer {expirado}"}).status_code, 401)
        self.assertEqual(self.client.get(f"/projetos/{A1}", headers={"Authorization": "Bearer nao.e.jwt"}).status_code, 401)
        refresh = security.create_refresh_token({"sub": "gerente@qa.com"})
        self.assertEqual(self.client.get(f"/projetos/{A1}", headers={"Authorization": f"Bearer {refresh}"}).status_code, 401)
        valido_inativo = security.create_access_token({"sub": "inativo@qa.com"})
        self.assertEqual(self.client.get(f"/projetos/{A1}", headers={"Authorization": f"Bearer {valido_inativo}"}).status_code, 401)
        self.assertEqual(
            [(e.event_type, e.details["motivo"]) for e in self.eventos()],
            [(auditoria.TOKEN_EXPIRED, "expirado"), (auditoria.TOKEN_INVALID, "invalido"),
             (auditoria.TOKEN_INVALID, "tipo_errado"), (auditoria.TOKEN_REJECTED, "usuario_inativo_ou_inexistente")],
        )
        self.assertEqual(self.eventos()[-1].user_id, INATIVO)
        dump = self.tabela_serializada()
        for t in (expirado, refresh, valido_inativo):
            self.assertEqual(dump.count(t), 0)

    # --- cross-tenant e RBAC: externo x interno ------------------------------
    def test_AU40_cross_tenant_404_externo_e_HIGH_interno(self):
        r = self.client.get(f"/projetos/{B1}")   # Gerente da A pede projeto da B
        self.assertEqual((r.status_code, r.json()["detail"]), (404, "Projeto não encontrado"))
        (ev,) = self.eventos()
        self.assertEqual((ev.event_type, ev.severity, ev.status_code, ev.project_id, ev.company_id),
                         (auditoria.CROSS_TENANT_ACCESS_ATTEMPT, "HIGH", 404, B1, EMPRESA_A))
        self.assertEqual(self.eventos(auditoria.PROJECT_ACCESS), [])

    def test_AU41_cliente_com_acl_faz_PATCH_e_recebe_403_com_um_unico_evento(self):
        self.como(CLIENTE, "Cliente")
        r = self.client.patch(f"/projetos/{A1}", json={"nome": "novo"})
        self.assertEqual(r.status_code, 403)
        eventos = self.eventos()
        self.assertEqual(len(eventos), 1, "uma negacao, um evento")
        self.assertEqual((eventos[0].event_type, eventos[0].status_code), (auditoria.RBAC_DENIED, 403))
        self.assertEqual(eventos[0].details["papel"], "CLIENTE")


if __name__ == "__main__":
    unittest.main()
