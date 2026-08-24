import os
import unittest
from unittest.mock import patch

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SECRET_KEY", "test-only-tenant-validation-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.api.endpoints import empresas, login
from pesquisa360.core import security
from pesquisa360.core.dependencies import get_db


class TenantValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        cls.Session = sessionmaker(bind=cls.engine)
        cls.password_hash = security.get_password_hash("correct-password")
        with cls.engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE companies (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, cnpj VARCHAR(14) UNIQUE,
                    logo_url VARCHAR(2048), is_active BOOLEAN, created_at DATETIME
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
            connection.execute(text("""
                CREATE TABLE projetos (
                    id INTEGER PRIMARY KEY, nome TEXT, company_id INTEGER
                )
            """))
            connection.execute(text("""
                CREATE TABLE pesquisas (
                    id INTEGER PRIMARY KEY, titulo TEXT, projeto_id INTEGER
                )
            """))
            connection.execute(text("""
                CREATE TABLE coletas (
                    id INTEGER PRIMARY KEY, pesquisa_id INTEGER, company_id INTEGER
                )
            """))

        cls.app = FastAPI()
        cls.app.include_router(login.router, prefix="/login")
        cls.app.include_router(empresas.router, prefix="/empresas")

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
            for table in ("coletas", "pesquisas", "projetos", "usuarios", "perfis", "companies"):
                connection.execute(text(f"DELETE FROM {table}"))
            connection.execute(text("""
                INSERT INTO companies (id, name, cnpj, is_active)
                VALUES (10, 'Tenant A', '04252011000110', 1),
                       (20, 'Tenant B', '11222333000181', 1)
            """))
            connection.execute(text("""
                INSERT INTO perfis (id, nome)
                VALUES (7, 'Agente'), (42, 'Gerente'), (99, 'Superadmin')
            """))
            connection.execute(text("""
                INSERT INTO usuarios
                    (id, email, nome, senha_hash, ativo, perfil_id, company_id)
                VALUES (1, 'super@example.com', 'Super', :password, 1, 99, NULL),
                       (2, 'manager@example.com', 'Manager', :password, 1, 42, 10),
                       (3, 'agent@example.com', 'Agent', :password, 1, 7, 10)
            """), {"password": self.password_hash})

    def authorization(self, email="super@example.com"):
        token = security.create_access_token({"sub": email})
        return {"Authorization": f"Bearer {token}"}

    def create_tenant(self, payload, email="super@example.com"):
        return self.client.post("/empresas/", json=payload, headers=self.authorization(email))

    def update_tenant(self, tenant_id, payload, email="super@example.com"):
        return self.client.patch(
            f"/empresas/{tenant_id}", json=payload, headers=self.authorization(email)
        )

    def test_superadmin_creates_valid_tenant_and_normalizes_name_and_formatted_cnpj(self):
        response = self.create_tenant({
            "name": "  Tenant Novo  ",
            "cnpj": "45.723.174/0001-10",
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["name"], "Tenant Novo")
        self.assertEqual(response.json()["cnpj"], "45723174000110")

    def test_numeric_cnpj_is_accepted(self):
        response = self.create_tenant({"name": "Tenant Novo", "cnpj": "45723174000110"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["cnpj"], "45723174000110")

    def test_empty_name_returns_422(self):
        self.assertEqual(self.create_tenant({"name": "   "}).status_code, 422)

    def test_invalid_cnpj_check_digits_returns_422(self):
        self.assertEqual(
            self.create_tenant({"name": "Tenant", "cnpj": "04.252.011/0001-11"}).status_code,
            422,
        )

    def test_repeated_cnpj_returns_422(self):
        self.assertEqual(
            self.create_tenant({"name": "Tenant", "cnpj": "11.111.111/1111-11"}).status_code,
            422,
        )

    def test_cnpj_with_invalid_length_returns_422(self):
        self.assertEqual(
            self.create_tenant({"name": "Tenant", "cnpj": "123"}).status_code,
            422,
        )

    def test_formatted_duplicate_cnpj_returns_409(self):
        response = self.create_tenant({"name": "Duplicado", "cnpj": "04.252.011/0001-10"})
        self.assertEqual(response.status_code, 409)

    def test_update_can_keep_own_cnpj(self):
        response = self.update_tenant(10, {"cnpj": "04.252.011/0001-10"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cnpj"], "04252011000110")

    def test_update_to_other_tenant_cnpj_returns_409(self):
        self.assertEqual(
            self.update_tenant(10, {"cnpj": "11.222.333/0001-81"}).status_code,
            409,
        )

    def test_http_and_https_logo_urls_are_accepted(self):
        for tenant_id, url in ((10, "http://example.com/logo.png"), (20, "https://example.com/logo.png")):
            with self.subTest(url=url):
                response = self.update_tenant(tenant_id, {"logo_url": url})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["logo_url"], url)

    def test_empty_logo_becomes_null_and_invalid_logo_returns_422(self):
        self.assertIsNone(self.update_tenant(10, {"logo_url": "  "}).json()["logo_url"])
        self.assertEqual(self.update_tenant(10, {"logo_url": "ftp://example.com/a"}).status_code, 422)
        self.assertEqual(self.update_tenant(10, {"logo_url": "https://"}).status_code, 422)

    def test_deactivation_preserves_associated_history(self):
        with self.engine.begin() as connection:
            connection.execute(text("INSERT INTO projetos (id, nome, company_id) VALUES (100, 'P', 10)"))
            connection.execute(text("INSERT INTO pesquisas (id, titulo, projeto_id) VALUES (200, 'R', 100)"))
            connection.execute(text("INSERT INTO coletas (id, pesquisa_id, company_id) VALUES (300, 200, 10)"))

        self.assertEqual(self.update_tenant(10, {"is_active": False}).status_code, 200)
        with self.engine.connect() as connection:
            counts = [
                connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
                for table in ("usuarios", "projetos", "pesquisas", "coletas")
            ]
        self.assertEqual(counts, [3, 1, 1, 1])

    def test_inactive_tenant_blocks_login_and_reactivation_restores_new_login(self):
        self.update_tenant(10, {"is_active": False})
        blocked = self.client.post(
            "/login/token",
            data={"username": "manager@example.com", "password": "correct-password"},
        )
        self.assertEqual(blocked.status_code, 401)

        self.update_tenant(10, {"is_active": True})
        allowed = self.client.post(
            "/login/token",
            data={"username": "manager@example.com", "password": "correct-password"},
        )
        self.assertEqual(allowed.status_code, 200)

    def test_manager_and_agent_cannot_administer_tenants(self):
        for email in ("manager@example.com", "agent@example.com"):
            with self.subTest(email=email):
                self.assertEqual(
                    self.create_tenant({"name": "Forbidden"}, email=email).status_code,
                    403,
                )
                self.assertEqual(
                    self.update_tenant(10, {"is_active": False}, email=email).status_code,
                    403,
                )

    def test_missing_tenant_returns_404(self):
        self.assertEqual(self.update_tenant(999, {"name": "Missing"}).status_code, 404)

    def test_unique_constraint_race_returns_409_not_500(self):
        with patch("pesquisa360.crud.get_company_by_cnpj", return_value=None):
            response = self.create_tenant({"name": "Race", "cnpj": "04.252.011/0001-10"})
        self.assertEqual(response.status_code, 409)

    def test_migration_has_one_new_head(self):
        heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
        self.assertEqual(heads, ["b2c3d4e5f6a7"])


if __name__ == "__main__":
    unittest.main()
