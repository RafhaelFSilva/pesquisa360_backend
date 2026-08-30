"""ADR-038 -- documentacao OpenAPI restrita por ambiente.

Em producao `/docs`, `/redoc` e `/openapi.json` NAO sao registradas: a defesa e
a ausencia da rota, nao uma senha ou um perfil na frente dela. Desligar a
documentacao nao pode desligar a API -- o que estes testes provam nos dois
sentidos.
"""
import importlib
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ.setdefault("SECRET_KEY", "test-only-docs-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("UPLOAD_DIRECTORY", "/tmp/uploads")
os.environ.setdefault("UPLOAD_MAX_SIZE_BYTES", "10485760")

from pesquisa360.core import ambiente

ROTAS_DOC = ("/docs", "/redoc", "/openapi.json")


def app_com_ambiente(valor):
    """Recria a aplicacao com `APP_ENV` fixado.

    `main` monta o `app` na importacao, entao o recarregamento e o jeito
    honesto de observar o efeito real -- e nao apenas o helper isolado.
    """
    env = dict(os.environ)
    if valor is None:
        env.pop("APP_ENV", None)
    else:
        env["APP_ENV"] = valor
    with patch.dict(os.environ, env, clear=True):
        import pesquisa360.main as main

        importlib.reload(main)
        return main.app


class OpcoesDocumentacaoTests(unittest.TestCase):
    """Helper puro: sem servidor, sem recarregar modulo."""

    def test_producao_desliga_as_TRES_juntas(self):
        opcoes = ambiente.opcoes_documentacao(producao=True)
        self.assertEqual(opcoes, {"docs_url": None, "redoc_url": None, "openapi_url": None})
        # openapi_url junto: sozinho ele ja enumera a API inteira.
        self.assertIsNone(opcoes["openapi_url"])

    def test_desenvolvimento_mantem_os_caminhos_padrao(self):
        self.assertEqual(
            ambiente.opcoes_documentacao(producao=False),
            {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"},
        )

    def test_ambiente_vem_de_APP_ENV_e_o_default_e_desenvolvimento(self):
        casos = {
            None: False, "": False, "development": False, "dev": False, "staging": False,
            "production": True, "PRODUCTION": True, " Production ": True, "prod": True, "producao": True,
        }
        for valor, esperado_producao in casos.items():
            env = dict(os.environ)
            env.pop("APP_ENV", None)
            if valor is not None:
                env["APP_ENV"] = valor
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(ambiente.is_producao(), esperado_producao, repr(valor))

    def test_ambiente_nao_e_inferido_de_host_porta_ou_docker(self):
        """Ambiente e configuracao explicita, nunca palpite.

        Analise da ARVORE do modulo, nao do texto: a docstring cita `hostname`
        justamente para dizer que nao o usa.
        """
        import ast

        arvore = ast.parse(open("pesquisa360/core/ambiente.py", encoding="utf-8").read())
        importados = {
            alias.name.split(".")[0]
            for no in ast.walk(arvore)
            if isinstance(no, (ast.Import, ast.ImportFrom))
            for alias in no.names
        }
        # Nada de socket/platform/subprocess: so leitura de variavel de ambiente.
        self.assertEqual(importados - {"annotations"}, {"os"}, importados)

        lidas = {
            no.args[0].value
            for no in ast.walk(arvore)
            if isinstance(no, ast.Call)
            and isinstance(no.func, ast.Attribute)
            and no.func.attr in {"getenv", "get"}
            and no.args
            and isinstance(no.args[0], ast.Constant)
        }
        self.assertEqual(lidas, {"APP_ENV"}, "so APP_ENV decide o ambiente")

class DocumentacaoEmProducaoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_com_ambiente("production")

    @classmethod
    def tearDownClass(cls):
        # Deixa o modulo no estado padrao para os demais testes da suite.
        app_com_ambiente(None)

    def setUp(self):
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def test_docs_redoc_e_openapi_respondem_404(self):
        for rota in ROTAS_DOC:
            resposta = self.client.get(rota)
            self.assertEqual(resposta.status_code, 404, f"{rota} -> {resposta.status_code}")
            # 404 porque a rota nao existe -- nao e login, nem 401/403.
            self.assertNotIn("location", {k.lower() for k in resposta.headers})

    def test_variantes_com_barra_final_tambem_nao_servem_documentacao(self):
        for rota in ("/docs/", "/redoc/", "/openapi.json/"):
            resposta = self.client.get(rota, follow_redirects=True)
            self.assertEqual(resposta.status_code, 404, rota)
            self.assertNotIn("swagger", resposta.text.lower())

    def test_as_rotas_nao_estao_sequer_registradas(self):
        caminhos = {getattr(r, "path", None) for r in self.app.routes}
        for rota in ROTAS_DOC:
            self.assertNotIn(rota, caminhos, rota)
        self.assertIsNone(self.app.openapi_url)
        self.assertIsNone(self.app.docs_url)
        self.assertIsNone(self.app.redoc_url)

    def test_a_API_operacional_continua_registrada(self):
        """Desligar OpenAPI != desligar endpoints."""
        caminhos = {getattr(r, "path", None) for r in self.app.routes}
        for rota in ("/login/token", "/usuarios/me/", "/projetos/", "/agente/pesquisas/"):
            self.assertIn(rota, caminhos, rota)

    def test_login_e_rota_protegida_seguem_respondendo(self):
        # Sem credencial valida o login recusa -- mas a rota EXISTE (nao e 404).
        resposta = self.client.post("/login/token", data={"username": "x@y.com", "password": "z"})
        self.assertNotEqual(resposta.status_code, 404)
        self.assertIn(resposta.status_code, (401, 422, 500))
        # Rota protegida sem token: 401, e nunca 404.
        me = self.client.get("/usuarios/me/")
        self.assertEqual(me.status_code, 401, me.text)


class DocumentacaoEmDesenvolvimentoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_com_ambiente("development")

    @classmethod
    def tearDownClass(cls):
        app_com_ambiente(None)

    def setUp(self):
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def test_docs_e_redoc_disponiveis(self):
        for rota in ("/docs", "/redoc"):
            resposta = self.client.get(rota)
            self.assertEqual(resposta.status_code, 200, rota)
            self.assertIn("text/html", resposta.headers.get("content-type", ""))

    def test_openapi_json_valido(self):
        resposta = self.client.get("/openapi.json")
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("application/json", resposta.headers.get("content-type", ""))
        corpo = resposta.json()
        self.assertIn("openapi", corpo)
        self.assertIn("paths", corpo)
        self.assertEqual(corpo["info"]["title"], "Pesquisa360 API")
        # O contrato documentado continua sendo o da API real.
        self.assertIn("/login/token", corpo["paths"])

    def test_sem_APP_ENV_o_comportamento_e_o_de_hoje(self):
        """Compatibilidade: quem nao configurar nada continua com Swagger."""
        app = app_com_ambiente(None)
        with TestClient(app, raise_server_exceptions=False) as client:
            self.assertEqual(client.get("/docs").status_code, 200)
            self.assertEqual(client.get("/openapi.json").status_code, 200)


if __name__ == "__main__":
    unittest.main()
