"""ADR-024 -- ACL multiempresa/multiprojeto por usuario.

Prova o cenario do enunciado com banco real (Alembic ate o head):

    Usuario X  (empresa principal = Empresa A)
      Empresa A -> A1, A2   (acesso total)
      Empresa B -> B1       (acesso restrito)

    GET /projetos/  -> A1, A2, B1   (nunca B2)
    GET B2          -> 404
    revogar B1      -> 404 com o MESMO token, sem novo login
    coleta em B1    -> company_id da Empresa B, agente_id do usuario

Tambem cobre migration/backfill, seguranca da API administrativa e a
compatibilidade do usuario legado (1 empresa, acesso total).
"""
import os
import re
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from shapely import wkb, wkt
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-acl-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.api.endpoints import coletas as rotas_coletas
from pesquisa360.api.endpoints import projetos as rotas_projetos
from pesquisa360.api.endpoints import usuarios as rotas_usuarios
from pesquisa360.core.dependencies import (
    get_current_user,
    get_db,
    require_manager_or_superadmin,
    require_superadmin,
)
from pesquisa360.db import models
from pesquisa360.services import acessos
from tests.test_base_eleitoral_import import run_alembic_upgrade

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HEAD = "c6d7e8f9a0b1"
ANTES = "b9c0d1e2f3a4"

EMPRESA_A, EMPRESA_B = 10, 20
A1, A2, B1, B2 = 101, 102, 201, 202
USUARIO_X, GERENTE_A, SUPERADMIN, AGENTE = 5, 1, 9, 7


def usuario(user_id, company_id, perfil="Gerente"):
    return SimpleNamespace(
        id=user_id, email=f"u{user_id}@qa.com", company_id=company_id, ativo=True,
        perfil_id={"Gerente": 1, "Agente": 2, "Superadmin": 3}.get(perfil, 1),
        perfil=SimpleNamespace(nome=perfil), perfil_nome=perfil,
    )


