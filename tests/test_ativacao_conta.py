"""Cadastro por convite, ativacao de conta e primeira senha.

Fluxo coberto de ponta a ponta com banco real (Alembic ate o head):

    admin cria usuario (sem senha)  -> conta INATIVA + token de uso unico
    e-mail leva o token PURO        -> banco guarda so o SHA-256
    usuario define a primeira senha -> conta ATIVA, token consumido
    login com a nova senha          -> autoriza
    segundo uso do mesmo token      -> recusado
"""
import hashlib
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-ativacao-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360 import schemas
from pesquisa360.api.endpoints import login as rotas_login
from pesquisa360.api.endpoints import usuarios as rotas_usuarios
from pesquisa360.core import security
from pesquisa360.core.dependencies import (
    get_current_user,
    get_db,
    require_manager_or_superadmin,
    require_superadmin,
)
from pesquisa360.core.password_policy import erro_da_senha, senha_valida
from pesquisa360.db import models
from pesquisa360.services import ativacao
from tests.test_base_eleitoral_import import run_alembic_upgrade

EMPRESA_A, EMPRESA_B = 10, 20
GERENTE, SUPERADMIN, AGENTE_ATIVO = 1, 9, 3
PERFIL_GERENTE, PERFIL_AGENTE, PERFIL_SUPER = 1, 2, 3


def usuario(user_id, company_id, perfil="Gerente"):
    return SimpleNamespace(
        id=user_id, email=f"u{user_id}@qa.com", company_id=company_id, ativo=True,
        perfil_id={"Gerente": PERFIL_GERENTE, "Agente": PERFIL_AGENTE, "Superadmin": PERFIL_SUPER}[perfil],
        perfil=SimpleNamespace(nome=perfil), perfil_nome=perfil,
    )


# =============================================================================
# Politica de senha (funcao pura, reutilizavel na futura recuperacao de senha)
# =============================================================================
class PoliticaSenhaTests(unittest.TestCase):
    def test_exemplos_do_enunciado(self):
        self.assertTrue(senha_valida("abc123"))
        self.assertTrue(senha_valida("pesquisa9"))
        self.assertFalse(senha_valida("123456"))   # sem letra
        self.assertFalse(senha_valida("abcdef"))   # sem numero
        self.assertFalse(senha_valida("a12"))      # curta

    def test_mensagens_explicam_o_requisito_sem_citar_hashing(self):
        self.assertIn("6 caracteres", erro_da_senha("a12"))
        self.assertIn("letra e um numero", erro_da_senha("abcdef"))
        self.assertIn("letra e um numero", erro_da_senha("123456"))
        self.assertIsNone(erro_da_senha("abc123"))
        for entrada in ("a12", "abcdef", "123456", None, ""):
            mensagem = erro_da_senha(entrada) or ""
            for proibido in ("bcrypt", "hash", "passlib", "salt"):
                self.assertNotIn(proibido, mensagem.lower())

    def test_borda_de_tamanho_e_entradas_vazias(self):
        self.assertFalse(senha_valida("abcd1"))    # 5
        self.assertTrue(senha_valida("abcd12"))    # 6
        self.assertFalse(senha_valida(None))
        self.assertFalse(senha_valida(""))


