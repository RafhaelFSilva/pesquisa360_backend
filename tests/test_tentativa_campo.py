"""PROMPT 03 -- TentativaCampo: abordagem operacional != Coleta.

Cobre TC-B01..TC-B10 sobre SQLite (mesma estrategia de
test_collection_idempotency): tabelas legadas em DDL cru, tabela nova criada
a partir do modelo (sem geometria, roda igual em SQLite e PostgreSQL).
"""
import os
import re
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from shapely import wkb, wkt

os.environ.setdefault("SECRET_KEY", "test-only-tentativa-campo-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.api.endpoints import coletas, tentativas_campo
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.db import models
from tests.acl_fixture import criar_tabelas_acl


class TentativaCampoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(cls.engine, "connect")
        def register_spatial_functions(connection, _):
            def point_coordinate(value, position):
                if value is None:
                    return None
                if isinstance(value, bytes):
                    value = value.decode()
                match = re.search(r"POINT\s*\(([-+0-9.eE]+)\s+([-+0-9.eE]+)\)", str(value))
                return float(match.group(position + 1)) if match else None

            def as_ewkb(value):
                if value is None:
                    return None
                if isinstance(value, bytes):
                    value = value.decode()
                geometry_text = str(value).split(";", 1)[-1]
                return wkb.dumps(wkt.loads(geometry_text), hex=True, srid=4326)

            connection.create_function("AsEWKB", 1, as_ewkb)
            connection.create_function("ST_AsEWKB", 1, as_ewkb)
            connection.create_function("GeomFromEWKT", 1, lambda value: value)
            connection.create_function("ST_Y", 1, lambda value: point_coordinate(value, 1))
            connection.create_function("ST_X", 1, lambda value: point_coordinate(value, 0))
            connection.create_function("ST_AsGeoJSON", 1, lambda value: None)
            connection.create_function("AsGeoJSON", 1, lambda value: None)

        criar_tabelas_acl(cls.engine)

        criar_tabelas_acl(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)
        with cls.engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT NOT NULL)
            """))
            # RBAC (ADR-037) le o NOME do perfil: a fixture precisa da tabela.
            connection.execute(text("CREATE TABLE perfis (id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT)"))
            connection.execute(text("INSERT INTO perfis (id,nome) VALUES (1,'Gerente'),(99,'Gerente')"))
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
            connection.execute(text("""
                CREATE TABLE pesquisas (
                    id INTEGER PRIMARY KEY, titulo TEXT NOT NULL, tipo_pesquisa TEXT,
                    ativo BOOLEAN NOT NULL, projeto_id INTEGER NOT NULL,
                    cerca_eletronica BLOB, tolerancia_metros INTEGER
                )
            """))
            connection.execute(text("""
                CREATE TABLE perguntas (
                    id INTEGER PRIMARY KEY, texto_pergunta TEXT NOT NULL,
                    tipo_pergunta TEXT NOT NULL, ordem INTEGER NOT NULL,
                    eh_obrigatoria BOOLEAN NOT NULL, eh_resposta_espontanea BOOLEAN NOT NULL DEFAULT 0,
                    papel_analitico VARCHAR(50), metadados_analiticos JSON NOT NULL DEFAULT '{}',
                    ativo BOOLEAN NOT NULL, pesquisa_id INTEGER NOT NULL,
                    aplicabilidade VARCHAR(20) NOT NULL DEFAULT 'GLOBAL'
                )
            """))
            connection.execute(text("""
                CREATE TABLE setores (
                    id INTEGER PRIMARY KEY, nome TEXT NOT NULL, meta INTEGER NOT NULL,
                    tolerancia INTEGER NOT NULL DEFAULT 50,
                    finalidade TEXT NOT NULL DEFAULT 'OPERACAO', geometria BLOB,
                    pesquisa_id INTEGER NOT NULL, agente_id INTEGER, municipio_territorio_id INTEGER)
            """))
            connection.execute(text("""
                CREATE TABLE setor_agentes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, setor_id INTEGER NOT NULL,
                    agente_id INTEGER NOT NULL, ativo BOOLEAN NOT NULL DEFAULT 1,
                    UNIQUE (setor_id, agente_id)
                )
            """))
            connection.execute(text("""
                CREATE TABLE coletas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pesquisa_id INTEGER NOT NULL, agente_id INTEGER NOT NULL,
                    company_id INTEGER NOT NULL, client_uuid TEXT NOT NULL, setor_id INTEGER,
                    foi_offline BOOLEAN, endereco_estimado TEXT,
                    status_sincronizacao TEXT, data_inicio_coleta DATETIME NOT NULL,
                    data_fim_coleta DATETIME, localizacao_inicio BLOB,
                    localizacao_fim BLOB, inconformidade_localizacao BOOLEAN NOT NULL DEFAULT 0,
                    CONSTRAINT uq_coletas_company_client_uuid UNIQUE (company_id, client_uuid)
                )
            """))
            connection.execute(text("""
                CREATE TABLE respostas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pergunta_id INTEGER NOT NULL, coleta_id INTEGER NOT NULL,
                    valor_resposta TEXT NOT NULL
                )
            """))
        # A tabela nova sai do proprio modelo: e o contrato que a migration
        # f7a8b9c0d1e2 materializa em PostgreSQL.
        models.TentativaCampo.__table__.create(cls.engine)

        cls.current_user_id = 1
        cls.test_app = FastAPI()
        cls.test_app.include_router(coletas.router)
        cls.test_app.include_router(tentativas_campo.router)

        def override_get_db():
            db = cls.Session()
            try:
                yield db
            finally:
                db.close()

        def override_current_user():
            db = cls.Session()
            try:
                yield db.get(models.Usuario, cls.current_user_id)
            finally:
                db.close()

        cls.test_app.dependency_overrides[get_db] = override_get_db
        cls.test_app.dependency_overrides[get_current_user] = override_current_user
        cls.client = TestClient(cls.test_app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.engine.dispose()

    def setUp(self):
        patcher = patch(
            "pesquisa360.crud.geocoding.obter_endereco_por_coords",
            return_value="Endereco de teste",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        type(self).current_user_id = 1
        with self.engine.begin() as connection:
            for table in (
                "tentativas_campo", "respostas", "coletas", "setor_agentes",
                "setores", "perguntas", "pesquisas", "projetos", "usuarios", "companies",
            ):
                connection.execute(text(f"DELETE FROM {table}"))
            connection.execute(text("INSERT INTO companies (id, name) VALUES (10, 'A'), (20, 'B')"))
            connection.execute(text("""
                INSERT INTO usuarios (id, email, nome, senha_hash, ativo, perfil_id, company_id)
                VALUES (1, 'agent-a1@example.com', 'Agent A1', 'hash', 1, 99, 10),
                       (2, 'agent-a2@example.com', 'Agent A2', 'hash', 1, 99, 10),
                       (3, 'agent-b@example.com', 'Agent B', 'hash', 1, 99, 20)
            """))
            connection.execute(text("""
                INSERT INTO projetos (id, nome, status, coordenador_id, company_id)
                VALUES (10, 'Projeto A', 'Ativo', 1, 10), (20, 'Projeto B', 'Ativo', 3, 20)
            """))
            connection.execute(text("""
                INSERT INTO pesquisas (id, titulo, ativo, projeto_id)
                VALUES (100, 'Pesquisa A1', 1, 10), (101, 'Pesquisa A2', 1, 10), (200, 'Pesquisa B', 1, 20)
            """))
            connection.execute(text("""
                INSERT INTO perguntas (id, texto_pergunta, tipo_pergunta, ordem, eh_obrigatoria, ativo, pesquisa_id)
                VALUES (1000, 'Pergunta A1.1', 'TEXTO', 1, 1, 1, 100)
            """))
            connection.execute(text("""
                INSERT INTO setores (id, nome, meta, pesquisa_id, agente_id)
                VALUES (500, 'Setor A1', 10, 100, NULL), (502, 'Setor A2', 10, 101, NULL),
                       (600, 'Setor B', 10, 200, NULL)
            """))
            connection.execute(text("""
                INSERT INTO setor_agentes (setor_id, agente_id, ativo)
                VALUES (500, 1, 1), (502, 1, 1), (600, 3, 1)
            """))

    # ------------------------------------------------------------------ helpers
    def payload(self, client_uuid=None, resultado="RECUSA", motivo="NAO_QUIS_PARTICIPAR", **extra):
        body = {
            "client_uuid": str(client_uuid or uuid4()),
            "setor_id": 500,
            "iniciada_em": "2026-08-27T10:00:00-03:00",
            "encerrada_em": "2026-08-27T10:01:00-03:00",
            "localizacao": {
                "lat": 0.0349,
                "lng": -51.0694,
                "accuracy": 8.5,
                "capturada_em": "2026-08-27T10:00:00-03:00",
            },
            "resultado": resultado,
            "motivo": motivo,
            "observacao": None,
            "coleta_client_uuid": None,
        }
        body.update(extra)
        return body

    def submit(self, body, pesquisa_id=100):
        return self.client.post(f"/pesquisas/{pesquisa_id}/tentativas-campo/", json=body)

    def submit_coleta(self, client_uuid, pesquisa_id=100, setor_id=500):
        return self.client.post(
            f"/pesquisas/{pesquisa_id}/coletas/",
            json={
                "client_uuid": str(client_uuid),
                "setor_id": setor_id,
                "data_inicio_coleta": "2026-08-27T10:00:00-03:00",
                "data_fim_coleta": "2026-08-27T10:05:00-03:00",
                "localizacao_inicio": {"lat": 0.0349, "lon": -51.0694},
                "respostas": [{"pergunta_id": 1000, "valor_resposta": "ok"}],
            },
        )

    def scalar(self, statement, params=None):
        with self.engine.connect() as connection:
            return connection.execute(text(statement), params or {}).scalar_one()

    def rows(self, statement, params=None):
        with self.engine.connect() as connection:
            return connection.execute(text(statement), params or {}).mappings().all()

    # ------------------------------------------------------------------- testes
    def test_TC_B01_recusa_valida(self):
        response = self.submit(self.payload())
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["resultado"], "RECUSA")
        self.assertEqual(body["motivo"], "NAO_QUIS_PARTICIPAR")
        self.assertEqual(body["agente_id"], 1)
        self.assertEqual(body["setor_id"], 500)
        self.assertIsNone(body["coleta_id"])
        row = self.rows("SELECT * FROM tentativas_campo")[0]
        self.assertEqual(row["company_id"], 10)
        self.assertAlmostEqual(row["latitude"], 0.0349)
        self.assertAlmostEqual(row["longitude"], -51.0694)
        self.assertAlmostEqual(row["precisao_metros"], 8.5)
        self.assertIsNotNone(row["capturada_em"])

    def test_TC_B02_nao_elegivel(self):
        response = self.submit(self.payload(resultado="NAO_ELEGIVEL", motivo="MENOR_DE_IDADE"))
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["resultado"], "NAO_ELEGIVEL")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM coletas"), 0)

    def test_TC_B03_concluida_com_referencia_de_coleta(self):
        coleta_uuid = uuid4()
        created = self.submit_coleta(coleta_uuid)
        self.assertEqual(created.status_code, 201, created.text)
        coleta_id = created.json()["id"]

        response = self.submit(
            self.payload(resultado="CONCLUIDA", motivo=None, coleta_client_uuid=str(coleta_uuid))
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["coleta_id"], coleta_id)
        self.assertEqual(response.json()["resultado"], "CONCLUIDA")

    def test_TC_B03b_concluida_antes_da_coleta_fica_pendente_422(self):
        # Ordem de sync: coleta primeiro. Se a tentativa chegar antes, 422
        # (o Mobile mantem pendente e reenvia depois) -- nunca cria vinculo falso.
        response = self.submit(
            self.payload(resultado="CONCLUIDA", motivo=None, coleta_client_uuid=str(uuid4()))
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM tentativas_campo"), 0)

    def test_TC_B04_mesmo_client_uuid_nao_duplica(self):
        client_uuid = uuid4()
        first = self.submit(self.payload(client_uuid))
        second = self.submit(self.payload(client_uuid, motivo="OUTRO_MOTIVO"))
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.json()["id"], second.json()["id"])
        # O retry NAO sobrescreve o original.
        self.assertEqual(second.json()["motivo"], "NAO_QUIS_PARTICIPAR")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM tentativas_campo"), 1)

        # Mesmo uuid por OUTRO agente do mesmo tenant: conflito, nao duplicata.
        type(self).current_user_id = 2
        other = self.submit(self.payload(client_uuid, setor_id=None))
        self.assertEqual(other.status_code, 409)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM tentativas_campo"), 1)

    def test_TC_B05_outro_tenant_recebe_404(self):
        type(self).current_user_id = 3  # empresa B
        response = self.submit(self.payload(setor_id=None), pesquisa_id=100)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM tentativas_campo"), 0)

    def test_TC_B06_agente_id_do_payload_e_ignorado(self):
        response = self.submit(self.payload(agente_id=3))
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["agente_id"], 1)
        self.assertEqual(self.scalar("SELECT agente_id FROM tentativas_campo"), 1)

    def test_TC_B07_company_id_do_payload_e_ignorado(self):
        response = self.submit(self.payload(company_id=20))
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.scalar("SELECT company_id FROM tentativas_campo"), 10)

    def test_TC_B08_setor_de_outra_pesquisa_rejeitado(self):
        response = self.submit(self.payload(setor_id=502))  # setor da pesquisa 101
        self.assertEqual(response.status_code, 404, response.text)
        cross = self.submit(self.payload(setor_id=600))  # setor da empresa B
        self.assertEqual(cross.status_code, 404)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM tentativas_campo"), 0)

    def test_TC_B09_gps_invalido_rejeitado(self):
        for loc in (
            {"lat": 91, "lng": -51.0},
            {"lat": 0.0, "lng": -181},
            {"lat": None, "lng": -51.0},
            {"lat": "abc", "lng": -51.0},
        ):
            response = self.submit(self.payload(localizacao=loc))
            self.assertEqual(response.status_code, 422, loc)
        sem = self.payload()
        del sem["localizacao"]
        self.assertEqual(self.submit(sem).status_code, 422)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM tentativas_campo"), 0)

    def test_TC_B10_tentativa_sem_coleta_nao_cria_coleta(self):
        for resultado in ("RECUSA", "NAO_ELEGIVEL", "DESISTENCIA", "INCOMPLETA", "PROBLEMA_TECNICO", "OUTRO"):
            response = self.submit(self.payload(resultado=resultado, motivo="OUTRO"))
            self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM tentativas_campo"), 6)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM coletas"), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM respostas"), 0)

    def test_em_andamento_nao_sincroniza(self):
        response = self.submit(self.payload(resultado="EM_ANDAMENTO", motivo=None))
        self.assertEqual(response.status_code, 422)

    def test_setor_nulo_aceito_e_motivo_texto_livre_rejeitado(self):
        ok = self.submit(self.payload(setor_id=None))
        self.assertEqual(ok.status_code, 201, ok.text)
        self.assertIsNone(ok.json()["setor_id"])
        livre = self.submit(self.payload(motivo="nao quis participar"))
        self.assertEqual(livre.status_code, 422)


if __name__ == "__main__":
    unittest.main()
