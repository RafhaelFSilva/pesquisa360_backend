"""Sessao resiliente: access curto renovado por refresh token (FASE E.3).

A regra que estes testes protegem nao e "existe um endpoint de refresh", e sim
a SEPARACAO entre dois estados que antes eram um so:

    access expirado  -> o cliente renova sozinho, o usuario nem percebe
    refresh invalido -> a sessao acabou, e ai sim volta para o login

Como access e refresh sao assinados com a mesma chave, a fronteira entre eles e
uma unica claim. Boa parte do arquivo existe para garantir que essa fronteira
nao vaza em nenhuma direcao.
"""

import os
import unittest
from datetime import timedelta

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SECRET_KEY", "test-only-sessao-refresh-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.api.endpoints import login as login_endpoint
from pesquisa360.core import security
from pesquisa360.core.dependencies import get_current_user, get_db
from tests.acl_fixture import criar_tabelas_acl

SENHA = "correct-password"


class SessaoRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        criar_tabelas_acl(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)
        cls.senha_hash = security.get_password_hash(SENHA)
        with cls.engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT, cnpj TEXT,"
                " logo_url TEXT, is_active BOOLEAN, created_at DATETIME)"
            ))
            connection.execute(text(
                "CREATE TABLE perfis (id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT)"
            ))
            connection.execute(text(
                "CREATE TABLE usuarios (id INTEGER PRIMARY KEY, email TEXT, nome TEXT,"
                " senha_hash TEXT, ativo BOOLEAN, perfil_id INTEGER, company_id INTEGER)"
            ))

        cls.app = FastAPI()
        cls.app.include_router(login_endpoint.router, prefix="/login")

        # Endpoint protegido minimo: o alvo real e `get_current_user`, nao a
        # regra de negocio de nenhuma rota especifica.
        @cls.app.get("/protegido")
        def protegido(usuario=Depends(get_current_user)):
            return {"email": usuario.email}

        def override_get_db():
            db = cls.Session()
            try:
                yield db
            finally:
                db.close()

        cls.app.dependency_overrides[get_db] = override_get_db
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.engine.dispose()

    def setUp(self):
        with self.engine.begin() as connection:
            for tabela in ("usuarios", "perfis", "companies"):
                connection.execute(text(f"DELETE FROM {tabela}"))
            connection.execute(text(
                "INSERT INTO companies (id, name, is_active) VALUES (10, 'Tenant A', 1)"
            ))
            connection.execute(text(
                "INSERT INTO perfis (id, nome) VALUES (7, 'Agente'), (42, 'Gerente')"
            ))
            connection.execute(text(
                "INSERT INTO usuarios (id, email, nome, senha_hash, ativo, perfil_id, company_id)"
                " VALUES (2, 'agente@example.com', 'Agente', :senha, 1, 7, 10)"
            ), {"senha": self.senha_hash})

    # --- utilidades ----------------------------------------------------------

    def fazer_login(self, email="agente@example.com", senha=SENHA):
        return self.client.post(
            "/login/token",
            data={"username": email, "password": senha},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    def sessao(self):
        resposta = self.fazer_login()
        self.assertEqual(resposta.status_code, 200)
        return resposta.json()

    def renovar(self, refresh_token):
        return self.client.post("/login/refresh", json={"refresh_token": refresh_token})

    def acessar(self, token):
        return self.client.get("/protegido", headers={"Authorization": f"Bearer {token}"})

    def desativar_usuario(self):
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE usuarios SET ativo = 0 WHERE id = 2"))

    # --- contrato do login ---------------------------------------------------

    def test_login_preserva_contrato_antigo_e_acrescenta_refresh(self):
        corpo = self.sessao()

        # Campos que um cliente anterior a esta fase ja lia.
        self.assertIn("access_token", corpo)
        self.assertEqual(corpo["token_type"], "bearer")
        # Aditivos.
        self.assertIn("refresh_token", corpo)
        self.assertEqual(corpo["expires_in"], security.access_token_expires_in())
        self.assertNotEqual(corpo["access_token"], corpo["refresh_token"])

    def test_login_com_senha_errada_continua_401(self):
        self.assertEqual(self.fazer_login(senha="errada").status_code, 401)

    def test_access_recem_emitido_abre_endpoint_protegido(self):
        corpo = self.sessao()
        resposta = self.acessar(corpo["access_token"])

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["email"], "agente@example.com")

    # --- renovacao -----------------------------------------------------------

    def test_refresh_valido_devolve_sessao_nova_e_utilizavel(self):
        corpo = self.sessao()

        renovado = self.renovar(corpo["refresh_token"])
        self.assertEqual(renovado.status_code, 200)
        novo = renovado.json()

        self.assertIn("access_token", novo)
        self.assertIn("refresh_token", novo)
        self.assertEqual(novo["token_type"], "bearer")
        # O access novo tem que funcionar de verdade, nao so existir.
        self.assertEqual(self.acessar(novo["access_token"]).status_code, 200)
        # E o refresh novo tambem renova (rotacao encadeada).
        self.assertEqual(self.renovar(novo["refresh_token"]).status_code, 200)

    def test_access_expirado_e_401_e_o_refresh_o_recupera(self):
        """O ciclo inteiro da fase, em um teste.

        TTL controlado pelo proprio token, sem mexer no relogio do container.
        """
        corpo = self.sessao()
        access_vencido = security.create_access_token(
            data={"sub": "agente@example.com"},
            expires_delta=timedelta(seconds=-1),
        )

        self.assertEqual(self.acessar(access_vencido).status_code, 401)

        renovado = self.renovar(corpo["refresh_token"])
        self.assertEqual(renovado.status_code, 200)
        self.assertEqual(self.acessar(renovado.json()["access_token"]).status_code, 200)

    def test_refresh_expirado_e_401(self):
        vencido = security.create_refresh_token(
            data={"sub": "agente@example.com"},
            expires_delta=timedelta(seconds=-1),
        )

        resposta = self.renovar(vencido)
        self.assertEqual(resposta.status_code, 401)
        self.assertIn("detail", resposta.json())

    # --- fronteira entre os dois tipos ---------------------------------------

    def test_access_nao_serve_como_refresh(self):
        corpo = self.sessao()

        self.assertEqual(self.renovar(corpo["access_token"]).status_code, 401)

    def test_refresh_nao_autentica_endpoint_comum(self):
        corpo = self.sessao()

        self.assertEqual(self.acessar(corpo["refresh_token"]).status_code, 401)

    def test_token_com_type_desconhecido_nao_e_aceito_em_lugar_nenhum(self):
        from datetime import datetime, timezone

        from jose import jwt

        forjado = jwt.encode(
            {
                "sub": "agente@example.com",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
                security.TOKEN_TYPE_CLAIM: "outra_coisa",
            },
            security.SECRET_KEY,
            algorithm=security.ALGORITHM,
        )

        self.assertEqual(self.acessar(forjado).status_code, 401)
        self.assertEqual(self.renovar(forjado).status_code, 401)

    def test_token_malformado_e_401_e_nao_500(self):
        self.assertEqual(self.renovar("abc").status_code, 401)
        # String vazia e um `str` valido para o schema: nao e erro de corpo,
        # e credencial invalida -- 401, como qualquer outro token que nao abre.
        self.assertEqual(self.renovar("").status_code, 401)
        self.assertEqual(self.renovar("a.b.c").status_code, 401)
        self.assertEqual(self.acessar("abc").status_code, 401)

    def test_refresh_assinado_com_outra_chave_e_401(self):
        from datetime import datetime, timezone

        from jose import jwt

        intruso = jwt.encode(
            {
                "sub": "agente@example.com",
                "exp": datetime.now(timezone.utc) + timedelta(days=1),
                security.TOKEN_TYPE_CLAIM: security.REFRESH_TOKEN_TYPE,
            },
            "chave-que-nao-e-a-do-servidor",
            algorithm=security.ALGORITHM,
        )

        self.assertEqual(self.renovar(intruso).status_code, 401)

    # --- janela de compatibilidade -------------------------------------------

    def test_token_legado_sem_claim_de_tipo_ainda_autentica(self):
        """Sessoes emitidas antes da E.3 nao podem cair todas de uma vez."""
        from datetime import datetime, timezone

        from jose import jwt

        legado = jwt.encode(
            {
                "sub": "agente@example.com",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            },
            security.SECRET_KEY,
            algorithm=security.ALGORITHM,
        )

        self.assertEqual(self.acessar(legado).status_code, 200)

    def test_token_legado_sem_claim_NAO_serve_como_refresh(self):
        """O outro lado da compatibilidade, que e o lado perigoso.

        Se ausencia de claim valesse como refresh, todo access antigo em
        circulacao viraria uma credencial de renovacao de 30 dias.
        """
        from datetime import datetime, timezone

        from jose import jwt

        legado = jwt.encode(
            {
                "sub": "agente@example.com",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            },
            security.SECRET_KEY,
            algorithm=security.ALGORITHM,
        )

        self.assertEqual(self.renovar(legado).status_code, 401)

    # --- usuario que perdeu acesso -------------------------------------------

    def test_usuario_desativado_nao_renova(self):
        corpo = self.sessao()
        self.desativar_usuario()

        resposta = self.renovar(corpo["refresh_token"])
        self.assertEqual(resposta.status_code, 401)

    def test_empresa_desativada_nao_renova(self):
        corpo = self.sessao()
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE companies SET is_active = 0 WHERE id = 10"))

        self.assertEqual(self.renovar(corpo["refresh_token"]).status_code, 401)

    def test_usuario_removido_nao_renova(self):
        corpo = self.sessao()
        with self.engine.begin() as connection:
            connection.execute(text("DELETE FROM usuarios WHERE id = 2"))

        self.assertEqual(self.renovar(corpo["refresh_token"]).status_code, 401)

    # --- higiene -------------------------------------------------------------

    def test_ttl_do_refresh_e_bem_maior_que_o_do_access(self):
        """Refresh curto demais traria de volta o logout manual que a fase remove."""
        self.assertGreater(
            security.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60,
            security.ACCESS_TOKEN_EXPIRE_MINUTES,
        )

    def test_resposta_de_erro_nao_devolve_o_token_enviado(self):
        segredo = security.create_access_token(data={"sub": "agente@example.com"})

        corpo = self.renovar(segredo).text

        self.assertNotIn(segredo, corpo)


if __name__ == "__main__":
    unittest.main()
