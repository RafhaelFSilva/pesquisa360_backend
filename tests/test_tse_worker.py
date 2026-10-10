"""Ingestor automatico do TSE: configuracao, descoberta do pleito, ciclo,
singleton (advisory lock), concorrencia com a CLI, rollback, shutdown e health.

Sem rede: o TSE e o `FakeTse` de test_tse_persistencia. O advisory lock real
do PostgreSQL e exercitado quando `P360_TSE_PG_URL` aponta para um banco de QA
descartavel; sem a variavel, esses testes sao pulados.
"""

import importlib.util
import io
import json
import os
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-tse-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.services.tse import discovery, worker as worker_module
from pesquisa360.services.tse.config import (
    SIMULADO_AMBIENTE, SIMULADO_BASE_URL, TseIngestionSettings, TseSettings,
    load_ingestion_settings, settings_for_origem,
)
from pesquisa360.services.tse.locking import LOCK_NAMESPACE, ORIGEM_KEYS, IngestionLock
from pesquisa360.services.tse.normalization import OFICIAL, SIMULADO
from pesquisa360.services.tse.worker import TseIngestorWorker, healthcheck, health_max_age

from tests.test_tse_persistencia import (
    CANDIDATO, DADOS, FIXTURES, SETTINGS, URL_EA14, URL_EA20_UF, _TseFixture, fixture,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PG_URL = os.environ.get("P360_TSE_PG_URL")
URL_ZONA = f"{DADOS}/ap/ap06050-z0014-c0006-e021272-u.json"


# ------------------------------------------------------------------ config
class ConfiguracaoDoIngestorTest(unittest.TestCase):
    def test_desligado_por_padrao(self):
        settings = load_ingestion_settings({})
        self.assertFalse(settings.enabled)
        self.assertEqual((settings.origem, settings.ufs, settings.interval_seconds),
                         (OFICIAL, (), 20))
        self.assertEqual(settings.cargos, ("0001", "0003", "0005", "0006", "0007"))
        self.assertIsNone(settings.pleito)      # descoberto no EA11, nao fixado

    def test_habilitado_por_ambiente(self):
        settings = load_ingestion_settings({
            "TSE_INGESTION_ENABLED": "true", "TSE_INGESTION_ORIGIN": "simulado",
            "TSE_INGESTION_UFS": "AP, pa", "TSE_INGESTION_CARGOS": "6,0003",
            "TSE_INGESTION_INTERVAL_SECONDS": "30", "TSE_INGESTION_MUNICIPIOS": "todos",
            "TSE_INGESTION_ZONAS_DE": "6050", "TSE_INGESTION_HEARTBEAT_FILE": "/tmp/x.beat",
        })
        self.assertTrue(settings.enabled)
        self.assertEqual((settings.origem, settings.ufs, settings.cargos),
                         (SIMULADO, ("ap", "pa"), ("0006", "0003")))
        self.assertEqual((settings.municipios, settings.zonas_de, settings.interval_seconds),
                         (("*",), ("06050",), 30.0))
        self.assertEqual(settings.heartbeat_file, Path("/tmp/x.beat"))

    def test_valores_invalidos_sao_recusados(self):
        base = {"TSE_INGESTION_ENABLED": "1", "TSE_INGESTION_UFS": "ap"}
        invalidos = {
            "origem": {"TSE_INGESTION_ORIGIN": "TESTE"},
            "uf": {"TSE_INGESTION_UFS": "ap,xx"},
            "cargo": {"TSE_INGESTION_CARGOS": "0006,deputado"},
            "cargo longo": {"TSE_INGESTION_CARGOS": "00006"},
            "intervalo curto": {"TSE_INGESTION_INTERVAL_SECONDS": "5"},
            "intervalo longo": {"TSE_INGESTION_INTERVAL_SECONDS": "99999"},
            "municipio": {"TSE_INGESTION_ZONAS_DE": "macapa"},
            "pleito": {"TSE_INGESTION_PLEITO": "abc"},
            "flag": {"TSE_INGESTION_ENABLED": "talvez"},
            "habilitado sem uf": {"TSE_INGESTION_UFS": ""},
        }
        for caso, extra in invalidos.items():
            with self.assertRaises(ValueError, msg=caso):
                load_ingestion_settings({**base, **extra})
        # Desligado nao exige UF: o processo sobe ocioso.
        self.assertFalse(load_ingestion_settings({"TSE_INGESTION_UFS": ""}).enabled)

    def test_intervalo_dentro_da_faixa_segura(self):
        for segundos in (15, 20, 30):
            self.assertEqual(TseIngestionSettings(interval_seconds=segundos).interval_seconds,
                             segundos)

    def test_origem_define_o_ambiente_do_tse(self):
        oficial = settings_for_origem(OFICIAL, TseSettings())
        simulado = settings_for_origem(SIMULADO, TseSettings())
        self.assertEqual(oficial.base_url, "https://resultados.tse.jus.br")
        self.assertEqual((simulado.base_url, simulado.ambiente),
                         (SIMULADO_BASE_URL, SIMULADO_AMBIENTE))
        self.assertEqual(simulado.requests_per_second, oficial.requests_per_second)
        with self.assertRaises(ValueError):
            settings_for_origem("TESTE", TseSettings())


class DescobertaDoPleitoTest(unittest.TestCase):
    """O codigo do pleito vem do EA11, nao de constante (3220 so aparece em fixture)."""

    def test_pleito_geral_pelos_cargos(self):
        ea11 = fixture("ea11.json")        # pleitos 452 (2024) e 3220 (2026)
        hoje = date(2026, 10, 4)
        self.assertEqual(discovery.discover_pleito(ea11, ["0006"], hoje), "3220")
        self.assertEqual(discovery.discover_pleito(
            ea11, ["0001", "0003", "0005", "0006", "0007"], hoje), "3220")
        self.assertEqual(discovery.discover_pleito(ea11, ["0011"], hoje), "452")
        self.assertEqual(discovery.discover_pleito(fixture("SIMULADO_ea11.json"), ["6"], hoje),
                         "17801")

    def test_pleito_futuro_ou_inexistente_nao_e_escolhido(self):
        ea11 = fixture("ea11.json")
        with self.assertRaises(LookupError):       # 3220 ainda nao aconteceu
            discovery.discover_pleito(ea11, ["0006"], date(2026, 10, 3))
        with self.assertRaises(LookupError):
            discovery.discover_pleito(ea11, ["0099"], date(2026, 10, 4))

    def test_segundo_turno_nao_substitui_o_pleito_geral(self):
        ea11 = fixture("ea11.json")
        geral = next(p for p in ea11["pl"] if p["cd"] == "3220")
        segundo = json.loads(json.dumps(geral))
        segundo.update({"cd": "3221", "dt": "25/10/2026"})
        segundo["e"] = [e for e in segundo["e"] if e["cd"] == "6257"]     # so Presidente
        ea11["pl"].append(segundo)
        hoje = date(2026, 10, 26)
        self.assertEqual(discovery.discover_pleito(ea11, ["0001", "0006"], hoje), "3220")
        self.assertEqual(discovery.discover_pleito(ea11, ["0001"], hoje), "3221")

    def test_pleitos_empatados_exigem_escolha_explicita(self):
        ea11 = fixture("ea11.json")
        ea11["pl"].append({**next(p for p in ea11["pl"] if p["cd"] == "3220"), "cd": "9999"})
        with self.assertRaises(LookupError):
            discovery.discover_pleito(ea11, ["0006"], date(2026, 10, 4))


# ------------------------------------------------------------------ worker
class _WorkerFixture(_TseFixture):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="p360-tse-worker-"))
        self.addCleanup(lambda: [p.unlink() for p in self.tmp.glob("*")] and None)
        self.factory = sessionmaker(bind=self.engine)

    def settings(self, **extra) -> TseIngestionSettings:
        return TseIngestionSettings(**{
            "enabled": True, "origem": SIMULADO, "ufs": ("ap",), "cargos": ("0006",),
            "municipios": ("06050",), "zonas_de": ("06050",), "interval_seconds": 15,
            "heartbeat_file": self.tmp / "beat.json", **extra})

    def worker(self, tse_settings=SETTINGS, **extra) -> TseIngestorWorker:
        w = TseIngestorWorker(self.settings(**extra), tse_settings, self.engine, self.factory,
                              transport=httpx.MockTransport(self.tse))
        self.addCleanup(w.lock.release)
        return w

    def beat(self, w) -> dict:
        return json.loads(w.settings.heartbeat_file.read_text(encoding="utf-8"))

    def ciclo(self, w) -> dict:
        self.tse.chamadas.clear()
        self.assertTrue(w.lock.try_acquire())
        return w.run_cycle()


