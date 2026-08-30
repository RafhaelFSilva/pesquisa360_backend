"""ADR-037 -- matriz de autorizacao por perfil, exercitada por HTTP.

Nada aqui depende do Frontend: sao chamadas diretas a API, como um `curl` faria.
E esse o ponto do RBAC -- esconder botao e UX, autorizacao e servidor.

Cenario fixo:

    Empresa A: Projeto A1 e A2      Empresa B: Projeto B1
    Cada perfil recebe ACL de A1 (e Cliente/Coordenador NAO recebem A2).

Combinacoes provadas (§8 do enunciado):

    Cliente + ACL A1 + GET    -> 200
    Cliente sem ACL em A2     -> 404   (escopo: nao revela existencia)
    Cliente + ACL A1 + PATCH  -> 403   (capacidade: recurso ja e visivel)
    Cliente + projeto da B    -> 404
"""
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-rbac-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.api.endpoints import agente as rotas_agente
from pesquisa360.api.endpoints import coletas as rotas_coletas
from pesquisa360.api.endpoints import empresas as rotas_empresas
from pesquisa360.api.endpoints import projetos as rotas_projetos
from pesquisa360.api.endpoints import relatorios as rotas_relatorios
from pesquisa360.api.endpoints import usuarios as rotas_usuarios
from pesquisa360.core import rbac
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.core.rbac import MATRIZ, Papel, Permissao
from pesquisa360.db import models
from tests.test_base_eleitoral_import import run_alembic_upgrade

EMPRESA_A, EMPRESA_B = 10, 20
A1, A2, B1 = 101, 102, 201
PESQ_A1, PESQ_A2, PESQ_B1 = 1001, 1002, 2001

# usuario_id por papel
IDS = {"SUPERADMIN": 1, "GERENTE": 2, "COORDENADOR": 3, "SUPERVISOR": 4, "CLIENTE": 5, "AGENTE": 6}
PERFIS = {"SUPERADMIN": 1, "GERENTE": 2, "COORDENADOR": 3, "SUPERVISOR": 4, "CLIENTE": 5, "AGENTE": 6}
NOMES = {"SUPERADMIN": "Superadmin", "GERENTE": "Gerente", "COORDENADOR": "Coordenador",
         "SUPERVISOR": "Supervisor", "CLIENTE": "Cliente", "AGENTE": "Agente"}


def usuario(papel: str, company_id=EMPRESA_A):
    return SimpleNamespace(
        id=IDS[papel], email=f"{papel.lower()}@qa.com", nome=NOMES[papel], ativo=True,
        company_id=company_id, perfil_id=PERFIS[papel],
        perfil=SimpleNamespace(nome=NOMES[papel]), perfil_nome=NOMES[papel],
    )


