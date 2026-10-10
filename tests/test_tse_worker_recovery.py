"""Regressoes de replay, diagnostico e health; apenas fixtures locais/QA."""

import copy
import json
from dataclasses import replace

import httpx
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from pesquisa360.db.models_tse import TseTotalizacao
from pesquisa360.services.tse.config import load_ingestion_settings
from pesquisa360.services.tse.worker import healthcheck
from tests.test_tse_persistencia import CANDIDATO, SETTINGS, URL_EA14, URL_EA20_UF
from tests.test_tse_worker import _WorkerFixture


def replay_states(tse):
    final = copy.deepcopy(tse.arquivos)
    partial = copy.deepcopy(final)
    line = next(a for a in partial[URL_EA14]["abr"] if a["cdabr"] == "ap")
    line["and"] = "p"
    line["s"]["st"] = "1000"
    doc = partial[URL_EA20_UF]
    doc.update(tf="n", ht="15:00:00", idg="111")
    doc["and"] = "p"
    doc["s"]["st"] = "1000"
    candidate = next(c for agr in doc["carg"][0]["agr"] for par in agr["par"]
                     for c in par["cand"] if c["sqcand"] == CANDIDATO)
    candidate["vap"] = "1500"
    third = copy.deepcopy(partial)
    next(a for a in third[URL_EA14]["abr"] if a["cdabr"] == "ap")["s"]["st"] = "1500"
    third[URL_EA20_UF]["s"]["st"] = "1500"
    third[URL_EA20_UF]["idg"] = "333"
    return partial, final, third