class CicloDoWorkerTest(_WorkerFixture):
    def test_tres_ciclos_ingestao_304_e_zero_duplicacoes(self):
        w = self.worker()
        primeiro = self.ciclo(w)
        self.assertEqual((primeiro["status"], primeiro["pleito"], primeiro["origem"]),
                         ("ok", "17801", SIMULADO))
        self.assertEqual((primeiro["totalizacoes_new"], primeiro["snapshots_new"],
                          primeiro["ea20"], primeiro["200"], primeiro["304"]),
                         (5, 10, 5, 10, 0))
        self.assertEqual(primeiro["ea14_changed"], ["uf:ap"])
        # Primeira passada: sem estado anterior, os 16 municipios do EA15 "mudaram";
        # so o do escopo (Macapa) tem EA20 consultado. O log leva a contagem.
        self.assertEqual(primeiro["ea15_changed"], 16)
        self.assertEqual(primeiro["requests"], 10)
        self.assertGreaterEqual(primeiro["duration_ms"], 0)
        antes = self.contagens()

        segundo = self.ciclo(w)
        self.assertEqual((segundo["status"], segundo["totalizacoes_new"], segundo["snapshots_new"],
                          segundo["ea20"], segundo["200"]), ("ok", 0, 0, 0, 0))
        self.assertEqual(segundo["304"], segundo["requests"])
        self.assertEqual((segundo["ea14_changed"], segundo["ea15_changed"]), ([], 0))
        self.assertFalse([u for u, _s in self.tse.chamadas if u.endswith("-u.json")])

        terceiro = self.ciclo(w)
        self.assertEqual(terceiro["totalizacoes_new"], 0)
        self.assertEqual(self.contagens(), antes)

    def test_log_do_ciclo_nao_carrega_payload(self):
        w = self.worker()
        with self.assertLogs("pesquisa360.tse.worker", level="INFO") as logs:
            self.ciclo(w)
        linha = next(l for l in logs.output if "tse_ingest_cycle " in l)
        registro = json.loads(linha.split("tse_ingest_cycle ", 1)[1])
        for campo in ("timestamp", "origem", "ufs", "cargos", "ea14_changed", "ea15_changed",
                      "requests", "200", "304", "404", "429", "ea20", "snapshots_new",
                      "totalizacoes_new", "duration_ms", "status"):
            self.assertIn(campo, registro)
        self.assertLess(len(linha), 1500)
        self.assertNotIn("CANDIDATO 9741", linha)

    def test_pleito_explicito_dispensa_descoberta(self):
        registro = self.ciclo(self.worker(pleito="17801"))
        self.assertEqual((registro["status"], registro["pleito"]), ("ok", "17801"))

    def test_404_e_registrado_sem_retry_e_o_ciclo_segue(self):
        del self.tse.arquivos[URL_ZONA]
        registro = self.ciclo(self.worker())
        self.assertEqual((registro["status"], registro["404"], registro["totalizacoes_new"]),
                         ("ok", 1, 4))
        self.assertEqual([s for u, s in self.tse.chamadas if u == URL_ZONA], [404])

    def test_429_respeita_retry_after_e_conclui(self):
        estado = {"feito": False}
        original = self.tse.__call__

        def handler(request):
            if str(request.url) == URL_EA20_UF and not estado["feito"]:
                estado["feito"] = True
                self.tse.chamadas.append((URL_EA20_UF, 429))
                return httpx.Response(429, headers={"Retry-After": "0"})
            return original(request)

        w = TseIngestorWorker(self.settings(), SETTINGS, self.engine, self.factory,
                              transport=httpx.MockTransport(handler))
        self.addCleanup(w.lock.release)
        registro = self.ciclo(w)
        self.assertEqual((registro["status"], registro["429"], registro["totalizacoes_new"]),
                         ("ok", 1, 5))

    def _ciclo_com_falha(self, falha):
        """Ingere, muda o EA14 (forca nova consulta) e faz o EA20 UF falhar."""
        w = self.worker(tse_settings=TseSettings(
            base_url=SETTINGS.base_url, ambiente=SETTINGS.ambiente, requests_per_second=10,
            max_retries=0))
        self.assertEqual(self.ciclo(w)["status"], "ok")
        antes = self.contagens()
        ap = next(a for a in self.tse.arquivos[URL_EA14]["abr"] if a["cdabr"] == "ap")
        ap["e"]["c"] = str(int(ap["e"]["c"]) + 1)
        original = self.tse.__call__

        def handler(request):
            if str(request.url) == URL_EA20_UF:
                return falha(request)
            return original(request)

        w.transport = httpx.MockTransport(handler)
        registro = self.ciclo(w)
        return w, registro, antes

    def test_5xx_faz_rollback_e_preserva_o_ultimo_estado(self):
        w, registro, antes = self._ciclo_com_falha(lambda _r: httpx.Response(503))
        self.assertEqual(registro["status"], "error")
        self.assertIn("TseTransientError", registro["errors"][0]["erro"])
        # Rollback: nem o snapshot novo do EA14 ficou gravado sem a totalizacao.
        self.assertEqual(self.contagens(), antes)
        self.assertEqual(self.votos(self.abrangencia()), 3115)
        # O worker segue vivo: com o TSE de volta, o ciclo seguinte recupera.
        w.transport = httpx.MockTransport(self.tse)
        self.assertEqual(self.ciclo(w)["status"], "ok")
        self.assertEqual(self.contagens()["tse_snapshots"], antes["tse_snapshots"] + 1)

    def test_timeout_e_falha_transitoria_com_rollback(self):
        def timeout(request):
            raise httpx.ReadTimeout("tempo esgotado", request=request)

        _w, registro, antes = self._ciclo_com_falha(timeout)
        self.assertEqual(registro["status"], "error")
        self.assertEqual(self.contagens(), antes)

    def test_erro_de_parse_preserva_o_ultimo_estado_valido(self):
        _w, registro, antes = self._ciclo_com_falha(
            lambda _r: httpx.Response(200, content=b"<html>fora do ar</html>"))
        self.assertEqual(registro["status"], "error")
        self.assertIn("JSONDecodeError", registro["errors"][0]["erro"])
        self.assertEqual(self.contagens(), antes)
        self.assertEqual(self.votos(self.abrangencia()), 3115)

    def test_escopo_oficial_contra_arquivo_simulado_e_erro_sem_gravar(self):
        registro = self.ciclo(self.worker(origem=OFICIAL))
        self.assertEqual(registro["status"], "error")
        self.assertIn("TseOrigemError", registro["errors"][0]["erro"])
        self.assertEqual(sum(self.contagens().values()), 0)


