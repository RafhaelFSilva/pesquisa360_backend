import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from shapely import wkb, wkt

os.environ.setdefault("SECRET_KEY", "test-only-synthetic-import-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360 import schemas
from pesquisa360.db import models
from pesquisa360.services import synthetic_import
from tests.acl_fixture import criar_tabelas_acl


class SyntheticImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )

        @event.listens_for(cls.engine, "connect")
        def spatial_functions(connection, _):
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

        criar_tabelas_acl(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)
        with cls.engine.begin() as c:
            c.execute(text("CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT, is_active BOOLEAN)"))
            c.execute(text("CREATE TABLE perfis (id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT)"))
            c.execute(text("""CREATE TABLE usuarios (
                id INTEGER PRIMARY KEY, email TEXT, nome TEXT, senha_hash TEXT,
                ativo BOOLEAN, perfil_id INTEGER, company_id INTEGER)"""))
            c.execute(text("""CREATE TABLE projetos (
                id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT, status TEXT,
                data_inicio DATE, data_fim DATE, coordenador_id INTEGER, company_id INTEGER)"""))
            c.execute(text("""CREATE TABLE pesquisas (
                id INTEGER PRIMARY KEY, titulo TEXT, tipo_pesquisa TEXT, ativo BOOLEAN,
                projeto_id INTEGER, cerca_eletronica BLOB, tolerancia_metros INTEGER)"""))
            c.execute(text("""CREATE TABLE perguntas (
                id INTEGER PRIMARY KEY, texto_pergunta TEXT, tipo_pergunta TEXT,
                ordem INTEGER, eh_obrigatoria BOOLEAN, eh_resposta_espontanea BOOLEAN,
                papel_analitico TEXT, metadados_analiticos JSON, ativo BOOLEAN,
                pesquisa_id INTEGER, aplicabilidade TEXT)"""))
            c.execute(text("""CREATE TABLE opcoes (
                id INTEGER PRIMARY KEY, texto TEXT, ordem INTEGER,
                pergunta_id INTEGER, proxima_pergunta_id INTEGER)"""))
            c.execute(text("""CREATE TABLE setores (
                id INTEGER PRIMARY KEY, nome TEXT, meta INTEGER, tolerancia INTEGER,
                finalidade TEXT, geometria BLOB, pesquisa_id INTEGER, agente_id INTEGER,
                municipio_territorio_id INTEGER)"""))
            c.execute(text("""CREATE TABLE setor_agentes (
                id INTEGER PRIMARY KEY, setor_id INTEGER, agente_id INTEGER, ativo BOOLEAN)"""))
            c.execute(text("""CREATE TABLE coletas (
                id INTEGER PRIMARY KEY AUTOINCREMENT, pesquisa_id INTEGER, agente_id INTEGER,
                company_id INTEGER, client_uuid TEXT, setor_id INTEGER,
                is_synthetic BOOLEAN NOT NULL DEFAULT 0, seed_run_id CHAR(32),
                synthetic_source TEXT, synthetic_operator_id INTEGER,
                foi_offline BOOLEAN, endereco_estimado TEXT, status_sincronizacao TEXT,
                data_inicio_coleta DATETIME, data_fim_coleta DATETIME,
                localizacao_inicio BLOB, localizacao_fim BLOB,
                inconformidade_localizacao BOOLEAN NOT NULL DEFAULT 0,
                UNIQUE(company_id, client_uuid))"""))
            c.execute(text("""CREATE TABLE respostas (
                id INTEGER PRIMARY KEY AUTOINCREMENT, pergunta_id INTEGER,
                coleta_id INTEGER, valor_resposta TEXT)"""))

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        with self.engine.begin() as c:
            for table in (
                "respostas", "coletas", "setor_agentes", "setores", "opcoes",
                "perguntas", "pesquisas", "projetos", "usuario_projeto_acessos",
                "usuario_empresa_acessos", "usuarios", "perfis", "companies",
            ):
                c.execute(text(f"DELETE FROM {table}"))
            c.execute(text("INSERT INTO companies VALUES (10,'Tenant A',1),(20,'Tenant B',1)"))
            c.execute(text("INSERT INTO perfis VALUES (1,'Superadmin',NULL),(2,'Agente',NULL),(3,'Gerente',NULL)"))
            c.execute(text("""INSERT INTO usuarios VALUES
                (1,'admin@example.com','Admin','x',1,1,10),
                (2,'agent@example.com','Agent','x',1,2,10),
                (3,'other@example.com','Other','x',1,2,20),
                (4,'manager@example.com','Manager','x',1,3,10)"""))
            c.execute(text("INSERT INTO projetos VALUES (100,'Demo',NULL,'Ativo',NULL,NULL,1,10)"))
            c.execute(text("INSERT INTO pesquisas VALUES (200,'Demo',NULL,1,100,NULL,NULL)"))
            c.execute(text("""INSERT INTO perguntas VALUES
                (300,'Escolha','ESCOLHA_SIMPLES',1,1,0,NULL,'{}',1,200,'GLOBAL'),
                (301,'Dois nomes','MULTIPLA_ESCOLHA',2,1,1,NULL,
                 :metadata,1,200,'GLOBAL')"""), {
                     "metadata": json.dumps({"min_selections": 2, "max_selections": 2})
                 })
            c.execute(text("INSERT INTO opcoes VALUES (400,'Opção A',1,300,NULL),(401,'Opção B',2,300,NULL)"))
            c.execute(text("""INSERT INTO usuario_empresa_acessos
                (usuario_id,company_id,acesso_todos_projetos,ativo,principal)
                VALUES (2,10,1,1,1),(3,20,1,1,1)"""))

    def payload(self, run_id, record_key="1", second='["Nome A","Nome B"]'):
        client_uuid = synthetic_import.deterministic_client_uuid(run_id, 200, record_key)
        return schemas.ColetaCreate(
            client_uuid=client_uuid,
            data_inicio_coleta=datetime(2026, 8, 24, 11, 0, tzinfo=timezone.utc),
            data_fim_coleta=datetime(2026, 8, 24, 11, 8, tzinfo=timezone.utc),
            localizacao_inicio=schemas.Point(lat=0.04, lon=-51.07),
            localizacao_fim=schemas.Point(lat=0.0401, lon=-51.0701),
            respostas=[
                schemas.RespostaCreate(pergunta_id=300, valor_resposta="opcao a"),
                schemas.RespostaCreate(pergunta_id=301, valor_resposta=second),
            ],
        )

    def test_create_and_replay_are_safe_and_never_geocode(self):
        run_id = uuid4()
        with self.Session() as db, patch(
            "pesquisa360.crud.geocoding.obter_endereco_por_coords"
        ) as geocode, patch("pesquisa360.services.synthetic_import.auditoria.registrar"):
            created = synthetic_import.create_synthetic_coleta(
                db,
                operator=db.get(models.Usuario, 1),
                credited_agent=db.get(models.Usuario, 2),
                survey_id=200,
                seed_run_id=run_id,
                record_key="1",
                payload=self.payload(run_id),
                synthetic_source="demo-ap-2026",
            )
            replayed = synthetic_import.create_synthetic_coleta(
                db,
                operator=db.get(models.Usuario, 1),
                credited_agent=db.get(models.Usuario, 2),
                survey_id=200,
                seed_run_id=run_id,
                record_key="1",
                payload=self.payload(run_id),
                synthetic_source="demo-ap-2026",
            )
            self.assertEqual(created.id, replayed.id)
            self.assertTrue(created.is_synthetic)
            self.assertEqual(created.synthetic_operator_id, 1)
            self.assertIsNone(created.endereco_estimado)
            self.assertEqual(db.query(models.Coleta).count(), 1)
            self.assertEqual(json.loads(created.respostas[1].valor_resposta), ["Nome A", "Nome B"])
            geocode.assert_not_called()

    def test_non_admin_invalid_cardinality_other_question_and_agent_acl_fail(self):
        run_id = uuid4()
        cases = []
        with self.Session() as db, patch("pesquisa360.services.synthetic_import.auditoria.registrar"):
            common = db.get(models.Usuario, 2)
            admin = db.get(models.Usuario, 1)
            other = db.get(models.Usuario, 3)
            with self.assertRaises(HTTPException) as ctx:
                synthetic_import.create_synthetic_coleta(
                    db, operator=common, credited_agent=common, survey_id=200,
                    seed_run_id=run_id, record_key="a", payload=self.payload(run_id, "a"),
                    synthetic_source="demo",
                )
            cases.append(ctx.exception.status_code)
            with self.assertRaises(HTTPException) as ctx:
                synthetic_import.create_synthetic_coleta(
                    db, operator=admin, credited_agent=common, survey_id=200,
                    seed_run_id=run_id, record_key="b",
                    payload=self.payload(run_id, "b", '["Nome A"]'), synthetic_source="demo",
                )
            cases.append(ctx.exception.status_code)
            bad = self.payload(run_id, "c")
            bad.respostas[0].pergunta_id = 999
            with self.assertRaises(HTTPException) as ctx:
                synthetic_import.create_synthetic_coleta(
                    db, operator=admin, credited_agent=common, survey_id=200,
                    seed_run_id=run_id, record_key="c", payload=bad, synthetic_source="demo",
                )
            cases.append(ctx.exception.status_code)
            with self.assertRaises(HTTPException) as ctx:
                synthetic_import.create_synthetic_coleta(
                    db, operator=admin, credited_agent=other, survey_id=200,
                    seed_run_id=run_id, record_key="d", payload=self.payload(run_id, "d"),
                    synthetic_source="demo",
                )
            cases.append(ctx.exception.status_code)
        self.assertEqual(cases, [403, 422, 422, 403])

    def call(self, db, run_id, key="1", payload=None, agent_id=2, operator_id=1,
             source="demo"):
        return synthetic_import.create_synthetic_coleta(
            db, operator=db.get(models.Usuario, operator_id),
            credited_agent=db.get(models.Usuario, agent_id), survey_id=200,
            seed_run_id=run_id, record_key=key,
            payload=payload or self.payload(run_id, key), synthetic_source=source,
        )

    def test_manager_and_agent_cannot_import_but_superadmin_can(self):
        run_id = uuid4()
        with self.Session() as db, patch("pesquisa360.services.synthetic_import.auditoria.registrar"):
            for operator_id in (2, 4):
                with self.assertRaises(HTTPException) as ctx:
                    self.call(db, run_id, str(operator_id), operator_id=operator_id)
                self.assertEqual(ctx.exception.status_code, 403)
            self.assertTrue(self.call(db, run_id, "admin").is_synthetic)

    def test_missing_survey_question_option_and_required_answer_fail(self):
        run_id = uuid4()
        with self.Session() as db, patch("pesquisa360.services.synthetic_import.auditoria.registrar"):
            with self.assertRaises(HTTPException) as ctx:
                synthetic_import.create_synthetic_coleta(
                    db, operator=db.get(models.Usuario, 1), credited_agent=db.get(models.Usuario, 2),
                    survey_id=999, seed_run_id=run_id, record_key="missing",
                    payload=self.payload(run_id, "missing"), synthetic_source="demo")
            self.assertEqual(ctx.exception.status_code, 422)  # UUID pertence a outra pesquisa

            invalid_option = self.payload(run_id, "option")
            invalid_option.respostas[0].valor_resposta = "inexistente"
            with self.assertRaises(HTTPException) as ctx:
                self.call(db, run_id, "option", invalid_option)
            self.assertEqual(ctx.exception.status_code, 422)

            missing = self.payload(run_id, "required")
            missing.respostas.pop()
            with self.assertRaises(HTTPException) as ctx:
                self.call(db, run_id, "required", missing)
            self.assertEqual(ctx.exception.status_code, 422)

            with db.begin_nested():
                db.execute(text("INSERT INTO perguntas VALUES "
                    "(302,'Outra pesquisa','ESCOLHA_SIMPLES',1,1,0,NULL,'{}',1,999,'GLOBAL')"))
            foreign = self.payload(run_id, "foreign")
            foreign.respostas[0].pergunta_id = 302
            with self.assertRaises(HTTPException) as ctx:
                self.call(db, run_id, "foreign", foreign)
            self.assertEqual(ctx.exception.status_code, 422)

    def test_missing_survey_with_matching_uuid_returns_404(self):
        run_id = uuid4(); key = "missing"
        payload = self.payload(run_id, key)
        payload.client_uuid = synthetic_import.deterministic_client_uuid(run_id, 999, key)
        with self.Session() as db, patch("pesquisa360.services.synthetic_import.auditoria.registrar"):
            with self.assertRaises(HTTPException) as ctx:
                synthetic_import.create_synthetic_coleta(
                    db, operator=db.get(models.Usuario, 1), credited_agent=db.get(models.Usuario, 2),
                    survey_id=999, seed_run_id=run_id, record_key=key,
                    payload=payload, synthetic_source="demo")
            self.assertEqual(ctx.exception.status_code, 404)

    def test_malformed_multiple_conflict_same_run_and_rollback(self):
        run_id = uuid4()
        with self.Session() as db, patch("pesquisa360.services.synthetic_import.auditoria.registrar"):
            malformed = self.payload(run_id, "bad-json", "Nome A, Nome B")
            with self.assertRaises(HTTPException) as ctx:
                self.call(db, run_id, "bad-json", malformed)
            self.assertEqual(ctx.exception.status_code, 422)

            first = self.call(db, run_id, "one")
            second = self.call(db, run_id, "two")
            self.assertNotEqual(first.id, second.id)
            self.assertEqual(db.query(models.Coleta).filter(
                models.Coleta.seed_run_id == run_id).count(), 2)

            conflict = self.payload(run_id, "one")
            conflict.respostas[0].valor_resposta = "Opção B"
            with self.assertRaises(HTTPException) as ctx:
                self.call(db, run_id, "one", conflict)
            self.assertEqual(ctx.exception.status_code, 409)

        other_run = uuid4()
        with self.Session() as db, patch("pesquisa360.services.synthetic_import.auditoria.registrar"), \
                patch.object(db, "commit", side_effect=RuntimeError("forced")):
            with self.assertRaises(RuntimeError):
                self.call(db, other_run, "rollback")
        with self.Session() as db:
            self.assertEqual(db.query(models.Coleta).filter(
                models.Coleta.client_uuid == str(synthetic_import.deterministic_client_uuid(
                    other_run, 200, "rollback"))).count(), 0)

    def test_normal_collection_defaults_to_not_synthetic(self):
        with self.engine.begin() as c:
            c.execute(text("""INSERT INTO coletas
                (pesquisa_id,agente_id,company_id,client_uuid,data_inicio_coleta,
                 foi_offline,inconformidade_localizacao)
                VALUES (200,2,10,'normal-uuid','2026-08-24 11:00:00',0,0)"""))
            row = c.execute(text("""SELECT is_synthetic,seed_run_id,synthetic_source,
                                  synthetic_operator_id FROM coletas
                                  WHERE client_uuid='normal-uuid'""")).fetchone()
        self.assertEqual(row, (0, None, None, None))

    def test_batch_has_one_commit_audit_and_is_atomic(self):
        run_id = uuid4()
        with self.Session() as db, patch(
            "pesquisa360.services.synthetic_import.auditoria.registrar"
        ) as audit:
            agent = db.get(models.Usuario, 2)
            created = synthetic_import.create_synthetic_batch(
                db, operator=db.get(models.Usuario, 1), survey_id=200,
                seed_run_id=run_id, synthetic_source="demo",
                records=[
                    synthetic_import.SyntheticRecord(agent, "batch-1", self.payload(run_id, "batch-1")),
                    synthetic_import.SyntheticRecord(agent, "batch-2", self.payload(run_id, "batch-2")),
                ],
            )
            self.assertEqual(len(created), 2)
            self.assertEqual(audit.call_args.args[0],
                             synthetic_import.auditoria.SYNTHETIC_COLLECTION_BATCH_COMPLETED)
            self.assertEqual(audit.call_args.kwargs["details"]["quantidade"], 2)

        failed_run = uuid4()
        with self.Session() as db, patch(
            "pesquisa360.services.synthetic_import.auditoria.registrar"
        ):
            agent = db.get(models.Usuario, 2)
            invalid = self.payload(failed_run, "invalid")
            invalid.respostas.pop()
            with self.assertRaises(HTTPException):
                synthetic_import.create_synthetic_batch(
                    db, operator=db.get(models.Usuario, 1), survey_id=200,
                    seed_run_id=failed_run, synthetic_source="demo",
                    records=[
                        synthetic_import.SyntheticRecord(
                            agent, "valid", self.payload(failed_run, "valid")),
                        synthetic_import.SyntheticRecord(agent, "invalid", invalid),
                    ],
                )
            self.assertEqual(db.query(models.Coleta).filter(
                models.Coleta.seed_run_id == failed_run).count(), 0)


if __name__ == "__main__":
    unittest.main()
