import os
import unittest
from datetime import date

from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-project-coordinator-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360 import schemas
from pesquisa360.api.endpoints import projetos
from pesquisa360.db import models
from tests.acl_fixture import criar_tabelas_acl


class ProjectCoordinatorValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:")
        criar_tabelas_acl(cls.engine)
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
            connection.execute(text("""
                CREATE TABLE projetos (
                    id INTEGER PRIMARY KEY, nome TEXT NOT NULL, descricao TEXT,
                    status TEXT NOT NULL, data_inicio DATE, data_fim DATE,
                    coordenador_id INTEGER NOT NULL, company_id INTEGER NOT NULL
                )
            """))

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        self.db = self.Session()
        for table in ("projetos", "usuarios", "perfis", "companies"):
            self.db.execute(text(f"DELETE FROM {table}"))
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
            VALUES (1, 'manager-a@example.com', 'Manager A', 'hash', 1, 42, 10),
                   (2, 'super-a@example.com', 'Super A', 'hash', 1, 7, 10),
                   (3, 'agent-a@example.com', 'Agent A', 'hash', 1, 99, 10),
                   (4, 'inactive-a@example.com', 'Inactive A', 'hash', 0, 42, 10),
                   (5, 'manager-b@example.com', 'Manager B', 'hash', 1, 42, 20)
        """))
        self.db.execute(text("""
            INSERT INTO projetos
                (id, nome, status, data_inicio, coordenador_id, company_id)
            VALUES (100, 'Projeto A', 'Planejamento', '2026-01-01', 1, 10),
                   (200, 'Projeto B', 'Planejamento', '2026-01-01', 5, 20)
        """))
        self.db.commit()
        self.manager = self.db.get(models.Usuario, 1)
        self.superadmin = self.db.get(models.Usuario, 2)

    def tearDown(self):
        self.db.close()

    def project_payload(self, coordinator_id, company_id=None):
        return schemas.ProjetoCreate(
            nome="Novo projeto",
            data_inicio=date(2026, 2, 1),
            coordenador_id=coordinator_id,
            company_id=company_id,
        )

    def assert_http_status(self, status_code, operation):
        with self.assertRaises(HTTPException) as context:
            operation()
        self.assertEqual(context.exception.status_code, status_code)

    def test_manager_creates_with_active_manager_from_same_tenant(self):
        created = projetos.create_projeto(
            projeto=self.project_payload(1), db=self.db, current_user=self.manager
        )
        self.assertEqual((created.company_id, created.coordenador_id), (10, 1))

    def test_manager_creates_with_superadmin_from_same_tenant(self):
        created = projetos.create_projeto(
            projeto=self.project_payload(2), db=self.db, current_user=self.manager
        )
        self.assertEqual(created.coordenador_id, 2)

    def test_manager_cannot_use_agent_as_coordinator(self):
        self.assert_http_status(400, lambda: projetos.create_projeto(
            projeto=self.project_payload(3), db=self.db, current_user=self.manager
        ))

    def test_manager_cannot_use_inactive_coordinator(self):
        self.assert_http_status(400, lambda: projetos.create_projeto(
            projeto=self.project_payload(4), db=self.db, current_user=self.manager
        ))

    def test_manager_cannot_use_coordinator_from_another_tenant(self):
        self.assert_http_status(404, lambda: projetos.create_projeto(
            projeto=self.project_payload(5), db=self.db, current_user=self.manager
        ))

    def test_manager_cannot_use_missing_coordinator(self):
        self.assert_http_status(404, lambda: projetos.create_projeto(
            projeto=self.project_payload(999), db=self.db, current_user=self.manager
        ))

    def test_manager_payload_cannot_move_project_to_another_tenant(self):
        created = projetos.create_projeto(
            projeto=self.project_payload(1, company_id=20),
            db=self.db,
            current_user=self.manager,
        )
        self.assertEqual(created.company_id, 10)

    def test_update_accepts_valid_coordinator_from_same_tenant(self):
        updated = projetos.update_projeto(
            projeto_id=100,
            projeto_update=schemas.ProjetoUpdate(coordenador_id=2),
            db=self.db,
            current_user=self.manager,
        )
        self.assertEqual(updated.coordenador_id, 2)

    def test_update_rejects_agent_and_inactive_coordinator(self):
        for coordinator_id in (3, 4):
            with self.subTest(coordinator_id=coordinator_id):
                self.assert_http_status(400, lambda coordinator_id=coordinator_id: projetos.update_projeto(
                    projeto_id=100,
                    projeto_update=schemas.ProjetoUpdate(coordenador_id=coordinator_id),
                    db=self.db,
                    current_user=self.manager,
                ))

    def test_update_rejects_coordinator_from_another_tenant(self):
        self.assert_http_status(404, lambda: projetos.update_projeto(
            projeto_id=100,
            projeto_update=schemas.ProjetoUpdate(coordenador_id=5),
            db=self.db,
            current_user=self.manager,
        ))

    def test_manager_cannot_update_project_from_another_tenant(self):
        self.assert_http_status(404, lambda: projetos.update_projeto(
            projeto_id=200,
            projeto_update=schemas.ProjetoUpdate(coordenador_id=5),
            db=self.db,
            current_user=self.manager,
        ))

    def test_superadmin_creates_and_edits_project_in_explicit_tenant(self):
        created = projetos.create_projeto(
            projeto=self.project_payload(1, company_id=10),
            db=self.db,
            current_user=self.superadmin,
        )
        self.assertEqual(created.company_id, 10)
        updated = projetos.update_projeto(
            projeto_id=100,
            projeto_update=schemas.ProjetoUpdate(coordenador_id=2),
            db=self.db,
            current_user=self.superadmin,
        )
        self.assertEqual(updated.coordenador_id, 2)

    def test_superadmin_cannot_cross_project_and_coordinator_tenants(self):
        self.assert_http_status(404, lambda: projetos.create_projeto(
            projeto=self.project_payload(5, company_id=10),
            db=self.db,
            current_user=self.superadmin,
        ))
        self.assert_http_status(404, lambda: projetos.update_projeto(
            projeto_id=100,
            projeto_update=schemas.ProjetoUpdate(coordenador_id=5),
            db=self.db,
            current_user=self.superadmin,
        ))


if __name__ == "__main__":
    unittest.main()