class LoopDoWorkerTest(_WorkerFixture):
    def test_desligado_fica_ocioso_e_saudavel(self):
        w = self.worker(enabled=False)
        w.run(max_iterations=1)
        self.assertEqual(self.tse.chamadas, [])
        self.assertEqual(sum(self.contagens().values()), 0)

    def test_run_executa_ciclo_e_solta_a_trava_ao_sair(self):
        w = self.worker()
        w.run(max_iterations=1)
        self.assertEqual(self.contar_totalizacoes(), 5)
        self.assertFalse(w.lock.held())
        self.assertEqual(self.beat(w)["status"], "stopped")
        outro = IngestionLock(self.engine, SIMULADO)
        self.addCleanup(outro.release)
        self.assertTrue(outro.try_acquire())

    def contar_totalizacoes(self):
        return self.contagens()["tse_totalizacoes"]

    def test_parada_pedida_antes_do_inicio_nao_executa_ciclo(self):
        w = self.worker()
        w.stop_event.set()
        w.run()
        self.assertEqual(self.tse.chamadas, [])
        self.assertEqual(self.beat(w)["status"], "stopped")

    def test_parada_no_meio_da_ingestao_faz_rollback(self):
        w = self.worker()
        original = self.tse.__call__

        def handler(request):
            resposta = original(request)
            if len(self.tse.chamadas) == 4:      # no meio do ciclo
                w.stop_event.set()
            return resposta

        w.transport = httpx.MockTransport(handler)
        inicio = time.monotonic()
        w.run()                                   # sem max_iterations: sai pela parada
        self.assertLess(time.monotonic() - inicio, 5)
        self.assertLess(len(self.tse.chamadas), 10)
        self.assertEqual(sum(self.contagens().values()), 0)       # rollback total
        self.assertFalse(w.lock.held())

    def test_parada_interrompe_a_espera_entre_ciclos(self):
        w = self.worker()
        ciclo_original = w.run_cycle

        def ciclo_e_agenda_parada():
            registro = ciclo_original()
            threading.Timer(0.2, w.stop_event.set).start()    # ja na espera entre ciclos
            return registro

        w.run_cycle = ciclo_e_agenda_parada
        inicio = time.monotonic()
        w.run()                                   # intervalo de 15 s; sai logo apos o ciclo
        self.assertLess(time.monotonic() - inicio, 8)
        self.assertEqual(self.contar_totalizacoes(), 5)
        self.assertEqual(self.beat(w)["status"], "stopped")

    def test_sinal_de_parada_e_registrado_pelo_main(self):
        self.assertTrue(hasattr(worker_module.signal, "SIGTERM"))
        codigo = Path(worker_module.__file__).read_text(encoding="utf-8")
        self.assertIn("signal.signal(signal.SIGTERM", codigo)
        self.assertIn("signal.signal(signal.SIGINT", codigo)


