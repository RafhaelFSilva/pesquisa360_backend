import os
import re
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from shapely import wkb, wkt

os.environ.setdefault("SECRET_KEY", "test-only-collection-idempotency-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360 import crud, schemas
from pesquisa360.api.endpoints import coletas
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.core.utils import geojson_point, web_point
from pesquisa360.db import models


class CollectionIdempotencyTests(unittest.TestCase):
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

        cls.Session = sessionmaker(bind=cls.engine)
        with cls.engine.begin() as connection:
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
                    ativo BOOLEAN NOT NULL,
                    pesquisa_id INTEGER NOT NULL
                )
            """))
            connection.execute(text("""
                CREATE TABLE coletas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pesquisa_id INTEGER NOT NULL, agente_id INTEGER NOT NULL,
                    company_id INTEGER NOT NULL, client_uuid TEXT NOT NULL,
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
                    valor_resposta TEXT NOT NULL CHECK (valor_resposta <> '__FAIL__')
                )
            """))

        cls.current_user_id = 1
        cls.test_app = FastAPI()
        cls.test_app.include_router(coletas.router)

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
        self.geocoding_patcher = patch(
            "pesquisa360.crud.geocoding.obter_endereco_por_coords",
            return_value="Endereco de teste",
        )
        self.geocoding_patcher.start()
        self.addCleanup(self.geocoding_patcher.stop)
        self.current_user_id = 1
        type(self).current_user_id = 1
        with self.engine.begin() as connection:
            for table in ("respostas", "coletas", "perguntas", "pesquisas", "projetos", "usuarios"):
                connection.execute(text(f"DELETE FROM {table}"))
            connection.execute(text("""
                INSERT INTO usuarios
                    (id, email, nome, senha_hash, ativo, perfil_id, company_id)
                VALUES (1, 'agent-a1@example.com', 'Agent A1', 'hash', 1, 99, 10),
                       (2, 'agent-a2@example.com', 'Agent A2', 'hash', 1, 99, 10),
                       (3, 'agent-b@example.com', 'Agent B', 'hash', 1, 99, 20)
            """))
            connection.execute(text("""
                INSERT INTO projetos
                    (id, nome, status, coordenador_id, company_id)
                VALUES (10, 'Projeto A', 'Ativo', 1, 10),
                       (20, 'Projeto B', 'Ativo', 3, 20)
            """))
            connection.execute(text("""
                INSERT INTO pesquisas (id, titulo, ativo, projeto_id)
                VALUES (100, 'Pesquisa A1', 1, 10),
                       (101, 'Pesquisa A2', 1, 10),
                       (200, 'Pesquisa B', 1, 20)
            """))
            connection.execute(text("""
                INSERT INTO perguntas
                    (id, texto_pergunta, tipo_pergunta, ordem, eh_obrigatoria, ativo, pesquisa_id)
                VALUES (1000, 'Pergunta A1.1', 'TEXTO', 1, 1, 1, 100),
                       (1001, 'Pergunta A1.2', 'TEXTO', 2, 0, 1, 100),
                       (1002, 'Pergunta A1.3', 'TEXTO', 3, 0, 1, 100),
                       (1010, 'Pergunta A2', 'TEXTO', 1, 1, 1, 101),
                       (2000, 'Pergunta B', 'TEXTO', 1, 1, 1, 200)
            """))

    def payload(
        self,
        client_uuid,
        answer="Resposta original",
        data_inicio_coleta="2026-08-02T12:00:00Z",
        data_fim_coleta="2026-08-02T12:05:00Z",
        localizacao_inicio=None,
        localizacao_fim=None,
    ):
        payload = {
            "client_uuid": str(client_uuid),
            "data_inicio_coleta": data_inicio_coleta,
            "data_fim_coleta": data_fim_coleta,
            "respostas": [{"pergunta_id": 1000, "valor_resposta": answer}],
        }
        if localizacao_inicio is not None:
            payload["localizacao_inicio"] = localizacao_inicio
        if localizacao_fim is not None:
            payload["localizacao_fim"] = localizacao_fim
        return payload

    def submit(self, client_uuid, answer="Resposta original", pesquisa_id=100):
        return self.client.post(
            f"/pesquisas/{pesquisa_id}/coletas/",
            json=self.payload(client_uuid, answer),
        )

    def submit_answers(self, client_uuid, respostas, pesquisa_id=100):
        payload = self.payload(client_uuid)
        payload["respostas"] = respostas
        return self.client.post(
            f"/pesquisas/{pesquisa_id}/coletas/",
            json=payload,
        )

    def scalar(self, statement, params=None):
        with self.engine.connect() as connection:
            return connection.execute(text(statement), params or {}).scalar_one()

    def test_valid_client_uuid_creates_collection(self):
        response = self.submit(uuid4())
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM coletas"), 1)

    def test_valid_mobile_coordinates_are_stored_as_longitude_latitude(self):
        response = self.client.post(
            "/pesquisas/100/coletas/",
            json=self.payload(
                uuid4(),
                localizacao_inicio={"lat": -0.034, "lon": -51.069},
            ),
        )
        self.assertEqual(response.status_code, 201)
        stored = self.scalar("SELECT localizacao_inicio FROM coletas")
        self.assertRegex(stored, r"POINT\s*\(-51\.069 -0\.034\)")

    def test_coordinate_boundaries_are_accepted(self):
        for latitude, longitude in ((-90, -180), (90, 180)):
            with self.subTest(latitude=latitude, longitude=longitude):
                point = schemas.Point(lat=latitude, lon=longitude)
                self.assertEqual((point.lat, point.lon), (latitude, longitude))

    def test_latitude_below_minimum_returns_422(self):
        response = self.client.post(
            "/pesquisas/100/coletas/",
            json=self.payload(uuid4(), localizacao_inicio={"lat": -90.01, "lon": 0}),
        )
        self.assertEqual(response.status_code, 422)

    def test_latitude_above_maximum_returns_422(self):
        response = self.client.post(
            "/pesquisas/100/coletas/",
            json=self.payload(uuid4(), localizacao_inicio={"lat": 90.01, "lon": 0}),
        )
        self.assertEqual(response.status_code, 422)

    def test_longitude_below_minimum_returns_422(self):
        response = self.client.post(
            "/pesquisas/100/coletas/",
            json=self.payload(uuid4(), localizacao_inicio={"lat": 0, "lon": -180.01}),
        )
        self.assertEqual(response.status_code, 422)

    def test_longitude_above_maximum_returns_422(self):
        response = self.client.post(
            "/pesquisas/100/coletas/",
            json=self.payload(uuid4(), localizacao_inicio={"lat": 0, "lon": 180.01}),
        )
        self.assertEqual(response.status_code, 422)

    def test_collection_locations_are_serialized_for_web_as_lat_lng(self):
        response = self.client.post(
            "/pesquisas/100/coletas/",
            json=self.payload(
                uuid4(),
                localizacao_inicio={"lat": -0.034, "lon": -51.069},
                localizacao_fim={"lat": -0.035, "lon": -51.070},
            ),
        )
        self.assertEqual(response.status_code, 201)
        collection = self.client.get("/pesquisas/100/coletas/").json()[0]
        self.assertEqual(collection["localizacao_inicio"], {"lat": -0.034, "lng": -51.069})
        self.assertEqual(collection["localizacao_fim"], {"lat": -0.035, "lng": -51.07})
        self.assertNotIn("lon", collection["localizacao_inicio"])

    def test_geojson_point_uses_longitude_before_latitude(self):
        self.assertEqual(
            geojson_point(longitude=-51.069, latitude=-0.034),
            {"type": "Point", "coordinates": [-51.069, -0.034]},
        )

    def test_monitoring_returns_lat_lng_without_inversion(self):
        self.client.post(
            "/pesquisas/100/coletas/",
            json=self.payload(
                uuid4(),
                localizacao_inicio={"lat": -0.034, "lon": -51.069},
            ),
        )
        location = self.client.get(
            "/pesquisas/100/coletas/monitoramento/"
        ).json()[0]["localizacao_inicio"]
        self.assertEqual(location, {"lat": -0.034, "lng": -51.069})

    def test_web_and_geofence_conversion_does_not_invert_coordinates(self):
        coordinate = schemas.Coordenada(lat=-0.034, lng=-51.069)
        self.assertEqual(web_point(coordinate.lat, coordinate.lng), {"lat": -0.034, "lng": -51.069})
        self.assertEqual(f"{coordinate.lng} {coordinate.lat}", "-51.069 -0.034")

    def test_idempotent_retry_keeps_original_coordinates(self):
        client_uuid = uuid4()
        self.client.post(
            "/pesquisas/100/coletas/",
            json=self.payload(
                client_uuid,
                localizacao_inicio={"lat": -0.034, "lon": -51.069},
            ),
        )
        self.client.post(
            "/pesquisas/100/coletas/",
            json=self.payload(
                client_uuid,
                localizacao_inicio={"lat": 10.0, "lon": 20.0},
            ),
        )
        stored = self.scalar("SELECT localizacao_inicio FROM coletas")
        self.assertRegex(stored, r"POINT\s*\(-51\.069 -0\.034\)")

    def test_created_collection_uses_canonical_date_fields(self):
        self.submit(uuid4())
        with self.Session() as db:
            coleta = db.query(models.Coleta).one()
            self.assertEqual(coleta.data_inicio_coleta, datetime(2026, 8, 2, 12, 0))
            self.assertEqual(coleta.data_fim_coleta, datetime(2026, 8, 2, 12, 5))

    def test_created_collection_serializes_canonical_date_fields(self):
        self.submit(uuid4())
        with self.Session() as db:
            serialized = schemas.Coleta.model_validate(db.query(models.Coleta).one())
            self.assertEqual(serialized.data_inicio_coleta, datetime(2026, 8, 2, 12, 0))
            self.assertEqual(serialized.data_fim_coleta, datetime(2026, 8, 2, 12, 5))

    def _create_collection_with_period(self, start, end):
        payload = self.payload(uuid4(), data_inicio_coleta=start, data_fim_coleta=end)
        response = self.client.post("/pesquisas/100/coletas/", json=payload)
        self.assertEqual(response.status_code, 201)

    def _seed_collection_periods(self):
        self._create_collection_with_period("2026-08-01T10:00:00Z", "2026-08-01T10:30:00Z")
        self._create_collection_with_period("2026-08-02T10:00:00Z", "2026-08-02T10:30:00Z")
        self._create_collection_with_period("2026-08-03T10:00:00Z", "2026-08-03T10:30:00Z")

    def test_list_collections_by_survey_uses_canonical_date_fields(self):
        self._create_collection_with_period("2026-08-01T10:00:00Z", "2026-08-01T10:30:00Z")
        response = self.client.get("/pesquisas/100/coletas/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertIn("data_inicio_coleta", response.json()[0])
        self.assertIn("data_fim_coleta", response.json()[0])

    def test_list_collections_without_date_filters(self):
        self._seed_collection_periods()
        response = self.client.get("/pesquisas/100/coletas/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 3)

    def test_list_collections_filters_by_start_date(self):
        self._seed_collection_periods()
        response = self.client.get(
            "/pesquisas/100/coletas/",
            params={"data_inicio_coleta": "2026-08-02T00:00:00Z"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 2)

    def test_list_collections_filters_by_end_date(self):
        self._seed_collection_periods()
        response = self.client.get(
            "/pesquisas/100/coletas/",
            params={"data_fim_coleta": "2026-08-02T23:59:59Z"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 2)

    def test_list_collections_filters_by_interval(self):
        self._seed_collection_periods()
        response = self.client.get(
            "/pesquisas/100/coletas/",
            params={
                "data_inicio_coleta": "2026-08-02T00:00:00Z",
                "data_fim_coleta": "2026-08-02T23:59:59Z",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)

    def test_monitoring_query_uses_canonical_start_date(self):
        self._create_collection_with_period("2026-08-01T10:00:00Z", "2026-08-01T10:30:00Z")
        response = self.client.get("/pesquisas/100/coletas/monitoramento/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["data_inicio_coleta"], "2026-08-01T10:00:00")

        with self.Session() as db:
            rows = crud.get_coletas_monitoramento(db, 100, db.get(models.Usuario, 1))
            self.assertEqual(rows[0].data_inicio_coleta, datetime(2026, 8, 1, 10, 0))

    def test_basic_report_does_not_access_nonexistent_collection_dates(self):
        self._create_collection_with_period("2026-08-01T10:00:00Z", "2026-08-01T10:30:00Z")
        with self.Session() as db:
            report = crud.get_relatorio_pesquisa(db, 100, db.get(models.Usuario, 1))
            self.assertEqual(report["total_coletas"], 1)

    def test_idempotent_response_keeps_original_canonical_dates(self):
        client_uuid = uuid4()
        first = self.client.post(
            "/pesquisas/100/coletas/",
            json=self.payload(client_uuid, data_inicio_coleta="2026-08-01T10:00:00Z"),
        )
        second = self.client.post(
            "/pesquisas/100/coletas/",
            json=self.payload(client_uuid, data_inicio_coleta="2026-08-09T10:00:00Z"),
        )
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(
            self.scalar("SELECT data_inicio_coleta FROM coletas"),
            "2026-08-01 10:00:00.000000",
        )

    def test_collection_without_answers_is_allowed_atomically(self):
        response = self.submit_answers(uuid4(), [])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM coletas"), 1)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM respostas"), 0)

    def test_collection_with_multiple_valid_answers_is_committed(self):
        response = self.submit_answers(uuid4(), [
            {"pergunta_id": 1000, "valor_resposta": "A"},
            {"pergunta_id": 1001, "valor_resposta": "B"},
            {"pergunta_id": 1002, "valor_resposta": "C"},
        ])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM coletas"), 1)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM respostas"), 3)

    def test_failure_in_any_answer_rolls_back_collection_and_all_answers(self):
        positions = (0, 1, 2)
        for failure_position in positions:
            with self.subTest(failure_position=failure_position):
                answers = [
                    {"pergunta_id": 1000, "valor_resposta": "A"},
                    {"pergunta_id": 1001, "valor_resposta": "B"},
                    {"pergunta_id": 1002, "valor_resposta": "C"},
                ]
                answers[failure_position]["valor_resposta"] = "__FAIL__"
                with self.assertRaises(IntegrityError):
                    self.submit_answers(uuid4(), answers)
                self.assertEqual(self.scalar("SELECT COUNT(*) FROM coletas"), 0)
                self.assertEqual(self.scalar("SELECT COUNT(*) FROM respostas"), 0)

    def test_question_from_another_survey_returns_404_without_persistence(self):
        response = self.submit_answers(uuid4(), [
            {"pergunta_id": 1010, "valor_resposta": "Invalida"},
        ])
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM coletas"), 0)

    def test_question_from_another_tenant_returns_404_without_persistence(self):
        response = self.submit_answers(uuid4(), [
            {"pergunta_id": 2000, "valor_resposta": "Invalida"},
        ])
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM coletas"), 0)

    def test_missing_question_returns_404_without_persistence(self):
        response = self.submit_answers(uuid4(), [
            {"pergunta_id": 9999, "valor_resposta": "Invalida"},
        ])
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM coletas"), 0)

    def test_duplicate_question_is_rejected_without_partial_persistence(self):
        response = self.submit_answers(uuid4(), [
            {"pergunta_id": 1000, "valor_resposta": "A"},
            {"pergunta_id": 1000, "valor_resposta": "B"},
        ])
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM coletas"), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM respostas"), 0)

    def test_invalid_or_missing_client_uuid_returns_422(self):
        invalid = self.client.post(
            "/pesquisas/100/coletas/",
            json=self.payload("not-a-uuid"),
        )
        missing_payload = self.payload(uuid4())
        missing_payload.pop("client_uuid")
        missing = self.client.post("/pesquisas/100/coletas/", json=missing_payload)
        self.assertEqual((invalid.status_code, missing.status_code), (422, 422))

    def test_identical_retry_returns_same_collection(self):
        client_uuid = uuid4()
        first = self.submit(client_uuid)
        second = self.submit(client_uuid)
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM coletas"), 1)

    def test_identical_retry_does_not_duplicate_answers(self):
        client_uuid = uuid4()
        self.submit(client_uuid)
        self.submit(client_uuid)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM respostas"), 1)

    def test_retry_with_different_payload_does_not_change_original(self):
        client_uuid = uuid4()
        self.submit(client_uuid, answer="Original")
        self.submit(client_uuid, answer="Alterada")
        answer = self.scalar("SELECT valor_resposta FROM respostas")
        self.assertEqual(answer, "Original")

    def test_same_uuid_from_other_agent_in_same_tenant_is_rejected(self):
        client_uuid = uuid4()
        self.submit(client_uuid)
        type(self).current_user_id = 2
        response = self.submit(client_uuid)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM coletas"), 1)

    def test_same_uuid_in_another_tenant_is_allowed(self):
        client_uuid = uuid4()
        first = self.submit(client_uuid)
        type(self).current_user_id = 3
        second = self.submit_answers(client_uuid, [
            {"pergunta_id": 2000, "valor_resposta": "Resposta Tenant B"},
        ], pesquisa_id=200)
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM coletas"), 2)

    def test_different_uuids_create_distinct_collections(self):
        first = self.submit(uuid4())
        second = self.submit(uuid4())
        self.assertNotEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM coletas"), 2)

    def test_integrity_conflict_rolls_back_and_returns_existing_collection(self):
        client_uuid = uuid4()
        coleta_in = schemas.ColetaCreate(
            client_uuid=client_uuid,
            data_inicio_coleta=datetime.now(timezone.utc),
            respostas=[],
        )
        existing = SimpleNamespace(id=77, agente_id=1, client_uuid=str(client_uuid))
        db = MagicMock()
        db.commit.side_effect = IntegrityError("insert", {}, Exception("unique"))

        with patch(
            "pesquisa360.crud._get_coleta_by_client_uuid",
            side_effect=[None, existing],
        ):
            result = crud.create_coleta(
                db=db,
                coleta_in=coleta_in,
                pesquisa_id=100,
                agente_id=1,
                company_id=10,
            )

        self.assertIs(result, existing)
        db.rollback.assert_called_once()

    def test_migration_has_exactly_one_new_head(self):
        config = Config("alembic.ini")
        heads = ScriptDirectory.from_config(config).get_heads()
        self.assertEqual(heads, ["f2a3b4c5d6e7"])


if __name__ == "__main__":
    unittest.main()