# =============================================================================
# Fluxo completo
# =============================================================================
class AtivacaoContaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._dir = TemporaryDirectory()
        cls.db_path = Path(cls._dir.name) / "ativacao.sqlite"
        run_alembic_upgrade(f"sqlite:///{cls.db_path.as_posix()}")

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def setUp(self):
        self.engine = create_engine(f"sqlite:///{self.db_path.as_posix()}")
        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as c:
            for tabela in ("user_activation_tokens", "usuario_projeto_acessos",
                           "usuario_empresa_acessos", "usuarios", "perfis", "companies"):
                c.execute(text(f"DELETE FROM {tabela}"))
            c.execute(text(
                f"INSERT INTO companies (id,name,is_active) VALUES ({EMPRESA_A},'Empresa A',1),({EMPRESA_B},'Empresa B',1)"))
            c.execute(text(
                f"INSERT INTO perfis (id,nome) VALUES ({PERFIL_GERENTE},'Gerente'),({PERFIL_AGENTE},'Agente'),({PERFIL_SUPER},'Superadmin')"))
            c.execute(text(
                "INSERT INTO usuarios (id,email,nome,senha_hash,ativo,perfil_id,company_id) VALUES "
                f"({GERENTE},'gerente@qa.com','Gerente','{security.get_password_hash('antiga123')}',1,{PERFIL_GERENTE},{EMPRESA_A}),"
                f"({SUPERADMIN},'root@qa.com','Root','{security.get_password_hash('root123')}',1,{PERFIL_SUPER},{EMPRESA_A}),"
                f"({AGENTE_ATIVO},'antigo@qa.com','Antigo','{security.get_password_hash('legado123')}',1,{PERFIL_AGENTE},{EMPRESA_A})"))
        self.db = self.Session()
        self.addCleanup(self.db.close)

        self.app = FastAPI()
        self.app.include_router(rotas_usuarios.router, prefix="/usuarios")
        self.app.include_router(rotas_usuarios.admin_router)
        self.app.include_router(rotas_login.router, prefix="/login")
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.atual = usuario(GERENTE, EMPRESA_A)
        self.app.dependency_overrides[get_current_user] = lambda: self.atual
        self.app.dependency_overrides[require_manager_or_superadmin] = lambda: self.atual
        self.app.dependency_overrides[require_superadmin] = lambda: self.atual
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

        # Nada de SMTP em teste: o envio e observado, nao executado.
        self.enviados = []
        patcher = patch(
            "pesquisa360.services.email.enviar_email",
            side_effect=lambda destinatario, assunto, corpo: (
                self.enviados.append({"destinatario": destinatario, "assunto": assunto, "corpo": corpo}) or True
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    # --- helpers ---------------------------------------------------------
    def convidar(self, email="novo@cliente.com", nome="João Silva", perfil_id=PERFIL_AGENTE, esperado=201, **extra):
        corpo = {"email": email, "nome": nome, "perfil_id": perfil_id, **extra}
        r = self.client.post("/usuarios/", json=corpo)
        self.assertEqual(r.status_code, esperado, r.text)
        return r

    def token_do_email(self):
        """Extrai o token do link -- como o convidado faria."""
        self.assertTrue(self.enviados, "nenhum e-mail registrado")
        corpo = self.enviados[-1]["corpo"]
        return corpo.split("token=")[1].split()[0].strip()

    def registro(self, usuario_id):
        return (
            self.db.query(models.UserActivationToken)
            .filter(models.UserActivationToken.usuario_id == usuario_id)
            .order_by(models.UserActivationToken.id.desc())
            .first()
        )

    def usuario_por_email(self, email):
        return self.db.query(models.Usuario).filter(models.Usuario.email == email).first()

    # --- criacao ---------------------------------------------------------
    def test_AT01_convite_cria_usuario_inativo_no_tenant_do_criador(self):
        self.convidar()
        criado = self.usuario_por_email("novo@cliente.com")
        self.assertIsNotNone(criado)
        self.assertFalse(criado.ativo, "conta convidada nasce inativa")
        self.assertEqual(criado.company_id, EMPRESA_A, "tenant vem do current_user")
        self.assertEqual(criado.perfil_id, PERFIL_AGENTE)
        self.assertIsNotNone(self.registro(criado.id))

    def test_AT02_payload_nao_escolhe_o_tenant(self):
        """Gerente tentando plantar a conta na Empresa B pelo payload."""
        self.convidar(email="intruso@cliente.com", company_id=EMPRESA_B)
        criado = self.usuario_por_email("intruso@cliente.com")
        self.assertEqual(criado.company_id, EMPRESA_A, "company_id do payload e ignorado")

    def test_AT03_email_duplicado_e_recusado_sem_criar_nem_convidar(self):
        antes = len(self.enviados)
        r = self.convidar(email="antigo@qa.com", esperado=400)
        self.assertIn("cadastrado", r.json()["detail"].lower())
        self.assertEqual(len(self.enviados), antes, "nao dispara convite em duplicidade")
        self.assertEqual(
            self.db.query(models.Usuario).filter(models.Usuario.email == "antigo@qa.com").count(), 1)

    def test_AT04_criacao_nao_e_anonima(self):
        """A rota exige sessao: sem override de autorizacao, nao passa."""
        app = FastAPI()
        app.include_router(rotas_usuarios.router, prefix="/usuarios")
        app.dependency_overrides[get_db] = lambda: self.db
        with TestClient(app) as anonimo:
            r = anonimo.post("/usuarios/", json={"email": "a@b.com", "nome": "X", "perfil_id": PERFIL_AGENTE})
            self.assertIn(r.status_code, (401, 403), r.text)
        self.assertIsNone(self.usuario_por_email("a@b.com"))

    # --- token -----------------------------------------------------------
    def test_AT05_banco_guarda_somente_o_hash_do_token(self):
        self.convidar()
        token = self.token_do_email()
        criado = self.usuario_por_email("novo@cliente.com")
        registro = self.registro(criado.id)
        self.assertEqual(registro.token_hash, hashlib.sha256(token.encode()).hexdigest())
        self.assertNotEqual(registro.token_hash, token)
        # O token puro nao aparece em lugar nenhum da tabela.
        linhas = self.db.execute(text("SELECT * FROM user_activation_tokens")).fetchall()
        for linha in linhas:
            self.assertNotIn(token, " ".join(str(v) for v in linha))
        self.assertGreater(len(token), 30, "token precisa de entropia alta")

    def test_AT06_email_de_convite_tem_link_e_nao_carrega_senha(self):
        self.convidar()
        mensagem = self.enviados[-1]
        self.assertEqual(mensagem["destinatario"], "novo@cliente.com")
        self.assertEqual(mensagem["assunto"], "Ative seu acesso ao Pesquisa360")
        self.assertIn("/ativar-conta?token=", mensagem["corpo"])
        self.assertIn("expira em 24 horas", mensagem["corpo"])
        self.assertIn("João Silva", mensagem["corpo"])
        for proibido in ("senha_hash", "senha temporaria", "password", "bcrypt"):
            self.assertNotIn(proibido, mensagem["corpo"].lower())

    def test_AT07_validacao_distingue_valido_invalido_expirado_e_usado(self):
        self.convidar()
        token = self.token_do_email()

        r = self.client.get("/usuarios/ativacao/validar", params={"token": token})
        self.assertEqual(r.status_code, 200, r.text)
        corpo = r.json()
        self.assertEqual(corpo["status"], "VALIDO")
        self.assertTrue(corpo["valido"])
        self.assertEqual(corpo["email"], "novo@cliente.com")
        for proibido in ("senha_hash", "token_hash", "id", "company_id", "perfil_id"):
            self.assertNotIn(proibido, corpo)

        self.assertEqual(self.client.get("/usuarios/ativacao/validar", params={"token": "nao-existe"}).json()["status"], "INVALIDO")
        self.assertEqual(self.client.get("/usuarios/ativacao/validar").json()["status"], "INVALIDO")

        # Expirado.
        criado = self.usuario_por_email("novo@cliente.com")
        registro = self.registro(criado.id)
        registro.expira_em = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.db.commit()
        self.assertEqual(self.client.get("/usuarios/ativacao/validar", params={"token": token}).json()["status"], "EXPIRADO")

        # Utilizado.
        registro.expira_em = datetime.now(timezone.utc) + timedelta(hours=1)
        registro.usado_em = datetime.now(timezone.utc)
        self.db.commit()
        self.assertEqual(self.client.get("/usuarios/ativacao/validar", params={"token": token}).json()["status"], "UTILIZADO")

    # --- ativacao --------------------------------------------------------
    def test_AT08_ativacao_define_senha_ativa_conta_e_consome_token(self):
        self.convidar()
        token = self.token_do_email()
        r = self.client.post("/usuarios/ativacao", json={"token": token, "senha": "abc123", "confirmacao_senha": "abc123"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["ativado"])
        self.assertEqual(r.json()["email"], "novo@cliente.com")

        self.db.expire_all()
        criado = self.usuario_por_email("novo@cliente.com")
        self.assertTrue(criado.ativo)
        self.assertTrue(security.verify_password("abc123", criado.senha_hash))
        self.assertIsNotNone(self.registro(criado.id).usado_em)

    def test_AT09_token_e_de_uso_unico(self):
        self.convidar()
        token = self.token_do_email()
        self.assertEqual(self.client.post("/usuarios/ativacao", json={"token": token, "senha": "abc123"}).status_code, 200)
        r = self.client.post("/usuarios/ativacao", json={"token": token, "senha": "outra123"})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("utilizado", r.json()["detail"].lower())
        self.db.expire_all()
        criado = self.usuario_por_email("novo@cliente.com")
        self.assertTrue(security.verify_password("abc123", criado.senha_hash), "a senha nao pode ser trocada pelo reuso")

    def test_AT10_token_expirado_nao_ativa(self):
        self.convidar()
        token = self.token_do_email()
        criado = self.usuario_por_email("novo@cliente.com")
        registro = self.registro(criado.id)
        registro.expira_em = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.db.commit()
        r = self.client.post("/usuarios/ativacao", json={"token": token, "senha": "abc123"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("expirou", r.json()["detail"].lower())
        self.db.expire_all()
        self.assertFalse(self.usuario_por_email("novo@cliente.com").ativo)

    def test_AT11_senha_fraca_e_recusada_e_nada_muda(self):
        self.convidar()
        token = self.token_do_email()
        for senha in ("abcdef", "123456", "a12", ""):
            r = self.client.post("/usuarios/ativacao", json={"token": token, "senha": senha})
            self.assertIn(r.status_code, (422,), f"{senha}: {r.text}")
            self.db.expire_all()
            criado = self.usuario_por_email("novo@cliente.com")
            self.assertFalse(criado.ativo, f"{senha} nao pode ativar")
            self.assertIsNone(self.registro(criado.id).usado_em, "token nao pode ser consumido por senha invalida")
        # Depois das recusas, uma senha valida ainda funciona.
        self.assertEqual(self.client.post("/usuarios/ativacao", json={"token": token, "senha": "pesquisa9"}).status_code, 200)

    def test_AT12_confirmacao_divergente_e_recusada(self):
        self.convidar()
        token = self.token_do_email()
        r = self.client.post("/usuarios/ativacao", json={"token": token, "senha": "abc123", "confirmacao_senha": "abc124"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("conferem", r.json()["detail"].lower())
        self.db.expire_all()
        self.assertFalse(self.usuario_por_email("novo@cliente.com").ativo)

    def test_AT13_token_invalido_nao_ativa_ninguem(self):
        self.convidar()
        r = self.client.post("/usuarios/ativacao", json={"token": "inventado", "senha": "abc123"})
        self.assertEqual(r.status_code, 400)
        self.db.expire_all()
        self.assertFalse(self.usuario_por_email("novo@cliente.com").ativo)

    # --- login -----------------------------------------------------------
    def test_AT14_conta_inativa_nao_autentica_e_ativada_sim(self):
        self.convidar()
        token = self.token_do_email()

        def logar(email, senha):
            return self.client.post("/login/token", data={"username": email, "password": senha})

        # Antes da ativacao a senha nem existe; e a conta esta inativa.
        self.assertEqual(logar("novo@cliente.com", "abc123").status_code, 401)

        self.assertEqual(self.client.post("/usuarios/ativacao", json={"token": token, "senha": "abc123"}).status_code, 200)
        r = logar("novo@cliente.com", "abc123")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("access_token", r.json())

    def test_AT15_usuario_antigo_continua_logando(self):
        """Compatibilidade: contas existentes nao foram tocadas."""
        r = self.client.post("/login/token", data={"username": "antigo@qa.com", "password": "legado123"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("access_token", r.json())

    def test_AT16_criacao_com_senha_mantem_o_comportamento_anterior(self):
        """Contrato antigo preservado: com senha, sem convite."""
        antes = len(self.enviados)
        self.convidar(email="direto@cliente.com", senha="direta123")
        criado = self.usuario_por_email("direto@cliente.com")
        self.assertTrue(criado.ativo)
        self.assertEqual(len(self.enviados), antes, "senha informada nao dispara convite")
        self.assertTrue(security.verify_password("direta123", criado.senha_hash))

    # --- reenvio ---------------------------------------------------------
    def test_AT17_reenvio_invalida_o_convite_anterior(self):
        self.convidar()
        primeiro = self.token_do_email()
        criado = self.usuario_por_email("novo@cliente.com")

        self.atual = usuario(SUPERADMIN, EMPRESA_A, perfil="Superadmin")
        r = self.client.post(f"/admin/usuarios/{criado.id}/reenviar-ativacao")
        self.assertEqual(r.status_code, 200, r.text)
        segundo = self.token_do_email()
        self.assertNotEqual(primeiro, segundo)

        self.assertEqual(self.client.get("/usuarios/ativacao/validar", params={"token": primeiro}).json()["status"], "UTILIZADO")
        self.assertEqual(self.client.get("/usuarios/ativacao/validar", params={"token": segundo}).json()["status"], "VALIDO")
        self.assertEqual(self.client.post("/usuarios/ativacao", json={"token": segundo, "senha": "abc123"}).status_code, 200)

    def test_AT18_reenvio_recusado_para_conta_ja_ativa(self):
        self.atual = usuario(SUPERADMIN, EMPRESA_A, perfil="Superadmin")
        r = self.client.post(f"/admin/usuarios/{AGENTE_ATIVO}/reenviar-ativacao")
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("ja esta ativo", r.json()["detail"].lower())
        self.assertEqual(self.client.post("/admin/usuarios/999999/reenviar-ativacao").status_code, 404)

    # --- transacao -------------------------------------------------------
    def test_AT19_falha_ao_gravar_senha_nao_consome_o_token(self):
        self.convidar()
        token = self.token_do_email()
        with patch("pesquisa360.core.security.get_password_hash", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                ativacao.ativar_conta(self.db, token, "abc123")
        self.db.expire_all()
        criado = self.usuario_por_email("novo@cliente.com")
        self.assertFalse(criado.ativo, "rollback preserva a conta inativa")
        self.assertIsNone(self.registro(criado.id).usado_em, "token continua utilizavel")
        self.assertEqual(self.client.post("/usuarios/ativacao", json={"token": token, "senha": "abc123"}).status_code, 200)

    def test_AT20_convite_sobrevive_a_falha_do_servidor_de_email(self):
        """E-mail fora do ar nao pode perder o convite ja gravado."""
        with patch("pesquisa360.services.email.enviar_email", return_value=False):
            self.convidar(email="semmail@cliente.com")
        criado = self.usuario_por_email("semmail@cliente.com")
        self.assertIsNotNone(criado)
        self.assertFalse(criado.ativo)
        self.assertIsNotNone(self.registro(criado.id), "token gravado mesmo sem e-mail entregue")


if __name__ == "__main__":
    unittest.main()

# =============================================================================
# Link de convite devolvido ao painel (PROMPT 01.1)
# =============================================================================
class LinkConviteTests(AtivacaoContaTests):
    """Reusa a fixture do fluxo de convite: o link e o mesmo token, exposto uma
    unica vez a quem acabou de criar/renovar o convite."""

    def tokens_no_banco(self) -> str:
        """Dump textual de TODAS as tabelas -- para caçar o token literal."""
        nomes = [
            linha[0]
            for linha in self.db.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        ]
        partes = []
        for nome in nomes:
            for linha in self.db.execute(text(f"SELECT * FROM {nome}")).fetchall():
                partes.append(" ".join(str(valor) for valor in linha))
        return chr(10).join(partes)

    def test_LC01_criacao_por_convite_devolve_activation_url_utilizavel(self):
        r = self.convidar()
        corpo = r.json()
        convite = corpo["convite"]
        self.assertIsNotNone(convite)
        self.assertTrue(convite["email_enviado"])
        self.assertIn("/ativar-conta?token=", convite["activation_url"])
        self.assertTrue(convite["activation_url"].startswith(ativacao.servico_email.web_base_url()))
        self.assertIsNotNone(convite["expira_em"])
        self.assertFalse(corpo["ativo"])

        # O token do link e o mesmo do e-mail e ativa de verdade.
        token = convite["activation_url"].split("token=")[1]
        self.assertEqual(token, self.token_do_email())
        self.assertEqual(
            self.client.get("/usuarios/ativacao/validar", params={"token": token}).json()["status"],
            "VALIDO",
        )
        self.assertEqual(
            self.client.post("/usuarios/ativacao", json={"token": token, "senha": "abc123"}).status_code,
            200,
        )

    def test_LC02_o_token_do_link_nao_existe_em_nenhuma_tabela(self):
        """O criterio central: o banco guarda hash, nunca o segredo."""
        r = self.convidar()
        token = r.json()["convite"]["activation_url"].split("token=")[1]
        dump = self.tokens_no_banco()
        self.assertEqual(dump.count(token), 0, "token literal encontrado no banco")
        self.assertIn(hashlib.sha256(token.encode()).hexdigest(), dump)

    def test_LC03_criacao_com_senha_nao_inventa_link(self):
        corpo = self.convidar(email="direto@cliente.com", senha="direta123").json()
        self.assertIsNone(corpo["convite"])

    def test_LC04_listagem_e_leitura_nunca_expoem_o_link(self):
        self.convidar()
        self.atual = usuario(SUPERADMIN, EMPRESA_A, perfil="Superadmin")
        for rota in ("/usuarios/", "/admin/usuarios/"):
            r = self.client.get(rota)
            self.assertEqual(r.status_code, 200, r.text)
            for proibido in ("activation_url", "ativar-conta?token=", "token_hash"):
                self.assertNotIn(proibido, r.text, f"{rota} vazou {proibido}")

    def test_LC05_reenvio_devolve_novo_link_e_invalida_o_anterior(self):
        primeiro = self.convidar().json()["convite"]["activation_url"]
        criado = self.usuario_por_email("novo@cliente.com")

        self.atual = usuario(SUPERADMIN, EMPRESA_A, perfil="Superadmin")
        r = self.client.post(f"/admin/usuarios/{criado.id}/reenviar-ativacao")
        self.assertEqual(r.status_code, 200, r.text)
        corpo = r.json()
        segundo = corpo["activation_url"]
        self.assertIn("/ativar-conta?token=", segundo)
        self.assertNotEqual(primeiro, segundo)
        self.assertTrue(corpo["enviado"])
        self.assertIsNotNone(corpo["expira_em"])

        token_antigo = primeiro.split("token=")[1]
        token_novo = segundo.split("token=")[1]
        self.assertEqual(
            self.client.get("/usuarios/ativacao/validar", params={"token": token_antigo}).json()["status"],
            "UTILIZADO",
        )
        self.assertEqual(
            self.client.get("/usuarios/ativacao/validar", params={"token": token_novo}).json()["status"],
            "VALIDO",
        )
        self.assertEqual(
            self.client.post("/usuarios/ativacao", json={"token": token_novo, "senha": "abc123"}).status_code,
            200,
        )

    def test_LC06_usuario_ativo_nao_gera_convite(self):
        self.atual = usuario(SUPERADMIN, EMPRESA_A, perfil="Superadmin")
        r = self.client.post(f"/admin/usuarios/{AGENTE_ATIVO}/reenviar-ativacao")
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("activation_url", r.text)

    def test_LC07_link_falha_de_email_continua_utilizavel(self):
        """E-mail no spam/fora do ar: o link e o plano B, nao um erro."""
        with patch("pesquisa360.services.email.enviar_email", return_value=False):
            corpo = self.convidar(email="semmail@cliente.com").json()
        convite = corpo["convite"]
        self.assertFalse(convite["email_enviado"], "o painel precisa saber que o e-mail nao saiu")
        token = convite["activation_url"].split("token=")[1]
        self.assertEqual(
            self.client.post("/usuarios/ativacao", json={"token": token, "senha": "abc123"}).status_code,
            200,
        )

    def test_LC08_link_usa_a_base_configurada_e_nao_dominio_fixo(self):
        with patch.dict(os.environ, {"WEB_BASE_URL": "https://app360.rtecnologia.online"}):
            corpo = self.convidar(email="config@cliente.com").json()
        self.assertTrue(
            corpo["convite"]["activation_url"].startswith(
                "https://app360.rtecnologia.online/ativar-conta?token="
            ),
            corpo["convite"]["activation_url"],
        )

    def test_LC09_gerar_convite_exige_autorizacao(self):
        """Sem sessao administrativa nao ha link -- ele vale como credencial."""
        app = FastAPI()
        app.include_router(rotas_usuarios.admin_router)
        app.dependency_overrides[get_db] = lambda: self.db
        with TestClient(app) as anonimo:
            r = anonimo.post(f"/admin/usuarios/{AGENTE_ATIVO}/reenviar-ativacao")
            self.assertIn(r.status_code, (401, 403), r.text)

    def test_LC10_link_perdido_so_se_resolve_com_novo_convite(self):
        """Nao existe rota para reler o link antigo: o token nao e recuperavel."""
        rotas = [
            str(getattr(r, "path", ""))
            for r in rotas_usuarios.router.routes + rotas_usuarios.admin_router.routes
        ]
        for rota in rotas:
            self.assertNotIn("link-ativacao", rota)
            self.assertNotIn("token-ativacao", rota)
        self.convidar()
        criado = self.usuario_por_email("novo@cliente.com")
        registro = self.registro(criado.id)
        # Nem pelo banco: o que existe la e hash, sem caminho de volta.
        self.assertEqual(len(registro.token_hash), 64)
        self.assertNotIn("ativar-conta", registro.token_hash)
