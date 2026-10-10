"""Replay EA20 e corrida pela UNIQUE; bancos de teste, nunca PROD."""

import copy
import hashlib
import json
import os
import threading
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path

import httpx

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-tse-replay")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.db.models import Base
from pesquisa360.db.models_tse import (
    TseAbrangencia, TseCargo, TseEleicao, TseResultadoCandidato, TseSnapshot,
    TseTotalizacao,
)
from pesquisa360.services.tse.client import TseResponse
from pesquisa360.services.tse.ea20 import parse_ea20
from pesquisa360.services.tse.repository import TseRepository
from tests.test_tse_persistencia import (
    CANDIDATO, URL_EA20_UF, fixture, FakeTse, SETTINGS, arquivos_simulados,
)
from pesquisa360.services.tse.config import TseIngestionSettings
from pesquisa360.services.tse.worker import TseIngestorWorker, healthcheck

PG_URL = os.environ.get("P360_TSE_PG_URL")


class ReplayRepositoryTest(unittest.TestCase):
    def make_engine(self):
        engine = create_engine("sqlite:///:memory:")

        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")

        return engine

    def setUp(self):
        self.engine = self.make_engine()
        self.addCleanup(self.engine.dispose)
        tables = [t for t in Base.metadata.sorted_tables if t.name.startswith("tse_")]
        Base.metadata.create_all(self.engine, tables=tables)
        self.factory = sessionmaker(bind=self.engine)
        self.session = self.factory()
        self.addCleanup(self.session.close)
        self.repo = TseRepository(self.session)
        self.election = TseEleicao(id=2, origem="SIMULADO", ambiente="simulado2026",
                                  ciclo="ele2026", pleito="17801", codigo_eleicao="21272",
                                  nome="Replay QA", turno=1)
        self.cargo = TseCargo(id=5, codigo="0007", nome="Deputado Estadual")
        self.session.add_all([self.election, self.cargo])
        self.session.flush()
        self.scope = TseAbrangencia(id=20, eleicao_id=2, tipo="UF", chave="uf:ap", uf="ap")
        self.session.add(self.scope)
        self.session.commit()

    def snapshot(self, snapshot_id, votes, date="04/10/2026"):
        doc = copy.deepcopy(fixture("SIMULADO_ea20_ap_c0006.json"))
        doc["carg"][0]["cd"] = "7"
        doc["carg"][0]["nmn"] = "Deputado Estadual"
        doc.update(idg=str(snapshot_id), dg=date, hg="18:00:00", dt=date, ht="18:00:00")
        for group in doc["carg"][0]["agr"]:
            for party in group["par"]:
                for candidate in party.get("cand", []):
                    if candidate["sqcand"] == CANDIDATO:
                        candidate["vap"] = str(votes)
        body = json.dumps(doc, ensure_ascii=False).encode()
        row = TseSnapshot(id=snapshot_id, origem="SIMULADO", tipo="EA20", url=URL_EA20_UF,
                          http_status=200, sha256=hashlib.sha256(body).hexdigest(),
                          tamanho_bytes=len(body), payload_json=doc)
        self.session.add(row)
        self.session.flush()
        return row, parse_ea20(doc)

    def record(self, snapshot, result, scope=None):
        return self.repo.record_ea20(self.election, scope or self.scope, snapshot, result, "ap")

    def count(self, model=TseTotalizacao):
        return self.session.scalar(select(func.count()).select_from(model))

    def test_t1_first_snapshot_inserts(self):
        a, result = self.snapshot(174, 3115)
        total, created = self.record(a, result)
        self.assertTrue(created)
        self.assertEqual((total.cargo_id, total.abrangencia_id, total.snapshot_id), (5, 20, 174))
        self.assertEqual(self.count(), 1)

    def test_t2_immediate_replay_is_noop(self):
        a, result = self.snapshot(174, 3115)
        original, _ = self.record(a, result)
        total, created = self.record(a, result)
        self.assertFalse(created)
        self.assertEqual(total.id, original.id)
        self.assertEqual(self.count(), 1)

    def test_t3_a_b_a_preserves_history_and_latest(self):
        a, ar = self.snapshot(174, 3115)
        original, _ = self.record(a, ar)
        b, br = self.snapshot(229, 3215)
        latest, _ = self.record(b, br)
        before = self.count(TseResultadoCandidato)
        self.session.commit()
        total, created = self.record(a, ar)
        self.assertFalse(created)
        self.assertEqual(total.id, original.id)
        self.assertEqual(self.count(), 2)
        self.assertEqual(self.count(TseSnapshot), 2)
        self.assertEqual(self.count(TseResultadoCandidato), before)
        self.assertEqual(self.repo.latest_totalizacao(20, 5).id, latest.id)
        self.assertEqual((original.snapshot_id, latest.snapshot_id), (174, 229))

    def test_t4_a_b_c_b_does_not_duplicate(self):
        rows = []
        for sid, votes in [(174, 3115), (229, 3215), (250, 3315)]:
            snapshot, result = self.snapshot(sid, votes)
            total, _ = self.record(snapshot, result)
            rows.append((snapshot, result, total.id))
        total, created = self.record(*rows[1][:2])
        self.assertFalse(created)
        self.assertEqual(total.id, rows[1][2])
        self.assertEqual(self.count(), 3)
        self.assertEqual(self.repo.latest_totalizacao(20, 5).id, rows[2][2])

    def test_t5_same_snapshot_other_cargo_uses_distinct_key(self):
        a, ar = self.snapshot(174, 3115)
        self.record(a, ar)
        other = replace(ar, cargo="0006", cargo_nome="Deputado Federal",
                        candidatos=tuple(replace(c, sqcand=c.sqcand + "6") for c in ar.candidatos))
        total, created = self.record(a, other)
        self.assertTrue(created)
        self.assertNotEqual(total.cargo_id, 5)
        self.assertEqual(self.count(), 2)

    def test_t6_same_snapshot_other_scope_uses_distinct_key(self):
        a, ar = self.snapshot(174, 3115)
        self.record(a, ar)
        other = TseAbrangencia(id=21, eleicao_id=2, tipo="UF", chave="uf:pa", uf="pa")
        self.session.add(other)
        self.session.flush()
        total, created = self.record(a, replace(ar, codigo_abrangencia="pa"), other)
        self.assertTrue(created)
        self.assertEqual(total.abrangencia_id, 21)
        self.assertEqual(self.count(), 2)

    def test_t7_new_snapshot_with_older_time_is_preserved(self):
        a, ar = self.snapshot(174, 3115)
        first, _ = self.record(a, ar)
        b, br = self.snapshot(229, 3215, date="03/10/2026")
        total, created = self.record(b, br)
        self.assertTrue(created)
        self.assertLess(total.gerado_em, first.gerado_em)
        self.assertEqual(self.count(), 2)

    def test_snapshot_replay_keeps_url_hash_identity(self):
        a, ar = self.snapshot(174, 3115)
        self.record(a, ar)
        body = json.dumps(a.payload_json, ensure_ascii=False).encode()
        snapshot, created = self.repo.save_snapshot(
            origem="SIMULADO", tipo="EA20",
            response=TseResponse(url=a.url, status=200, content=body), payload=a.payload_json)
        self.assertFalse(created)
        self.assertEqual(snapshot.id, 174)
        self.assertEqual(self.count(TseSnapshot), 1)

    def test_unrelated_integrity_error_is_not_hidden(self):
        a, ar = self.snapshot(174, 3115)
        self.record(a, ar)
        b, br = self.snapshot(229, 3215)
        with self.assertRaises(IntegrityError):
            self.record(b, br, SimpleNamespace(id=99999, tipo="UF"))
        self.assertTrue(self.session.is_active)
        self.assertEqual(self.count(), 1)


