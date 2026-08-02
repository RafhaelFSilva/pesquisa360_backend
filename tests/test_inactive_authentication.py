import os
import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-inactive-authentication-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.api.endpoints import login
from pesquisa360.core import dependencies, security


class InactiveAuthenticationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:")
        cls.Session = sessionmaker(bind=cls.engine)
        cls.password_hash = security.get_password_hash("correct-password")
        with cls.engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE companies (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, cnpj TEXT,
                    logo_url TEXT, is_active BOOLEAN, created_at DATETIME
                )
            """))
            connection.execute(text("""
                CREATE TABLE perfis (
                    id INTEGER PRIMARY KEY, nome TEXT NOT NULL UNIQUE, descricao TEXT
                )
            """))
            connection.execute(text("""
                CREATE TABLE usuarios (
                    id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, nome TEXT,
                    senha_hash TEXT NOT NULL, ativo BOOLEAN NOT NULL,
                    perfil_id INTEGER NOT NULL, company_id INTEGER
                )
            """))

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        self.db = self.Session()
        self.db.execute(text("DELETE FROM usuarios"))
        self.db.execute(text("DELETE FROM perfis"))
        self.db.execute(text("DELETE FROM companies"))
        self.db.execute(text("""
            INSERT INTO companies (id, name, is_active)
            VALUES (10, 'Tenant A', 1)
        """))
        self.db.execute(text("""
            INSERT INTO perfis (id, nome)
            VALUES (7, 'Superadmin'), (42, 'Gerente')
        """))
        self.db.execute(text("""
            INSERT INTO usuarios
                (id, email, nome, senha_hash, ativo, perfil_id, company_id)
            VALUES (1, 'manager@example.com', 'Manager', :password_hash, 1, 42, 10),
                   (2, 'super@example.com', 'Super', :password_hash, 1, 7, NULL)
        """), {"password_hash": self.password_hash})
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def login(self, email="manager@example.com", password="correct-password"):
        return login.login_for_access_token(
            form_data=SimpleNamespace(username=email, password=password),
            db=self.db,
        )

    def assert_unauthorized(self, operation):
        with self.assertRaises(HTTPException) as context:
            operation()
        self.assertEqual(context.exception.status_code, 401)
        return context.exception.detail

    def authenticate_token(self, token):
        self.db.expire_all()
        return dependencies.get_current_user(db=self.db, token=token)

    def set_user_active(self, active):
        self.db.execute(
            text("UPDATE usuarios SET ativo = :active WHERE id = 1"),
            {"active": active},
        )
        self.db.commit()
        self.db.expire_all()

    def set_company_active(self, active):
        self.db.execute(
            text("UPDATE companies SET is_active = :active WHERE id = 10"),
            {"active": active},
        )
        self.db.commit()
        self.db.expire_all()

    def test_active_user_and_company_can_login_and_use_old_token(self):
        token = self.login()["access_token"]
        user = self.authenticate_token(token)
        self.assertEqual(user.email, "manager@example.com")

    def test_inactive_user_with_active_company_is_blocked(self):
        old_token = self.login()["access_token"]
        self.set_user_active(False)
        self.assert_unauthorized(self.login)
        self.assert_unauthorized(lambda: self.authenticate_token(old_token))

    def test_active_user_with_inactive_company_is_blocked(self):
        old_token = self.login()["access_token"]
        self.set_company_active(False)
        self.assert_unauthorized(self.login)
        self.assert_unauthorized(lambda: self.authenticate_token(old_token))

    def test_inactive_user_with_inactive_company_is_blocked(self):
        old_token = self.login()["access_token"]
        self.set_user_active(False)
        self.set_company_active(False)
        self.assert_unauthorized(self.login)
        self.assert_unauthorized(lambda: self.authenticate_token(old_token))

    def test_superadmin_without_company_can_login_and_use_authenticated_route(self):
        token = self.login(email="super@example.com")["access_token"]
        user = self.authenticate_token(token)
        self.assertEqual(user.email, "super@example.com")

    def test_superadmin_linked_to_inactive_company_is_blocked(self):
        self.db.execute(text("UPDATE usuarios SET company_id = 10 WHERE id = 2"))
        self.db.execute(text("UPDATE companies SET is_active = 0 WHERE id = 10"))
        self.db.commit()
        self.db.expire_all()
        self.assert_unauthorized(lambda: self.login(email="super@example.com"))

    def test_reactivated_user_can_login_again(self):
        self.set_user_active(False)
        self.assert_unauthorized(self.login)
        self.set_user_active(True)
        self.assertIn("access_token", self.login())

    def test_reactivated_company_allows_login_again(self):
        self.set_company_active(False)
        self.assert_unauthorized(self.login)
        self.set_company_active(True)
        self.assertIn("access_token", self.login())

    def test_public_login_message_does_not_reveal_failure_reason(self):
        messages = {
            self.assert_unauthorized(lambda: self.login(email="missing@example.com")),
            self.assert_unauthorized(lambda: self.login(password="wrong-password")),
        }
        self.set_user_active(False)
        messages.add(self.assert_unauthorized(self.login))
        self.set_user_active(True)
        self.set_company_active(False)
        messages.add(self.assert_unauthorized(self.login))
        self.assertEqual(messages, {"E-mail ou senha incorretos"})


if __name__ == "__main__":
    unittest.main()