class SingletonTest(_WorkerFixture):
    def test_advisory_lock_adquirido_e_ocupado(self):
        a, b = IngestionLock(self.engine, SIMULADO), IngestionLock(self.engine, SIMULADO)
        self.addCleanup(a.release)
        self.addCleanup(b.release)
        self.assertTrue(a.try_acquire())
        self.assertTrue(a.try_acquire())          # reentrante para o mesmo dono
        self.assertTrue(a.held())
        self.assertFalse(b.try_acquire())
        self.assertFalse(b.held())
        a.release()
        self.assertTrue(b.try_acquire())

    def test_trava_e_por_origem(self):
        simulado, oficial = IngestionLock(self.engine, SIMULADO), IngestionLock(self.engine, OFICIAL)
        self.addCleanup(simulado.release)
        self.addCleanup(oficial.release)
        self.assertTrue(simulado.try_acquire())
        self.assertTrue(oficial.try_acquire())
        self.assertEqual((LOCK_NAMESPACE, ORIGEM_KEYS), (0x545345, {"OFICIAL": 1, "SIMULADO": 2}))
        with self.assertRaises(ValueError):
            IngestionLock(self.engine, "TESTE")

    def test_segundo_worker_fica_em_espera_e_nao_ingere(self):
        primeiro, segundo = self.worker(), self.worker(heartbeat_file=self.tmp / "b.json")
        primeiro._iteration()
        antes = self.contagens()
        self.tse.chamadas.clear()
        with self.assertLogs("pesquisa360.tse.worker", level="WARNING") as logs:
            segundo._iteration()
        self.assertEqual(self.tse.chamadas, [])               # nenhuma requisicao ao TSE
        self.assertEqual(self.contagens(), antes)
        self.assertEqual(self.beat(segundo)["status"], "standby")
        self.assertTrue(any("tse_ingestor_standby" in l for l in logs.output))
        # O primeiro sai: o segundo assume no ciclo seguinte, sem duplicar nada.
        primeiro.lock.release()
        segundo._iteration()
        self.assertTrue(segundo.lock.held())
        self.assertEqual(self.beat(segundo)["status"], "ok")
        self.assertEqual(self.contagens(), antes)

    def test_cli_recusa_ingestao_com_o_worker_ativo(self):
        spec = importlib.util.spec_from_file_location(
            "tse_apuracao_cli", PROJECT_ROOT / "scripts" / "tse_apuracao.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        cli.engine, cli.SessionLocal = self.engine, self.factory
        cli.settings_for_origem = lambda _origem: SETTINGS
        original_client = cli.TseClient
        cli.TseClient = lambda settings: original_client(
            settings=settings, transport=httpx.MockTransport(self.tse), sleep=lambda _s: None)
        args = SimpleNamespace(simulado=True, pleito=None, uf="ap", cargo=["0006"],
                               municipios=["06050"], zonas_de=["06050"], secoes_ea18=0,
                               force=False)

        w = self.worker()
        w._iteration()                            # worker ativo, segurando a trava
        antes = self.contagens()
        self.tse.chamadas.clear()
        erro = io.StringIO()
        with redirect_stderr(erro), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_ingest(args), cli.EXIT_LOCK_OCUPADO)
        self.assertIn("em andamento por outro processo", erro.getvalue())
        self.assertEqual(self.tse.chamadas, [])
        self.assertEqual(self.contagens(), antes)

        # Worker parado: a CLI volta a funcionar como fallback e nao duplica.
        w.lock.release()
        saida = io.StringIO()
        with redirect_stdout(saida):
            self.assertEqual(cli.cmd_ingest(args), 0)
        self.assertEqual(json.loads(saida.getvalue())["totalizacoes_novas"], 0)
        self.assertEqual(self.contagens(), antes)
        # E a CLI solta a trava ao terminar.
        self.assertTrue(w.lock.try_acquire())