class MatrizRbacTests(unittest.TestCase):
    """A matriz em si, sem HTTP -- garante que a tabela nao seja afrouxada."""

    def test_cliente_nao_tem_nenhuma_capacidade_de_escrita(self):
        escritas = {
            Permissao.PROJETO_CRIAR, Permissao.PROJETO_EDITAR, Permissao.PROJETO_EXCLUIR,
            Permissao.PESQUISA_GERENCIAR, Permissao.TERRITORIO_GERENCIAR,
            Permissao.RELATORIO_CONFIGURAR, Permissao.LIDERANCA_GERENCIAR,
            Permissao.BASE_ELEITORAL_GERENCIAR, Permissao.USUARIO_GERENCIAR,
            Permissao.EMPRESA_GERENCIAR, Permissao.COLETA_ENVIAR,
        }
        self.assertEqual(MATRIZ[Papel.CLIENTE] & escritas, set(), "Cliente e READ ONLY")

    def test_agente_so_tem_o_necessario_para_o_campo(self):
        self.assertEqual(
            MATRIZ[Papel.AGENTE], {Permissao.MISSAO_SINCRONIZAR, Permissao.COLETA_ENVIAR}
        )

    def test_supervisor_nao_administra_tenant_nem_questionario(self):
        proibidas = {
            Permissao.USUARIO_GERENCIAR, Permissao.EMPRESA_GERENCIAR,
            Permissao.PROJETO_CRIAR, Permissao.PROJETO_EXCLUIR,
            Permissao.PESQUISA_GERENCIAR, Permissao.PROJETO_EDITAR,
        }
        self.assertEqual(MATRIZ[Papel.SUPERVISOR] & proibidas, set())
        # Mas monitora e organiza o campo.
        self.assertIn(Permissao.CAMPO_MONITORAR, MATRIZ[Papel.SUPERVISOR])
        self.assertIn(Permissao.TERRITORIO_GERENCIAR, MATRIZ[Papel.SUPERVISOR])

    def test_coordenador_gerencia_projeto_mas_nao_o_tenant(self):
        self.assertIn(Permissao.PESQUISA_GERENCIAR, MATRIZ[Papel.COORDENADOR])
        self.assertIn(Permissao.PROJETO_EDITAR, MATRIZ[Papel.COORDENADOR])
        for proibida in (Permissao.USUARIO_GERENCIAR, Permissao.EMPRESA_GERENCIAR,
                         Permissao.PROJETO_CRIAR, Permissao.PROJETO_EXCLUIR):
            self.assertNotIn(proibida, MATRIZ[Papel.COORDENADOR])

    def test_apenas_superadmin_administra_empresas(self):
        self.assertEqual(rbac.papeis_com(Permissao.EMPRESA_GERENCIAR), {Papel.SUPERADMIN})
        self.assertEqual(
            rbac.papeis_com(Permissao.USUARIO_GERENCIAR), {Papel.SUPERADMIN, Papel.GERENTE}
        )

    def test_perfil_desconhecido_nao_recebe_permissao(self):
        """Na duvida, negar: perfil novo/renomeado nao vira acesso implicito."""
        estranho = SimpleNamespace(id=99, ativo=True, perfil=SimpleNamespace(nome="Estagiario"))
        self.assertIsNone(rbac.papel_do_usuario(estranho))
        self.assertEqual(rbac.permissoes_do_usuario(estranho), set())
        for permissao in Permissao:
            self.assertFalse(rbac.tem_permissao(estranho, permissao))

    def test_papel_vem_do_nome_e_tolera_variacao(self):
        for nome, esperado in [("gerente", Papel.GERENTE), ("  Superadmin ", Papel.SUPERADMIN),
                               ("CLIENTE", Papel.CLIENTE), ("Coordenadora", Papel.COORDENADOR)]:
            u = SimpleNamespace(id=1, ativo=True, perfil=SimpleNamespace(nome=nome))
            self.assertEqual(rbac.papel_do_usuario(u), esperado, nome)

    def test_usuario_inativo_nao_tem_permissao(self):
        u = usuario("GERENTE")
        u.ativo = False
        self.assertFalse(rbac.tem_permissao(u, Permissao.PROJETO_VER))


class RbacHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._dir = TemporaryDirectory()
        cls.db_path = Path(cls._dir.name) / "rbac.sqlite"
        run_alembic_upgrade(f"sqlite:///{cls.db_path.as_posix()}")

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def setUp(self):
        self.engine = create_engine(f"sqlite:///{self.db_path.as_posix()}")

        @event.listens_for(self.engine, "connect")
        def _postgis_stubs(conexao, _):
            # SQLite nao tem PostGIS: as rotas leem geometria, mas o RBAC nao
            # depende dela -- basta nao explodir.
            for nome in ("AsEWKB", "ST_AsEWKB", "ST_AsGeoJSON", "AsGeoJSON", "ST_X", "ST_Y"):
                conexao.create_function(nome, 1, lambda v: None)
            conexao.create_function("GeomFromEWKT", 1, lambda v: v)
            conexao.create_function("ST_GeomFromText", -1, lambda *a: a[0])

        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as c:
            for tabela in ("usuario_projeto_acessos", "usuario_empresa_acessos", "perguntas",
                           "pesquisas", "projetos", "usuarios", "perfis", "companies"):
                c.execute(text(f"DELETE FROM {tabela}"))
            c.execute(text(
                f"INSERT INTO companies (id,name,is_active,created_at) VALUES ({EMPRESA_A},'Empresa A',1,'2026-01-01 00:00:00'),({EMPRESA_B},'Empresa B',1,'2026-01-01 00:00:00')"))
            for papel, pid in PERFIS.items():
                c.execute(text(f"INSERT INTO perfis (id,nome) VALUES ({pid},'{NOMES[papel]}')"))
            for papel, uid in IDS.items():
                c.execute(text(
                    "INSERT INTO usuarios (id,email,nome,senha_hash,ativo,perfil_id,company_id)"
                    f" VALUES ({uid},'{papel.lower()}@qa.com','{NOMES[papel]}','h',1,{PERFIS[papel]},{EMPRESA_A})"))
            c.execute(text(
                "INSERT INTO projetos (id,nome,status,coordenador_id,company_id) VALUES "
                f"({A1},'A1','Ativo',2,{EMPRESA_A}),({A2},'A2','Ativo',2,{EMPRESA_A}),({B1},'B1','Ativo',2,{EMPRESA_B})"))
            c.execute(text(
                "INSERT INTO pesquisas (id,titulo,ativo,projeto_id) VALUES "
                f"({PESQ_A1},'Pesq A1',1,{A1}),({PESQ_A2},'Pesq A2',1,{A2}),({PESQ_B1},'Pesq B1',1,{B1})"))
            # ACL: todos acessam A1. Cliente e Coordenador NAO acessam A2.
            for papel, uid in IDS.items():
                restrito = papel in ("CLIENTE", "COORDENADOR")
                c.execute(text(
                    "INSERT INTO usuario_empresa_acessos (usuario_id,company_id,acesso_todos_projetos,ativo,principal)"
                    f" VALUES ({uid},{EMPRESA_A},{0 if restrito else 1},1,1)"))
                if restrito:
                    c.execute(text(
                        f"INSERT INTO usuario_projeto_acessos (usuario_id,projeto_id,ativo) VALUES ({uid},{A1},1)"))
        self.db = self.Session()
        self.addCleanup(self.db.close)

        self.app = FastAPI()
        self.app.include_router(rotas_projetos.router)
        self.app.include_router(rotas_relatorios.router)
        self.app.include_router(rotas_coletas.router)
        self.app.include_router(rotas_usuarios.router, prefix="/usuarios")
        self.app.include_router(rotas_usuarios.admin_router)
        self.app.include_router(rotas_empresas.router, prefix="/empresas")
        self.app.include_router(rotas_agente.router, prefix="/agente")
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.atual = usuario("GERENTE")
        self.app.dependency_overrides[get_current_user] = lambda: self.atual
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def como(self, papel, company_id=EMPRESA_A):
        self.atual = usuario(papel, company_id)

    # --- §8: as quatro combinacoes canonicas ------------------------------
    def test_RB01_cliente_com_acl_le_o_projeto(self):
        self.como("CLIENTE")
        self.assertEqual(self.client.get(f"/projetos/{A1}").status_code, 200)

    def test_RB02_cliente_sem_acl_recebe_404_nao_403(self):
        """Escopo: negar nao pode revelar que A2 existe."""
        self.como("CLIENTE")
        r = self.client.get(f"/projetos/{A2}")
        self.assertEqual(r.status_code, 404, r.text)

    def test_RB03_cliente_escrevendo_no_proprio_projeto_recebe_403(self):
        """Capacidade: o recurso ja e visivel, entao 403 nao vaza nada."""
        self.como("CLIENTE")
        r = self.client.patch(f"/projetos/{A1}", json={"nome": "hack"})
        self.assertEqual(r.status_code, 403, r.text)
        self.assertIn("perfil", r.json()["detail"].lower())
        # E o dado nao mudou.
        self.assertEqual(
            self.db.query(models.Projeto).filter(models.Projeto.id == A1).first().nome, "A1"
        )

    def test_RB04_projeto_de_outro_tenant_e_404_para_todos_menos_superadmin(self):
        for papel in ("GERENTE", "COORDENADOR", "SUPERVISOR", "CLIENTE"):
            self.como(papel)
            self.assertEqual(self.client.get(f"/projetos/{B1}").status_code, 404, papel)

    # --- Cliente read-only, por HTTP direto -------------------------------
    def test_RB05_cliente_e_read_only_em_todas_as_escritas(self):
        self.como("CLIENTE")
        escritas = [
            ("post", "/projetos/", {"nome": "X", "descricao": "d", "status": "Ativo", "coordenador_id": 2}),
            ("patch", f"/projetos/{A1}", {"nome": "X"}),
            ("delete", f"/projetos/{A1}", None),
            ("post", f"/projetos/{A1}/pesquisas/", {"titulo": "X", "tipo_pesquisa": "Eleitoral"}),
            ("patch", f"/projetos/{A1}/pesquisas/{PESQ_A1}", {"titulo": "X"}),
            ("delete", f"/projetos/{A1}/pesquisas/{PESQ_A1}", None),
            ("post", f"/pesquisas/{PESQ_A1}/perguntas/", {"texto_pergunta": "X", "tipo_pergunta": "TEXTO", "ordem": 1}),
            ("patch", f"/projetos/{A1}/pesquisas/{PESQ_A1}/geofence", {"tolerancia_metros": 10}),
            ("post", f"/pesquisas/{PESQ_A1}/setores", {"nome": "S", "meta": 1, "geometria_coords": [[0, 0], [0, 1], [1, 1]]}),
            ("post", f"/pesquisas/{PESQ_A1}/coletas/", {"client_uuid": "x", "data_inicio_coleta": "2026-01-01T00:00:00Z", "respostas": []}),
        ]
        for metodo, rota, corpo in escritas:
            resposta = getattr(self.client, metodo)(rota, json=corpo) if corpo is not None else getattr(self.client, metodo)(rota)
            self.assertEqual(resposta.status_code, 403, f"{metodo.upper()} {rota} -> {resposta.status_code}")

    def test_RB06_cliente_le_relatorios_e_monitoramento_do_projeto_autorizado(self):
        self.como("CLIENTE")
        for rota in (f"/projetos/{A1}", f"/projetos/{A1}/pesquisas/", "/projetos/"):
            self.assertEqual(self.client.get(rota).status_code, 200, rota)
        # A listagem traz apenas o projeto autorizado.
        ids = [p["id"] for p in self.client.get("/projetos/").json()]
        self.assertEqual(ids, [A1])

    def test_RB07_cliente_nao_administra_usuarios_nem_empresas(self):
        self.como("CLIENTE")
        for metodo, rota in (("get", "/usuarios/"), ("post", "/usuarios/"), ("get", "/empresas/"),
                             ("get", "/admin/usuarios/")):
            r = self.client.post(rota, json={}) if metodo == "post" else self.client.get(rota)
            self.assertEqual(r.status_code, 403, f"{metodo.upper()} {rota}")

    # --- Agente ------------------------------------------------------------
    def test_RB08_agente_faz_o_fluxo_de_campo_e_nada_mais(self):
        self.como("AGENTE")
        # Permitido: missao e sincronizacao (o Mobile usa exatamente estas).
        self.assertEqual(self.client.get("/agente/pesquisas/").status_code, 200)
        self.assertEqual(self.client.get(f"/agente/missao/{PESQ_A1}").status_code, 200)
        # Negado: o Web administrativo inteiro.
        for metodo, rota in (
            ("get", "/projetos/"), ("post", "/projetos/"), ("patch", f"/projetos/{A1}"),
            ("get", f"/relatorios/pesquisas/{PESQ_A1}/simples/"),
            ("post", f"/relatorios/pesquisas/{PESQ_A1}/cruzamentos-multidimensionais/"),
            ("get", "/usuarios/"), ("get", "/empresas/"), ("get", "/admin/usuarios/"),
            ("get", f"/pesquisas/{PESQ_A1}/coletas/monitoramento/"),
        ):
            r = (
                getattr(self.client, metodo)(rota, json={})
                if metodo in ("post", "patch")
                else getattr(self.client, metodo)(rota)
            )
            self.assertEqual(r.status_code, 403, f"{metodo.upper()} {rota} -> {r.status_code}")

    def test_RB09_usuarios_me_continua_aberto_a_qualquer_autenticado(self):
        """Compatibilidade do Mobile e do Web."""
        for papel in IDS:
            self.como(papel)
            r = self.client.get("/usuarios/me/")
            self.assertEqual(r.status_code, 200, papel)
            corpo = r.json()
            self.assertEqual(corpo["papel"], papel)
            self.assertIn("permissions", corpo)
            self.assertEqual(
                set(corpo["permissions"]), {p.value for p in MATRIZ[Papel(papel)]}, papel
            )
            # Contrato antigo intacto.
            for chave in ("id", "email", "perfil_id", "company_id", "ativo"):
                self.assertIn(chave, corpo)

    # --- Supervisor / Coordenador / Gerente --------------------------------
    def test_RB10_supervisor_monitora_mas_nao_administra(self):
        self.como("SUPERVISOR")
        self.assertEqual(self.client.get(f"/projetos/{A1}").status_code, 200)
        self.assertEqual(self.client.get(f"/pesquisas/{PESQ_A1}/coletas/monitoramento/").status_code, 200)
        # Organiza campo (setores), mas nao mexe em questionario nem no tenant.
        for metodo, rota, corpo in (
            ("post", "/projetos/", {"nome": "X", "descricao": "d", "status": "Ativo", "coordenador_id": 2}),
            ("delete", f"/projetos/{A1}", None),
            ("post", f"/pesquisas/{PESQ_A1}/perguntas/", {"texto_pergunta": "X", "tipo_pergunta": "TEXTO", "ordem": 1}),
            ("get", "/usuarios/", None),
            ("get", "/empresas/", None),
        ):
            r = getattr(self.client, metodo)(rota, json=corpo) if corpo is not None else getattr(self.client, metodo)(rota)
            self.assertEqual(r.status_code, 403, f"{metodo.upper()} {rota}")

    def test_RB11_coordenador_gerencia_o_projeto_autorizado(self):
        self.como("COORDENADOR")
        self.assertEqual(self.client.get(f"/projetos/{A1}").status_code, 200)
        r = self.client.patch(f"/projetos/{A1}", json={"nome": "A1 renomeado"})
        self.assertEqual(r.status_code, 200, r.text)
        # Fora da ACL continua 404, mesmo com capacidade de editar.
        self.assertEqual(self.client.get(f"/projetos/{A2}").status_code, 404)
        self.assertEqual(self.client.patch(f"/projetos/{A2}", json={"nome": "x"}).status_code, 404)
        # E nao administra o tenant.
        for rota in ("/usuarios/", "/empresas/", "/admin/usuarios/"):
            self.assertEqual(self.client.get(rota).status_code, 403, rota)
        self.assertEqual(
            self.client.post("/projetos/", json={"nome": "novo", "descricao": "d", "status": "Ativo", "coordenador_id": 2}).status_code,
            403,
        )

    def test_RB12_gerente_administra_o_tenant_mas_nao_atravessa_para_outro(self):
        self.como("GERENTE")
        self.assertEqual(self.client.get("/usuarios/").status_code, 200)
        self.assertEqual(self.client.get(f"/projetos/{A1}").status_code, 200)
        self.assertEqual(self.client.get(f"/projetos/{A2}").status_code, 200)
        # Empresa B: invisivel.
        self.assertEqual(self.client.get(f"/projetos/{B1}").status_code, 404)
        self.assertNotIn(B1, [p["id"] for p in self.client.get("/projetos/").json()])
        # Administracao global continua so do Superadmin.
        self.assertEqual(self.client.get("/empresas/").status_code, 403)
        self.assertEqual(self.client.get("/admin/usuarios/").status_code, 403)

    def test_RB13_superadmin_mantem_as_capacidades_atuais(self):
        self.como("SUPERADMIN")
        for rota in ("/empresas/", "/admin/usuarios/", "/usuarios/", "/projetos/"):
            self.assertEqual(self.client.get(rota).status_code, 200, rota)
        # Enxerga os dois tenants (ADR-034 preservada).
        ids = [p["id"] for p in self.client.get("/projetos/").json()]
        self.assertEqual(sorted(ids), [A1, A2, B1])

    # --- ordem das checagens ----------------------------------------------
    def test_RB14_escopo_e_avaliado_antes_da_capacidade(self):
        """Projeto de outro tenant responde 404 mesmo para quem PODE editar."""
        self.como("GERENTE")
        r = self.client.patch(f"/projetos/{B1}", json={"nome": "x"})
        self.assertEqual(r.status_code, 404, "404 protege a existencia do recurso")

    def test_RB15_permissao_nao_substitui_acl(self):
        """Coordenador tem PESQUISA_GERENCIAR, mas so onde tem ACL."""
        self.como("COORDENADOR")
        permitido = self.client.get(f"/projetos/{A1}/pesquisas/")
        self.assertEqual(permitido.status_code, 200)
        negado = self.client.get(f"/projetos/{A2}/pesquisas/")
        self.assertEqual(negado.status_code, 404)


if __name__ == "__main__":
    unittest.main()