class WorkerRecoveryTest(_WorkerFixture):
    def fast_worker(self):
        return self.worker(tse_settings=replace(SETTINGS, requests_per_second=10, max_retries=0))

    def fail_uf(self, w, response):
        original = self.tse.__call__
        w.transport = httpx.MockTransport(
            lambda request: response(request) if str(request.url) == URL_EA20_UF
            else original(request))

    def test_historical_replay_is_ok_healthy_and_preserves_history(self):
        w = self.fast_worker()
        a, b, _ = replay_states(self.tse)
        for state in (a, b):
            self.tse.arquivos = state
            self.assertEqual(self.ciclo(w)["status"], "ok")
        before = self.contagens()
        self.tse.arquivos = a
        with self.assertLogs("pesquisa360.tse.repository", level="INFO") as logs:
            record = self.ciclo(w)
        self.assertEqual((record["status"], record["totalizacoes_new"]), ("ok", 0))
        self.assertTrue(healthcheck(w.settings.heartbeat_file)[0])
        self.assertEqual(self.contagens(), before)
        self.assertIn("snapshot_already_processed", "\n".join(logs.output))
        self.assertEqual(self.votos(self.abrangencia()), 3115)

    def test_structural_error_survives_heartbeat_and_transient_until_success(self):
        w = self.fast_worker()
        self.fail_uf(w, lambda _: httpx.Response(200, content=b"invalid-json"))
        self.assertEqual(self.ciclo(w)["status"], "error")
        beat = self.beat(w)
        self.assertEqual((beat["last_error_kind"], beat["consecutive_errors"]), ("structural", 1))
        self.assertFalse(healthcheck(w.settings.heartbeat_file)[0])
        w._sleep(0)
        self.assertEqual(self.beat(w)["status"], "running")
        self.assertFalse(healthcheck(w.settings.heartbeat_file)[0])
        self.fail_uf(w, lambda _: httpx.Response(503))
        self.assertEqual(self.ciclo(w)["status"], "error")
        self.assertFalse(healthcheck(w.settings.heartbeat_file)[0])
        w.transport = httpx.MockTransport(self.tse)
        self.assertEqual(self.ciclo(w)["status"], "ok")
        beat = self.beat(w)
        self.assertEqual(beat["consecutive_errors"], 0)
        self.assertIsNone(beat["last_error_kind"])
        self.assertGreaterEqual(beat["last_success_at"], beat["last_error_at"])
        self.assertTrue(healthcheck(w.settings.heartbeat_file)[0])

    def test_transient_threshold_and_recovery(self):
        w = self.fast_worker()
        self.fail_uf(w, lambda _: httpx.Response(503))
        for index in range(1, 4):
            self.assertEqual(self.ciclo(w)["status"], "error")
            self.assertEqual(self.beat(w)["consecutive_errors"], index)
            self.assertEqual(healthcheck(w.settings.heartbeat_file)[0], index < 3)
        w.transport = httpx.MockTransport(self.tse)
        self.assertEqual(self.ciclo(w)["status"], "ok")
        self.assertTrue(healthcheck(w.settings.heartbeat_file)[0])

    def test_other_5xx_is_transient_for_health(self):
        w = self.fast_worker()
        self.fail_uf(w, lambda _: httpx.Response(520))
        record = self.ciclo(w)
        self.assertEqual(record["status"], "error")
        self.assertEqual(record["errors"][0]["error_kind"], "transient")
        self.assertTrue(healthcheck(w.settings.heartbeat_file)[0])

    def test_exception_traceback_has_context_without_sql_payload(self):
        w = self.fast_worker()
        message = "unexpected constraint " + "details " * 80
        payload = "PAYLOAD_MUST_NOT_BE_LOGGED"

        def fail_insert(*_):
            raise IntegrityError("INSERT payload", {"payload_json": payload}, Exception(message))

        event.listen(TseTotalizacao, "before_insert", fail_insert)
        try:
            with self.assertLogs("pesquisa360.tse.worker", level="ERROR") as logs:
                record = self.ciclo(w)
        finally:
            event.remove(TseTotalizacao, "before_insert", fail_insert)
        output = "\n".join(logs.output)
        self.assertIn(message, output)
        self.assertIn("Traceback", output)
        self.assertIn("IntegrityError", output)
        self.assertNotIn(payload, output)
        context = record["errors"][0]
        for key in ("cycle_id", "origem", "pleito", "eleicao", "cargo", "uf",
                    "municipio", "zona", "url", "snapshot_id", "abrangencia_id",
                    "cargo_id", "phase", "exception_type", "error_kind"):
            self.assertIn(key, context)
        self.assertEqual((context["url"], context["phase"], context["eleicao"], context["cargo"]),
                         (URL_EA20_UF, "record_ea20", "21272", "0006"))
        for key in ("snapshot_id", "abrangencia_id", "cargo_id"):
            self.assertIsInstance(context[key], int)
        self.assertEqual(context["cycle_id"], record["cycle_id"])

    def test_cycle_times_are_distinct_from_heartbeat(self):
        w = self.fast_worker()
        record = self.ciclo(w)
        beat = self.beat(w)
        for key in ("last_heartbeat_at", "last_cycle_started_at", "last_cycle_finished_at",
                    "last_success_at", "last_error_at", "consecutive_errors"):
            self.assertIn(key, beat)
        self.assertLessEqual(beat["last_cycle_started_at"], beat["last_cycle_finished_at"])
        self.assertEqual(beat["last_cycle_finished_at"], beat["last_success_at"])
        w._sleep(0)
        after = self.beat(w)
        self.assertGreater(after["last_heartbeat_at"], beat["last_heartbeat_at"])
        self.assertEqual(after["last_cycle_finished_at"], beat["last_cycle_finished_at"])
        self.assertEqual(after["cycle_id"], record["cycle_id"])

    def test_restart_preserves_pending_structural_failure_until_success(self):
        first = self.fast_worker()
        self.fail_uf(first, lambda _: httpx.Response(200, content=b"invalid-json"))
        self.assertEqual(self.ciclo(first)["status"], "error")
        first.lock.release()
        restarted = self.fast_worker()
        restarted._heartbeat("running")
        self.assertFalse(healthcheck(restarted.settings.heartbeat_file)[0])
        self.assertEqual(self.beat(restarted)["consecutive_errors"], 1)
        self.assertEqual(self.ciclo(restarted)["status"], "ok")
        self.assertTrue(healthcheck(restarted.settings.heartbeat_file)[0])

    def test_error_threshold_is_configurable_and_positive(self):
        settings = load_ingestion_settings({"TSE_INGESTION_MAX_CONSECUTIVE_ERRORS": "2"})
        self.assertEqual(settings.max_consecutive_errors, 2)
        for value in ("0", "-1", "invalid"):
            with self.assertRaises(ValueError):
                load_ingestion_settings({"TSE_INGESTION_MAX_CONSECUTIVE_ERRORS": value})