class HealthcheckTest(_WorkerFixture):
    def test_heartbeat_recente_e_saudavel(self):
        w = self.worker()
        w._iteration()
        ok, detalhe = healthcheck(w.settings.heartbeat_file)
        self.assertTrue(ok, detalhe)
        self.assertIn("ok", detalhe)

    def test_worker_travado_encerrado_ou_sem_heartbeat_nao_e_saudavel(self):
        w = self.worker()
        w._iteration()
        arquivo = w.settings.heartbeat_file
        limite = health_max_age(w.settings.interval_seconds)
        self.assertEqual(limite, 120)
        self.assertEqual(health_max_age(60), 240)
        self.assertTrue(healthcheck(arquivo, now=time.time() + limite - 5)[0])
        ok, detalhe = healthcheck(arquivo, now=time.time() + limite + 5)       # travado
        self.assertFalse(ok)
        self.assertIn("heartbeat", detalhe)
        w.run(max_iterations=1)                                                # encerrado
        self.assertEqual(healthcheck(arquivo), (False, "worker encerrado"))
        self.assertEqual(healthcheck(self.tmp / "inexistente"), (False, "sem heartbeat"))
        arquivo.write_text("lixo", encoding="utf-8")
        self.assertFalse(healthcheck(arquivo)[0])

    def test_estados_ociosos_tambem_batem(self):
        desligado = self.worker(enabled=False)
        desligado._iteration()
        self.assertEqual(self.beat(desligado)["status"], "disabled")
        self.assertTrue(healthcheck(desligado.settings.heartbeat_file)[0])

    def test_healthcheck_pela_linha_de_comando(self):
        w = self.worker()
        w._iteration()
        ambiente = {"TSE_INGESTION_HEARTBEAT_FILE": str(w.settings.heartbeat_file)}
        with unittest.mock.patch.dict(os.environ, ambiente), redirect_stdout(io.StringIO()):
            self.assertEqual(worker_module.main(["--healthcheck"]), 0)
        with unittest.mock.patch.dict(
                os.environ, {"TSE_INGESTION_HEARTBEAT_FILE": str(self.tmp / "nao.existe")}), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(worker_module.main(["--healthcheck"]), 1)