@unittest.skipUnless(PG_URL, "P360_TSE_PG_URL exige PostgreSQL de QA descartavel")
class ReplayPostgresTest(ReplayRepositoryTest):
    def setUp(self):
        super().setUp()
        # IDs do incidente sao explicitos; sincronizar sequencias somente neste schema QA.
        for model in (TseEleicao, TseCargo, TseAbrangencia):
            self.session.execute(select(func.setval(func.pg_get_serial_sequence(
                f"{self.qa_schema}.{model.__tablename__}", "id"), func.max(model.id), True)))
        self.session.commit()

    def make_engine(self):
        schema = "tse_replay_" + uuid.uuid4().hex
        self.qa_schema = schema
        admin = create_engine(PG_URL)
        with admin.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')

        def cleanup():
            with admin.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
            admin.dispose()

        self.addCleanup(cleanup)
        return create_engine(PG_URL, execution_options={"schema_translate_map": {None: schema}})

    def test_real_pg_worker_a_b_a_and_a_b_c_a_b(self):
        from tests.test_tse_worker_recovery import replay_states

        fake = FakeTse(arquivos_simulados())
        states = replay_states(fake)
        with tempfile.TemporaryDirectory(prefix="tse-pg-replay-") as directory:
            settings = TseIngestionSettings(
                enabled=True, origem="SIMULADO", ufs=("ap",), cargos=("0006",),
                municipios=(), zonas_de=(), heartbeat_file=Path(directory) / "beat.json")
            worker = TseIngestorWorker(
                settings, replace(SETTINGS, requests_per_second=10), self.engine, self.factory,
                transport=httpx.MockTransport(fake))
            try:
                self.assertTrue(worker.lock.try_acquire())
                for index, expected in zip((0, 1, 0, 2, 0, 1), (1, 2, 2, 3, 3, 3)):
                    before = list(self.session.execute(select(
                        TseTotalizacao.id, TseTotalizacao.snapshot_id, TseTotalizacao.conteudo_hash
                    ).order_by(TseTotalizacao.id)))
                    fake.arquivos = states[index]
                    self.assertEqual(worker.run_cycle()["status"], "ok")
                    self.assertTrue(healthcheck(settings.heartbeat_file)[0])
                    self.assertEqual(self.count(), expected)
                    after = list(self.session.execute(select(
                        TseTotalizacao.id, TseTotalizacao.snapshot_id, TseTotalizacao.conteudo_hash
                    ).order_by(TseTotalizacao.id)))
                    self.assertEqual(after[:len(before)], before)
                self.assertEqual(self.count(), 3)
                self.assertEqual(len(set(self.session.scalars(select(
                    TseTotalizacao.snapshot_id)))), 3)
            finally:
                worker.lock.release()

    def test_t8_concurrent_unique_conflict_preserves_outer_transaction(self):
        a, ar = self.snapshot(174, 3115)
        self.record(a, ar)
        b, br = self.snapshot(229, 3215)
        self.session.commit()
        barrier = threading.Barrier(2)

        class RacingRepository(TseRepository):
            def latest_totalizacao(inner, *args):
                row = super().latest_totalizacao(*args)
                barrier.wait(timeout=15)
                return row

        def contender(index):
            with self.factory() as session:
                session.add(TseCargo(codigo=f"099{index}", nome="Outer transaction sentinel"))
                session.flush()
                total, created = RacingRepository(session).record_ea20(
                    session.get(TseEleicao, 2), session.get(TseAbrangencia, 20),
                    session.get(TseSnapshot, 229), br, "ap")
                total_id = total.id
                session.commit()
                return total_id, created

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(contender, i) for i in (1, 2)]
            outcomes = [future.result(timeout=30) for future in futures]
        self.session.expire_all()
        self.assertEqual(sorted(created for _, created in outcomes), [False, True])
        self.assertEqual(len({row_id for row_id, _ in outcomes}), 1)
        self.assertEqual(self.count(), 2)
        self.assertEqual(self.count(TseCargo), 3)

    def test_postgres_failing_row_detail_does_not_leak_payload(self):
        from pesquisa360.services.tse.worker import _erro, _safe_traceback

        sentinel = "DATABASE_PAYLOAD_MUST_NOT_BE_LOGGED"
        try:
            with self.session.begin_nested():
                self.session.add(TseSnapshot(
                    origem=None, tipo="EA20", url=URL_EA20_UF, http_status=200,
                    sha256="a" * 64, tamanho_bytes=100, payload_json={"private": sentinel}))
                self.session.flush()
        except IntegrityError as exc:
            self.assertIn(sentinel, str(exc.orig))  # DETAIL real do driver PostgreSQL.
            self.assertNotIn(sentinel, _erro(exc))
            self.assertNotIn(sentinel, _safe_traceback(exc))
            self.assertIn("null value", _erro(exc))
        else:
            self.fail("Expected QA NOT NULL violation")
        self.assertTrue(self.session.is_active)