# =============================================================================
# Migration / backfill
# =============================================================================
class BackfillAclTests(unittest.TestCase):
    def alembic(self, *args, url):
        env = os.environ.copy()
        env["DATABASE_URL"] = url
        done = subprocess.run(
            [sys.executable, "-m", "alembic", *args], cwd=PROJECT_ROOT,
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done.stdout + done.stderr

    def test_backfill_preserva_o_alcance_do_usuario_legado(self):
        with TemporaryDirectory() as d:
            caminho = Path(d) / "acl.sqlite"
            url = f"sqlite:///{caminho.as_posix()}"
            self.alembic("upgrade", ANTES, url=url)
            con = sqlite3.connect(caminho)
            try:
                perfil = con.execute("SELECT id FROM perfis ORDER BY id LIMIT 1").fetchone()[0]
                company = con.execute("SELECT id FROM companies ORDER BY id LIMIT 1").fetchone()[0]
                con.execute(
                    "INSERT INTO usuarios (id,email,nome,senha_hash,ativo,perfil_id,company_id)"
                    " VALUES (900,'legado@qa.com','Legado','h',1,?,?)", (perfil, company))
                con.execute(
                    "INSERT INTO projetos (id,nome,status,coordenador_id,company_id)"
                    " VALUES (901,'P legado','Ativo',900,?)", (company,))
                con.commit()
            finally:
                con.close()

            self.alembic("upgrade", "head", url=url)

            con = sqlite3.connect(caminho)
            try:
                linhas = con.execute(
                    "SELECT company_id, acesso_todos_projetos, ativo, principal"
                    "  FROM usuario_empresa_acessos WHERE usuario_id = 900").fetchall()
                projetos = con.execute(
                    "SELECT COUNT(*) FROM usuario_projeto_acessos").fetchone()[0]
                # Todo usuario preexistente ganhou exatamente um vinculo.
                total_usuarios = con.execute("SELECT COUNT(*) FROM usuarios WHERE company_id IS NOT NULL").fetchone()[0]
                total_acessos = con.execute("SELECT COUNT(*) FROM usuario_empresa_acessos").fetchone()[0]
            finally:
                con.close()

            self.assertEqual(linhas, [(company, 1, 1, 1)])
            # §14: nada de uma linha por projeto -- acesso_todos_projetos cobre.
            self.assertEqual(projetos, 0)
            self.assertEqual(total_acessos, total_usuarios)

    def test_backfill_e_idempotente_e_reversivel(self):
        with TemporaryDirectory() as d:
            caminho = Path(d) / "acl2.sqlite"
            url = f"sqlite:///{caminho.as_posix()}"
            self.alembic("upgrade", "head", url=url)
            self.assertIn(HEAD, self.alembic("current", url=url))
            con = sqlite3.connect(caminho)
            try:
                antes = con.execute("SELECT COUNT(*) FROM usuario_empresa_acessos").fetchone()[0]
            finally:
                con.close()

            self.alembic("downgrade", ANTES, url=url)
            con = sqlite3.connect(caminho)
            try:
                tabelas = {
                    linha[0]
                    for linha in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                # Coluna legada intacta apos o downgrade.
                colunas = {linha[1] for linha in con.execute("PRAGMA table_info(usuarios)")}
            finally:
                con.close()
            self.assertNotIn("usuario_empresa_acessos", tabelas)
            self.assertNotIn("usuario_projeto_acessos", tabelas)
            self.assertIn("company_id", colunas)

            self.alembic("upgrade", "head", url=url)
            con = sqlite3.connect(caminho)
            try:
                depois = con.execute("SELECT COUNT(*) FROM usuario_empresa_acessos").fetchone()[0]
            finally:
                con.close()
            self.assertEqual(antes, depois)
            self.assertEqual(self.alembic("heads", url=url).count("(head)"), 1)


# =============================================================================
# Autorizacao
# =============================================================================
class AclMultiempresaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._dir = TemporaryDirectory()
        cls.db_path = Path(cls._dir.name) / "acl.sqlite"
        run_alembic_upgrade(f"sqlite:///{cls.db_path.as_posix()}")

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def setUp(self):
        self.engine = create_engine(f"sqlite:///{self.db_path.as_posix()}")

        @event.listens_for(self.engine, "connect")
        def _funcoes(conexao, _):
            def coord(value, pos):
                if value is None:
                    return None
                if isinstance(value, bytes):
                    value = value.decode()
                m = re.search(r"POINT\s*\(([-+0-9.eE]+)\s+([-+0-9.eE]+)\)", str(value))
                return float(m.group(pos + 1)) if m else None

            def as_ewkb(value):
                if value is None:
                    return None
                if isinstance(value, bytes):
                    value = value.decode()
                return wkb.dumps(wkt.loads(str(value).split(";", 1)[-1]), hex=True, srid=4326)

            conexao.create_function("ST_GeomFromText", -1, lambda *a: a[0])
            conexao.create_function("ST_AsGeoJSON", 1, lambda v: None)
            conexao.create_function("AsGeoJSON", 1, lambda v: None)
            conexao.create_function("AsEWKB", 1, as_ewkb)
            conexao.create_function("GeomFromEWKT", 1, lambda v: v)
            conexao.create_function("ST_GeomFromEWKT", 1, lambda v: v)
            conexao.create_function("ST_Y", 1, lambda v: coord(v, 1))
            conexao.create_function("ST_X", 1, lambda v: coord(v, 0))

        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as c:
            for tabela in (
                "usuario_projeto_acessos", "usuario_empresa_acessos", "respostas", "coletas",
                "setor_agentes", "setores", "perguntas", "pesquisas", "projetos", "usuarios",
                "perfis", "companies",
            ):
                c.execute(text(f"DELETE FROM {tabela}"))
            c.execute(text(
                f"INSERT INTO companies (id,name,is_active) VALUES ({EMPRESA_A},'Empresa QA A',1),({EMPRESA_B},'Empresa QA B',1)"))
            c.execute(text("INSERT INTO perfis (id,nome) VALUES (1,'Gerente'),(2,'Agente'),(3,'Superadmin')"))
            c.execute(text(
                "INSERT INTO usuarios (id,email,nome,senha_hash,ativo,perfil_id,company_id) VALUES "
                f"({GERENTE_A},'gerente.a@qa.com','Gerente A','h',1,1,{EMPRESA_A}),"
                f"({USUARIO_X},'x@qa.com','Usuario X','h',1,1,{EMPRESA_A}),"
                f"({AGENTE},'agente@qa.com','Agente Joao','h',1,2,{EMPRESA_A}),"
                f"({SUPERADMIN},'root@qa.com','Root','h',1,3,{EMPRESA_A})"))
            c.execute(text(
                "INSERT INTO projetos (id,nome,status,coordenador_id,company_id) VALUES "
                f"({A1},'A1','Ativo',{GERENTE_A},{EMPRESA_A}),({A2},'A2','Ativo',{GERENTE_A},{EMPRESA_A}),"
                f"({B1},'B1','Ativo',{GERENTE_A},{EMPRESA_B}),({B2},'B2','Ativo',{GERENTE_A},{EMPRESA_B})"))
            c.execute(text(
                "INSERT INTO pesquisas (id,titulo,ativo,projeto_id) VALUES "
                f"(1001,'Pesq A1',1,{A1}),(1002,'Pesq A2',1,{A2}),(2001,'Pesq B1',1,{B1}),(2002,'Pesq B2',1,{B2})"))
            c.execute(text(
                "INSERT INTO perguntas (id,texto_pergunta,tipo_pergunta,ordem,eh_obrigatoria,ativo,pesquisa_id) VALUES "
                "(11,'Voto','TEXTO',1,1,1,1001),(21,'Voto','TEXTO',1,1,1,2001)"))
        self.db = self.Session()
        self.addCleanup(self.db.close)

        self.app = FastAPI()
        self.app.include_router(rotas_projetos.router)
        self.app.include_router(rotas_coletas.router)
        self.app.include_router(rotas_usuarios.router, prefix="/usuarios")
        self.app.include_router(rotas_usuarios.admin_router)
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.atual = usuario(USUARIO_X, EMPRESA_A)
        self.app.dependency_overrides[get_current_user] = lambda: self.atual
        self.app.dependency_overrides[require_manager_or_superadmin] = lambda: self.atual
        self.app.dependency_overrides[require_superadmin] = lambda: self.atual
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        p = patch("pesquisa360.crud.geocoding.obter_endereco_por_coords", return_value="Rua X")
        p.start()
        self.addCleanup(p.stop)

    # --- helpers ---------------------------------------------------------
    def dar_acesso(self, usuario_id, company_id, todos=True, projeto_ids=(), principal=False):
        self.db.execute(text(
            "INSERT INTO usuario_empresa_acessos (usuario_id,company_id,acesso_todos_projetos,ativo,principal)"
            " VALUES (:u,:c,:t,1,:p)"), {"u": usuario_id, "c": company_id, "t": 1 if todos else 0, "p": 1 if principal else 0})
        for projeto_id in projeto_ids:
            self.db.execute(text(
                "INSERT INTO usuario_projeto_acessos (usuario_id,projeto_id,ativo) VALUES (:u,:p,1)"),
                {"u": usuario_id, "p": projeto_id})
        self.db.commit()

    def cenario_x(self):
        """Empresa A completa + Empresa B restrita a B1."""
        self.dar_acesso(USUARIO_X, EMPRESA_A, todos=True, principal=True)
        self.dar_acesso(USUARIO_X, EMPRESA_B, todos=False, projeto_ids=[B1])

    def como(self, user_id, company_id, perfil="Gerente"):
        self.atual = usuario(user_id, company_id, perfil)

    def ids_de(self, resposta):
        return sorted(item["id"] for item in resposta.json())

    # --- §49/§50 ---------------------------------------------------------
    def test_ACL01_listagem_traz_projetos_das_duas_empresas_menos_o_nao_autorizado(self):
        self.cenario_x()
        r = self.client.get("/projetos/")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.ids_de(r), [A1, A2, B1])
        self.assertNotIn(B2, self.ids_de(r))

    def test_ACL02_acesso_direto_por_projeto(self):
        self.cenario_x()
        for projeto_id in (A1, A2, B1):
            self.assertEqual(self.client.get(f"/projetos/{projeto_id}").status_code, 200, projeto_id)
        # §52: empresa acessivel, projeto NAO autorizado -> 404 (nao 403).
        self.assertEqual(self.client.get(f"/projetos/{B2}").status_code, 404)

    def test_ACL03_pesquisa_cross_tenant_autorizada_funciona(self):
        """§51: empresa principal do usuario e A, mas B1 e da Empresa B."""
        self.cenario_x()
        r = self.client.get(f"/projetos/{B1}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["id"], B1)
        # O projeto acessado e da Empresa B, enquanto a empresa PRINCIPAL do
        # usuario continua sendo a A: e exatamente isso que a ADR-024 permite.
        self.assertEqual(
            self.db.query(models.Projeto).filter(models.Projeto.id == B1).first().company_id,
            EMPRESA_B,
        )
        self.assertEqual(self.atual.company_id, EMPRESA_A)
        self.assertTrue(acessos.usuario_tem_acesso_projeto(self.db, self.atual, B1))
        self.assertFalse(acessos.usuario_tem_acesso_projeto(self.db, self.atual, B2))

    def test_ACL04_revogacao_vale_com_o_mesmo_token(self):
        """§53: a ACL e lida do banco a cada request, nao do JWT."""
        self.cenario_x()
        self.assertEqual(self.client.get(f"/projetos/{B1}").status_code, 200)
        self.db.execute(text(
            "DELETE FROM usuario_projeto_acessos WHERE usuario_id = :u AND projeto_id = :p"),
            {"u": USUARIO_X, "p": B1})
        self.db.commit()
        # Mesmo objeto de usuario (mesma sessao/token): passa a 404.
        self.assertEqual(self.client.get(f"/projetos/{B1}").status_code, 404)
        self.assertEqual(self.ids_de(self.client.get("/projetos/")), [A1, A2])

    def test_ACL05_acesso_total_alcanca_projeto_futuro(self):
        """§54: com acesso_todos_projetos, projeto novo entra sem nova linha."""
        self.dar_acesso(USUARIO_X, EMPRESA_B, todos=True)
        self.assertEqual(self.ids_de(self.client.get("/projetos/")), [B1, B2])
        self.db.execute(text(
            "INSERT INTO projetos (id,nome,status,coordenador_id,company_id)"
            f" VALUES (203,'B3 novo','Ativo',{GERENTE_A},{EMPRESA_B})"))
        self.db.commit()
        self.assertEqual(self.ids_de(self.client.get("/projetos/")), [B1, B2, 203])
        self.assertEqual(
            self.db.query(models.UsuarioProjetoAcesso).count(), 0, "nao cria linha por projeto"
        )

    def test_ACL06_empresa_desativada_bloqueia_ate_projeto_autorizado(self):
        self.cenario_x()
        self.db.execute(text(
            "UPDATE usuario_empresa_acessos SET ativo = 0 WHERE usuario_id = :u AND company_id = :c"),
            {"u": USUARIO_X, "c": EMPRESA_B})
        self.db.commit()
        self.assertEqual(self.ids_de(self.client.get("/projetos/")), [A1, A2])
        self.assertEqual(self.client.get(f"/projetos/{B1}").status_code, 404)

    def test_ACL07_sem_ACL_vale_o_legado_mas_revogacao_explicita_nao(self):
        """Compatibilidade x revogacao.

        Usuario SEM nenhuma linha de ACL (backfill nao rodou) continua com o
        comportamento antigo -- ninguem fica trancado do lado de fora. Revogar
        e diferente: `PUT` com lista vazia deixa vinculo INATIVO, e a partir dai
        a ACL manda (nada de recair no legado).
        """
        self.assertEqual(
            self.db.query(models.UsuarioEmpresaAcesso)
            .filter(models.UsuarioEmpresaAcesso.usuario_id == USUARIO_X).count(), 0)
        # Legado: enxerga a propria empresa principal, como antes da ADR-024.
        self.assertEqual(self.ids_de(self.client.get("/projetos/")), [A1, A2])
        self.assertEqual(self.client.get(f"/projetos/{B1}").status_code, 404)

        # Revogacao explicita pelo Superadmin.
        self.como(SUPERADMIN, EMPRESA_A, perfil="Superadmin")
        r = self.client.put(f"/admin/usuarios/{USUARIO_X}/acessos",
                            json={"empresa_principal_id": None, "empresas": []})
        self.assertEqual(r.status_code, 200, r.text)
        self.como(USUARIO_X, EMPRESA_A)
        self.assertEqual(self.ids_de(self.client.get("/projetos/")), [])
        self.assertEqual(self.client.get(f"/projetos/{A1}").status_code, 404)
        marca = (
            self.db.query(models.UsuarioEmpresaAcesso)
            .filter(models.UsuarioEmpresaAcesso.usuario_id == USUARIO_X).all()
        )
        self.assertEqual([(m.company_id, m.ativo) for m in marca], [(EMPRESA_A, False)])

    def test_ACL07b_backfill_substitui_o_legado_sem_mudar_o_alcance(self):
        self.dar_acesso(USUARIO_X, EMPRESA_A, todos=True, principal=True)
        self.assertEqual(self.ids_de(self.client.get("/projetos/")), [A1, A2])

    def test_ACL08_superadmin_enxerga_tudo_sem_vinculo(self):
        """§11/§55: nenhuma linha de ACL para o Superadmin."""
        self.como(SUPERADMIN, EMPRESA_A, perfil="Superadmin")
        self.assertEqual(self.ids_de(self.client.get("/projetos/")), [A1, A2, B1, B2])
        self.assertEqual(
            self.db.query(models.UsuarioEmpresaAcesso)
            .filter(models.UsuarioEmpresaAcesso.usuario_id == SUPERADMIN).count(), 0)
        self.assertEqual(
            sorted(acessos.listar_company_ids_acessiveis(self.db, self.atual)), [EMPRESA_A, EMPRESA_B])

    # --- §31/§64 tenant do dado -----------------------------------------
    def test_ACL09_coleta_grava_o_tenant_do_PROJETO_e_nao_o_do_usuario(self):
        self.cenario_x()
        self.como(AGENTE, EMPRESA_A, perfil="Agente")
        self.dar_acesso(AGENTE, EMPRESA_A, todos=True, principal=True)
        self.dar_acesso(AGENTE, EMPRESA_B, todos=False, projeto_ids=[B1])

        def coletar(pesquisa_id, pergunta_id):
            r = self.client.post(f"/pesquisas/{pesquisa_id}/coletas/", json={
                "client_uuid": str(uuid4()),
                "data_inicio_coleta": "2026-08-27T12:00:00Z",
                "respostas": [{"pergunta_id": pergunta_id, "valor_resposta": "sim"}],
            })
            self.assertEqual(r.status_code, 201, r.text)
            return r.json()["id"]

        id_a = coletar(1001, 11)   # Pesquisa do Projeto A1 (Empresa A)
        id_b = coletar(2001, 21)   # Pesquisa do Projeto B1 (Empresa B)

        linhas = dict(self.db.execute(text(
            "SELECT id, company_id FROM coletas WHERE id IN (:a,:b)"), {"a": id_a, "b": id_b}).fetchall())
        self.assertEqual(linhas[id_a], EMPRESA_A)
        self.assertEqual(linhas[id_b], EMPRESA_B, "coleta do projeto B nao pode ficar com a empresa principal do agente")
        agentes = {linha[0] for linha in self.db.execute(text(
            "SELECT agente_id FROM coletas WHERE id IN (:a,:b)"), {"a": id_a, "b": id_b}).fetchall()}
        self.assertEqual(agentes, {AGENTE})

    def test_ACL10_helper_de_tenant_deriva_da_pesquisa(self):
        self.assertEqual(acessos.company_id_da_pesquisa(self.db, 1001), EMPRESA_A)
        self.assertEqual(acessos.company_id_da_pesquisa(self.db, 2001), EMPRESA_B)
        self.assertIsNone(acessos.company_id_da_pesquisa(self.db, 999999))

    # --- §20/§21/§56/§57 administracao ----------------------------------
    def test_ACL11_get_e_put_de_acessos(self):
        self.cenario_x()
        self.como(SUPERADMIN, EMPRESA_A, perfil="Superadmin")
        r = self.client.get(f"/admin/usuarios/{USUARIO_X}/acessos")
        self.assertEqual(r.status_code, 200, r.text)
        corpo = r.json()
        self.assertEqual(corpo["usuario_id"], USUARIO_X)
        self.assertEqual(corpo["empresa_principal_id"], EMPRESA_A)
        por_empresa = {item["company_id"]: item for item in corpo["empresas"]}
        self.assertTrue(por_empresa[EMPRESA_A]["acesso_todos_projetos"])
        self.assertEqual(por_empresa[EMPRESA_B]["projeto_ids"], [B1])
        self.assertEqual(por_empresa[EMPRESA_A]["company_nome"], "Empresa QA A")

        r = self.client.put(f"/admin/usuarios/{USUARIO_X}/acessos", json={
            "empresa_principal_id": EMPRESA_B,
            "empresas": [
                {"company_id": EMPRESA_A, "acesso_todos_projetos": False, "projeto_ids": [A1]},
                {"company_id": EMPRESA_B, "acesso_todos_projetos": True, "projeto_ids": []},
            ],
        })
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["empresa_principal_id"], EMPRESA_B)
        self.como(USUARIO_X, EMPRESA_B)
        self.assertEqual(self.ids_de(self.client.get("/projetos/")), [A1, B1, B2])
        # A empresa principal do cadastro acompanhou a ACL.
        self.assertEqual(
            self.db.query(models.Usuario).filter(models.Usuario.id == USUARIO_X).first().company_id,
            EMPRESA_B,
        )

    def test_ACL12_put_rejeita_projeto_de_outra_empresa_sem_alterar_nada(self):
        self.cenario_x()
        self.como(SUPERADMIN, EMPRESA_A, perfil="Superadmin")
        antes = self.client.get(f"/admin/usuarios/{USUARIO_X}/acessos").json()
        r = self.client.put(f"/admin/usuarios/{USUARIO_X}/acessos", json={
            "empresa_principal_id": EMPRESA_A,
            # §57: B1 pertence a Empresa B, nao a A.
            "empresas": [{"company_id": EMPRESA_A, "acesso_todos_projetos": False, "projeto_ids": [B1]}],
        })
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("nao pertence", r.json()["detail"])
        self.assertEqual(self.client.get(f"/admin/usuarios/{USUARIO_X}/acessos").json(), antes)

    def test_ACL13_put_valida_payload_e_nunca_grava_parcial(self):
        self.cenario_x()
        self.como(SUPERADMIN, EMPRESA_A, perfil="Superadmin")
        antes = self.client.get(f"/admin/usuarios/{USUARIO_X}/acessos").json()
        casos = [
            ({"empresa_principal_id": 999, "empresas": [{"company_id": EMPRESA_A, "acesso_todos_projetos": True, "projeto_ids": []}]}, "principal fora da lista"),
            ({"empresa_principal_id": EMPRESA_A, "empresas": [{"company_id": 999, "acesso_todos_projetos": True, "projeto_ids": []}, {"company_id": EMPRESA_A, "acesso_todos_projetos": True, "projeto_ids": []}]}, "empresa inexistente"),
            ({"empresa_principal_id": EMPRESA_A, "empresas": [{"company_id": EMPRESA_A, "acesso_todos_projetos": False, "projeto_ids": [A1, A1]}]}, "projeto repetido"),
            ({"empresa_principal_id": EMPRESA_A, "empresas": [{"company_id": EMPRESA_A, "acesso_todos_projetos": False, "projeto_ids": [99999]}]}, "projeto inexistente"),
            ({"empresa_principal_id": EMPRESA_A, "empresas": [{"company_id": EMPRESA_A, "acesso_todos_projetos": True, "projeto_ids": []}, {"company_id": EMPRESA_A, "acesso_todos_projetos": True, "projeto_ids": []}]}, "empresa repetida"),
        ]
        for payload, rotulo in casos:
            r = self.client.put(f"/admin/usuarios/{USUARIO_X}/acessos", json=payload)
            self.assertEqual(r.status_code, 422, f"{rotulo}: {r.text}")
            self.assertEqual(self.client.get(f"/admin/usuarios/{USUARIO_X}/acessos").json(), antes, rotulo)
        # extra="forbid": campo desconhecido tambem e recusado.
        r = self.client.put(f"/admin/usuarios/{USUARIO_X}/acessos", json={
            "empresa_principal_id": EMPRESA_A,
            "empresas": [{"company_id": EMPRESA_A, "acesso_todos_projetos": True, "projeto_ids": [], "tenant_id": 3}],
        })
        self.assertEqual(r.status_code, 422)

    def test_ACL14_apenas_superadmin_administra_acl(self):
        """§22/§56: gerente nao pode conceder acesso a outra empresa."""
        rota = rotas_usuarios.admin_router
        acl = [r for r in rota.routes if str(getattr(r, "path", "")).endswith("/acessos")]
        self.assertEqual(len(acl), 2)
        for r in acl:
            fonte = str(r.dependant.dependencies) + str(getattr(r, "dependencies", ""))
            self.assertIn("require_superadmin", fonte + str(r.endpoint.__code__.co_names))

    def test_ACL15_me_expoe_empresas_sem_quebrar_contrato(self):
        """§19: `company_id` continua; `company_ids`/`multiempresa` sao aditivos."""
        self.cenario_x()
        r = self.client.get("/usuarios/me/")
        self.assertEqual(r.status_code, 200, r.text)
        corpo = r.json()
        self.assertEqual(corpo["company_id"], EMPRESA_A)
        self.assertEqual(sorted(corpo["company_ids"]), [EMPRESA_A, EMPRESA_B])
        self.assertTrue(corpo["multiempresa"])
        for chave in ("id", "email", "perfil_id", "ativo"):
            self.assertIn(chave, corpo)
        # Sem arvore de projetos no /me/.
        self.assertNotIn("projetos", corpo)

    def test_ACL16_filtro_e_resolvido_em_SQL_sem_carregar_tudo(self):
        """§67: uma consulta com subquery, nao N consultas nem filtro em Python."""
        self.cenario_x()
        consultas = []
        listener = lambda conn, cursor, stmt, params, ctx, many: consultas.append(stmt)
        event.listen(self.engine, "before_cursor_execute", listener)
        try:
            self.client.get("/projetos/")
        finally:
            event.remove(self.engine, "before_cursor_execute", listener)
        selects_projeto = [s for s in consultas if "FROM projetos" in s]
        self.assertTrue(selects_projeto)
        self.assertTrue(
            any("usuario_empresa_acessos" in s for s in selects_projeto),
            "a ACL precisa entrar na propria query de projetos",
        )


if __name__ == "__main__":
    unittest.main()
