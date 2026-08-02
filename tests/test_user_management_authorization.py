import os
import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-user-authorization-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360 import crud, schemas
from pesquisa360.api.endpoints import usuarios
from pesquisa360.core import dependencies, security
from pesquisa360.db import models


class UserManagementAuthorizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:")
        cls.Session = sessionmaker(bind=cls.engine)
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
                    perfil_id INTEGER NOT NULL, company_id INTEGER NOT NULL
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
            VALUES (10, 'Tenant A', 1), (20, 'Tenant B', 1)
        """))
        self.db.execute(text("""
            INSERT INTO perfis (id, nome)
            VALUES (7, 'Superadmin'), (42, 'Gerente'), (99, 'Agente')
        """))
        self.db.execute(text("""
            INSERT INTO usuarios
                (id, email, nome, senha_hash, ativo, perfil_id, company_id)
            VALUES (1, 'super@example.com', 'Super', 'hash', 1, 7, 10),
                   (2, 'manager-a@example.com', 'Manager A', 'hash', 1, 42, 10),
                   (3, 'agent-a@example.com', 'Agent A', 'hash', 1, 99, 10),
                   (4, 'manager-a2@example.com', 'Manager A2', 'hash', 1, 42, 10),
                   (5, 'agent-b@example.com', 'Agent B', 'hash', 1, 99, 20),
                   (6, 'manager-b@example.com', 'Manager B', 'hash', 1, 42, 20)
        """))
        self.db.commit()
        self.superadmin = self.db.get(models.Usuario, 1)
        self.manager = self.db.get(models.Usuario, 2)
        self.agent = self.db.get(models.Usuario, 3)

    def tearDown(self):
        self.db.close()

    def assert_http_status(self, status_code, operation):
        with self.assertRaises(HTTPException) as context:
            operation()
        self.assertEqual(context.exception.status_code, status_code)

    def test_superadmin_lists_users_globally(self):
        result = usuarios.read_users(db=self.db, current_user=self.superadmin)
        self.assertEqual({user.company_id for user in result}, {10, 20})

    def test_superadmin_creates_manager_and_agent_in_informed_tenant(self):
        for email, profile_id in (("new-manager@example.com", 42), ("new-agent@example.com", 99)):
            created = usuarios.create_user(
                user=schemas.UsuarioCreate(
                    email=email,
                    nome="Novo",
                    senha="safe-password",
                    perfil_id=profile_id,
                    company_id=20,
                ),
                db=self.db,
                current_user=self.superadmin,
            )
            self.assertEqual(created.company_id, 20)
            self.assertTrue(security.verify_password("safe-password", created.senha_hash))

    def test_superadmin_edits_user_from_another_tenant(self):
        updated = usuarios.update_user(
            usuario_id=5,
            user_update=schemas.UsuarioAdminUpdate(nome="Agent B updated"),
            db=self.db,
            current_user=self.superadmin,
        )
        self.assertEqual(updated.nome, "Agent B updated")

    def test_superadmin_deactivates_and_reactivates_user(self):
        for active in (False, True):
            updated = usuarios.update_user(
                usuario_id=5,
                user_update=schemas.UsuarioAdminUpdate(ativo=active),
                db=self.db,
                current_user=self.superadmin,
            )
            self.assertIs(updated.ativo, active)

    def test_manager_lists_only_own_company(self):
        result = usuarios.read_users(db=self.db, current_user=self.manager)
        self.assertTrue(result)
        self.assertEqual({user.company_id for user in result}, {10})

    def test_manager_creates_only_agent_in_own_company(self):
        created = usuarios.create_user(
            user=schemas.UsuarioCreate(
                email="manager-created-agent@example.com",
                nome="Agent",
                senha="safe-password",
                perfil_id=99,
                company_id=20,
            ),
            db=self.db,
            current_user=self.manager,
        )
        self.assertEqual(created.company_id, 10)
        self.assertEqual(created.perfil.nome, "Agente")

    def test_manager_cannot_create_manager_or_superadmin(self):
        for profile_id in (42, 7):
            self.assert_http_status(403, lambda profile_id=profile_id: usuarios.create_user(
                user=schemas.UsuarioCreate(
                    email=f"blocked-{profile_id}@example.com",
                    senha="safe-password",
                    perfil_id=profile_id,
                    company_id=20,
                ),
                db=self.db,
                current_user=self.manager,
            ))

    def test_manager_edits_own_agent_without_moving_tenant_or_profile(self):
        updated = usuarios.update_user(
            usuario_id=3,
            user_update=schemas.UsuarioAdminUpdate(
                nome="Agent updated",
                company_id=20,
                perfil_id=99,
            ),
            db=self.db,
            current_user=self.manager,
        )
        self.assertEqual(updated.nome, "Agent updated")
        self.assertEqual(updated.company_id, 10)
        self.assertEqual(updated.perfil.nome, "Agente")

    def test_manager_deactivates_and_reactivates_own_agent(self):
        for active in (False, True):
            updated = usuarios.update_user(
                usuario_id=3,
                user_update=schemas.UsuarioAdminUpdate(ativo=active),
                db=self.db,
                current_user=self.manager,
            )
            self.assertIs(updated.ativo, active)

    def test_manager_resets_own_agent_password_as_hash(self):
        updated = usuarios.reset_user_password(
            usuario_id=3,
            password_reset=schemas.UsuarioPasswordReset(senha="new-safe-password"),
            db=self.db,
            current_user=self.manager,
        )
        self.assertNotEqual(updated.senha_hash, "new-safe-password")
        self.assertTrue(security.verify_password("new-safe-password", updated.senha_hash))

    def test_manager_cannot_access_user_from_another_tenant(self):
        self.assert_http_status(404, lambda: usuarios.read_user(
            usuario_id=5,
            db=self.db,
            current_user=self.manager,
        ))

    def test_manager_cannot_edit_manager(self):
        self.assert_http_status(403, lambda: usuarios.update_user(
            usuario_id=4,
            user_update=schemas.UsuarioAdminUpdate(nome="Blocked"),
            db=self.db,
            current_user=self.manager,
        ))

    def test_manager_cannot_change_self(self):
        self.assert_http_status(403, lambda: usuarios.update_user(
            usuario_id=2,
            user_update=schemas.UsuarioAdminUpdate(company_id=20, perfil_id=7),
            db=self.db,
            current_user=self.manager,
        ))

    def test_manager_cannot_elevate_agent_profile(self):
        self.assert_http_status(403, lambda: usuarios.update_user(
            usuario_id=3,
            user_update=schemas.UsuarioAdminUpdate(perfil_id=42),
            db=self.db,
            current_user=self.manager,
        ))

    def test_agent_cannot_manage_users(self):
        operations = {
            "list": lambda: usuarios.read_users(db=self.db, current_user=self.agent),
            "create": lambda: usuarios.create_user(
                user=schemas.UsuarioCreate(
                    email="blocked-agent@example.com",
                    senha="password",
                    perfil_id=99,
                ),
                db=self.db,
                current_user=self.agent,
            ),
            "edit": lambda: usuarios.update_user(
                usuario_id=3,
                user_update=schemas.UsuarioAdminUpdate(nome="Blocked"),
                db=self.db,
                current_user=self.agent,
            ),
            "reset-password": lambda: usuarios.reset_user_password(
                usuario_id=3,
                password_reset=schemas.UsuarioPasswordReset(senha="blocked"),
                db=self.db,
                current_user=self.agent,
            ),
        }
        for operation, endpoint_call in operations.items():
            with self.subTest(operation=operation):
                self.assert_http_status(
                    403,
                    lambda endpoint_call=endpoint_call: (
                        dependencies.require_manager_or_superadmin(
                            db=self.db,
                            current_user=self.agent,
                        ),
                        endpoint_call(),
                    ),
                )


if __name__ == "__main__":
    unittest.main()
