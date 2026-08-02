import os
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SECRET_KEY", "test-only-assignable-profiles-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.api.endpoints import usuarios
from pesquisa360.core import security
from pesquisa360.core.dependencies import get_db


class AssignableProfilesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
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
                    perfil_id INTEGER NOT NULL, company_id INTEGER
                )
            """))

        cls.app = FastAPI()
        cls.app.include_router(usuarios.profiles_router)

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
            connection.execute(text("DELETE FROM usuarios"))
            connection.execute(text("DELETE FROM perfis"))
            connection.execute(text("DELETE FROM companies"))
            connection.execute(text("""
                INSERT INTO companies (id, name, is_active)
                VALUES (10, 'Tenant ativo', 1), (20, 'Tenant inativo', 0)
            """))
            connection.execute(text("""
                INSERT INTO perfis (id, nome)
                VALUES (7, 'Agente'), (8, ' agente '), (42, 'Gerente'),
                       (99, 'Superadmin'), (123, 'Auditor')
            """))
            connection.execute(text("""
                INSERT INTO usuarios
                    (id, email, nome, senha_hash, ativo, perfil_id, company_id)
                VALUES (1, 'super@example.com', 'Super', 'hash', 1, 99, NULL),
                       (2, 'manager@example.com', 'Manager', 'hash', 1, 42, 10),
                       (3, 'agent@example.com', 'Agent', 'hash', 1, 7, 10),
                       (4, 'inactive@example.com', 'Inactive', 'hash', 0, 42, 10),
                       (5, 'inactive-company@example.com', 'Inactive company', 'hash', 1, 42, 20)
            """))

    def authorization(self, email):
        token = security.create_access_token({"sub": email})
        return {"Authorization": f"Bearer {token}"}

    def get_profiles(self, email):
        return self.client.get("/perfis/", headers=self.authorization(email))

    def test_route_is_registered_as_get(self):
        routes = {(route.path, method) for route in self.app.routes for method in route.methods or []}
        self.assertIn(("/perfis/", "GET"), routes)

    def test_request_without_authentication_returns_401(self):
        self.assertEqual(self.client.get("/perfis/").status_code, 401)

    def test_inactive_user_returns_401(self):
        self.assertEqual(self.get_profiles("inactive@example.com").status_code, 401)

    def test_inactive_company_returns_401(self):
        self.assertEqual(self.get_profiles("inactive-company@example.com").status_code, 401)

    def test_superadmin_receives_agent_and_manager_with_database_ids(self):
        response = self.get_profiles("super@example.com")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [
            {"id": 7, "code": "AGENT", "nome": "Agente"},
            {"id": 42, "code": "MANAGER", "nome": "Gerente"},
        ])

    def test_manager_receives_only_agent(self):
        response = self.get_profiles("manager@example.com")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [
            {"id": 7, "code": "AGENT", "nome": "Agente"},
        ])

    def test_agent_receives_403(self):
        self.assertEqual(self.get_profiles("agent@example.com").status_code, 403)

    def test_unknown_and_superadmin_profiles_are_not_exposed(self):
        codes = {item["code"] for item in self.get_profiles("super@example.com").json()}
        self.assertNotIn("SUPERADMIN", codes)
        self.assertNotIn("AUDITOR", codes)

    def test_codes_are_stable_for_nonconventional_ids(self):
        items = self.get_profiles("super@example.com").json()
        self.assertEqual({item["id"]: item["code"] for item in items}, {7: "AGENT", 42: "MANAGER"})

    def test_response_has_no_duplicate_codes(self):
        codes = [item["code"] for item in self.get_profiles("super@example.com").json()]
        self.assertEqual(len(codes), len(set(codes)))

    def test_response_order_is_deterministic(self):
        first = self.get_profiles("super@example.com").json()
        second = self.get_profiles("super@example.com").json()
        self.assertEqual(first, second)
        self.assertEqual([item["code"] for item in first], ["AGENT", "MANAGER"])


if __name__ == "__main__":
    unittest.main()