@unittest.skipUnless(PG_URL, "defina P360_TSE_PG_URL (PostgreSQL de QA descartavel)")
class AdvisoryLockPostgresTest(unittest.TestCase):
    """O lock de verdade: sessoes distintas do PostgreSQL."""

    def setUp(self):
        self.engines = [create_engine(PG_URL), create_engine(PG_URL)]
        for engine in self.engines:
            self.addCleanup(engine.dispose)

    def test_segundo_processo_nao_adquire_e_assume_quando_o_primeiro_sai(self):
        a = IngestionLock(self.engines[0], SIMULADO)
        b = IngestionLock(self.engines[1], SIMULADO)
        self.addCleanup(a.release)
        self.addCleanup(b.release)
        self.assertTrue(a.try_acquire())
        self.assertFalse(b.try_acquire())
        outra_origem = IngestionLock(self.engines[1], OFICIAL)
        self.addCleanup(outra_origem.release)
        self.assertTrue(outra_origem.try_acquire())
        a.release()
        self.assertFalse(a.held())
        self.assertTrue(b.try_acquire())

    def test_conexao_perdida_solta_a_trava(self):
        a = IngestionLock(self.engines[0], SIMULADO)
        b = IngestionLock(self.engines[1], SIMULADO)
        self.addCleanup(a.release)
        self.addCleanup(b.release)
        self.assertTrue(a.try_acquire())
        a._conn.invalidate()                  # simula queda da conexao/processo
        self.assertFalse(a.held())
        self.assertTrue(b.try_acquire())


import unittest.mock  # noqa: E402  (usado em HealthcheckTest)

assert FIXTURES.exists() and CANDIDATO


if __name__ == "__main__":
    unittest.main()
