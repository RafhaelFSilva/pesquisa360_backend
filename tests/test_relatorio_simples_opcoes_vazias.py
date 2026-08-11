import os
import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SECRET_KEY", "test-only-simple-report-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360 import crud
from pesquisa360.api.endpoints import relatorios
from pesquisa360.core.dependencies import get_current_user, get_db


class RelatorioSimplesOpcoesVaziasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(cls.engine, "connect")
        def register_functions(connection, _):
            connection.create_function("ST_AsEWKB", 1, lambda value: value)
            connection.create_function("AsEWKB", 1, lambda value: value)
            connection.create_function("ST_AsGeoJSON", 1, lambda value: None)
            connection.create_function("AsGeoJSON", 1, lambda value: None)

        cls.Session = sessionmaker(bind=cls.engine)
        with cls.engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE companies (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL
                )
            """))
            connection.execute(text("""
                CREATE TABLE perfis (
                    id INTEGER PRIMARY KEY,
                    nome TEXT NOT NULL
                )
            """))
            connection.execute(text("""
                CREATE TABLE usuarios (
                    id INTEGER PRIMARY KEY,
                    email TEXT NOT NULL,
                    nome TEXT,
                    senha_hash TEXT NOT NULL,
                    ativo BOOLEAN NOT NULL,
                    perfil_id INTEGER NOT NULL,
                    company_id INTEGER NOT NULL
                )
            """))
            connection.execute(text("""
                CREATE TABLE projetos (
                    id INTEGER PRIMARY KEY,
                    nome TEXT NOT NULL,
                    status TEXT NOT NULL,
                    coordenador_id INTEGER NOT NULL,
                    company_id INTEGER NOT NULL
                )
            """))
            connection.execute(text("""
                CREATE TABLE pesquisas (
                    id INTEGER PRIMARY KEY,
                    titulo TEXT NOT NULL,
                    ativo BOOLEAN NOT NULL,
                    projeto_id INTEGER NOT NULL
                )
            """))
            connection.execute(text("""
                CREATE TABLE perguntas (
                    id INTEGER PRIMARY KEY,
                    texto_pergunta TEXT NOT NULL,
                    tipo_pergunta TEXT NOT NULL,
                    ordem INTEGER NOT NULL,
                    eh_obrigatoria BOOLEAN NOT NULL,
                    eh_resposta_espontanea BOOLEAN NOT NULL DEFAULT 0,
                    ativo BOOLEAN NOT NULL,
                    pesquisa_id INTEGER NOT NULL
                )
            """))
            connection.execute(text("""
                CREATE TABLE opcoes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    texto TEXT NOT NULL,
                    ordem INTEGER NOT NULL DEFAULT 0,
                    pergunta_id INTEGER,
                    proxima_pergunta_id INTEGER
                )
            """))
            connection.execute(text("""
                CREATE TABLE coletas (
                    id INTEGER PRIMARY KEY,
                    pesquisa_id INTEGER NOT NULL,
                    agente_id INTEGER NOT NULL,
                    company_id INTEGER NOT NULL,
                    client_uuid TEXT NOT NULL,
                    data_inicio_coleta DATETIME NOT NULL,
                    data_fim_coleta DATETIME,
                    foi_offline BOOLEAN,
                    endereco_estimado TEXT,
                    status_sincronizacao TEXT,
                    inconformidade_localizacao BOOLEAN NOT NULL DEFAULT 0
                )
            """))
            connection.execute(text("""
                CREATE TABLE respostas (
                    id INTEGER PRIMARY KEY,
                    pergunta_id INTEGER NOT NULL,
                    coleta_id INTEGER NOT NULL,
                    valor_resposta TEXT NOT NULL
                )
            """))

        cls.app = FastAPI()
        cls.app.include_router(relatorios.router)

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
            for table in ("respostas", "opcoes", "coletas", "perguntas", "pesquisas", "projetos", "usuarios", "perfis", "companies"):
                connection.execute(text(f"DELETE FROM {table}"))

            connection.execute(text("""
                INSERT INTO companies (id, name)
                VALUES (1, 'Empresa A')
            """))
            connection.execute(text("""
                INSERT INTO perfis (id, nome)
                VALUES (10, 'Gerente')
            """))
            connection.execute(text("""
                INSERT INTO usuarios (id, email, nome, senha_hash, ativo, perfil_id, company_id)
                VALUES (1, 'manager@a.com', 'Manager', 'hash', 1, 10, 1)
            """))
            connection.execute(text("""
                INSERT INTO projetos (id, nome, status, coordenador_id, company_id)
                VALUES (100, 'Projeto A', 'Ativo', 1, 1)
            """))
            connection.execute(text("""
                INSERT INTO pesquisas (id, titulo, ativo, projeto_id)
                VALUES (1000, 'Pesquisa A', 1, 100)
            """))
            connection.execute(text("""
                INSERT INTO perguntas
                    (id, texto_pergunta, tipo_pergunta, ordem, eh_obrigatoria, eh_resposta_espontanea, ativo, pesquisa_id)
                VALUES (13, 'Pergunta com 5 opcoes', 'ESCOLHA_SIMPLES', 1, 1, 0, 1, 1000),
                       (14, 'Pergunta sem resposta', 'ESCOLHA_SIMPLES', 2, 1, 0, 1, 1000),
                       (15, 'Pergunta completa', 'ESCOLHA_SIMPLES', 3, 1, 0, 1, 1000)
            """))
            connection.execute(text("""
                INSERT INTO opcoes (texto, ordem, pergunta_id)
                VALUES ('Clécio Luís', 1, 13),
                       ('Dr. Furlan', 2, 13),
                       ('Marcos Réategui', 3, 13),
                       ('BRANCO/NULO', 4, 13),
                       ('NS/SR', 5, 13),
                       ('Clécio Luís', 1, 14),
                       ('Dr. Furlan', 2, 14),
                       ('Marcos Réategui', 3, 14),
                       ('BRANCO/NULO', 4, 14),
                       ('NS/SR', 5, 14),
                       ('Clécio Luís', 1, 15),
                       ('Dr. Furlan', 2, 15),
                       ('Marcos Réategui', 3, 15),
                       ('BRANCO/NULO', 4, 15),
                       ('NS/SR', 5, 15)
            """))
            connection.execute(text("""
                INSERT INTO coletas (id, pesquisa_id, agente_id, company_id, client_uuid, data_inicio_coleta, data_fim_coleta, foi_offline, status_sincronizacao, inconformidade_localizacao)
                VALUES (100, 1000, 1, 1, 'uuid-1', '2026-08-11 10:00:00', '2026-08-11 10:05:00', 0, 'sincronizado', 0),
                       (101, 1000, 1, 1, 'uuid-2', '2026-08-11 10:10:00', '2026-08-11 10:15:00', 0, 'sincronizado', 0),
                       (102, 1000, 1, 1, 'uuid-3', '2026-08-11 10:20:00', '2026-08-11 10:25:00', 0, 'sincronizado', 0),
                       (103, 1000, 1, 1, 'uuid-4', '2026-08-11 10:30:00', '2026-08-11 10:35:00', 0, 'sincronizado', 0),
                       (104, 1000, 1, 1, 'uuid-5', '2026-08-11 10:40:00', '2026-08-11 10:45:00', 0, 'sincronizado', 0),
                       (105, 1000, 1, 1, 'uuid-6', '2026-08-11 10:50:00', '2026-08-11 10:55:00', 0, 'sincronizado', 0),
                       (106, 1000, 1, 1, 'uuid-7', '2026-08-11 11:00:00', '2026-08-11 11:05:00', 0, 'sincronizado', 0),
                       (107, 1000, 1, 1, 'uuid-8', '2026-08-11 11:10:00', '2026-08-11 11:15:00', 0, 'sincronizado', 0),
                       (108, 1000, 1, 1, 'uuid-9', '2026-08-11 11:20:00', '2026-08-11 11:25:00', 0, 'sincronizado', 0)
            """))
            connection.execute(text("""
                INSERT INTO respostas (id, pergunta_id, coleta_id, valor_resposta)
                VALUES (1, 13, 100, 'Clécio Luís'),
                       (2, 13, 101, 'Dr. Furlan'),
                       (3, 13, 102, 'BRANCO/NULO'),
                       (4, 13, 103, 'NS/SR'),
                       (5, 15, 104, 'Clécio Luís'),
                       (6, 15, 105, 'Dr. Furlan'),
                       (7, 15, 106, 'Marcos Réategui'),
                       (8, 15, 107, 'BRANCO/NULO'),
                       (9, 15, 108, 'NS/SR')
            """))

        self.current_user = SimpleNamespace(id=1, perfil_id=10, company_id=1)

        def override_get_current_user():
            return self.current_user

        self.app.dependency_overrides[get_current_user] = override_get_current_user

    def tearDown(self):
        self.app.dependency_overrides.pop(get_current_user, None)

    def test_categorical_questions_keep_configured_options_even_without_answers(self):
        response = self.client.get("/relatorios/pesquisas/1000/simples/")
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        resultados = {item["pergunta_id"]: item for item in payload["resultados"]}

        com_zero = resultados[13]
        self.assertEqual([item["opcao"] for item in com_zero["resultados"]], [
            "Clécio Luís",
            "Dr. Furlan",
            "Marcos Réategui",
            "BRANCO/NULO",
            "NS/SR",
        ])
        self.assertEqual(com_zero["resultados"][2]["contagem"], 0)
        self.assertEqual(com_zero["resultados"][2]["percentual"], 0.0)
        self.assertEqual(com_zero["total"], 4)
        self.assertEqual(sum(item["contagem"] for item in com_zero["resultados"]), 4)

        sem_resposta = resultados[14]
        self.assertEqual([item["contagem"] for item in sem_resposta["resultados"]], [0, 0, 0, 0, 0])
        self.assertEqual(sem_resposta["total"], 0)

        completa = resultados[15]
        self.assertEqual([item["opcao"] for item in completa["resultados"]], [
            "Clécio Luís",
            "Dr. Furlan",
            "Marcos Réategui",
            "BRANCO/NULO",
            "NS/SR",
        ])
        self.assertEqual(completa["total"], 5)
        self.assertEqual(sum(item["contagem"] for item in completa["resultados"]), 5)


if __name__ == "__main__":
    unittest.main()
