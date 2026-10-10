"""Ingestor automatico do TSE: processo separado da API (ADR-087).

    python -m pesquisa360.services.tse.worker              # loop
    python -m pesquisa360.services.tse.worker --healthcheck

TSE -> worker -> PostgreSQL -> API. A API so le o banco; quem fala com o TSE
e este processo (ou a CLI manual, sob a mesma trava).

Comportamento:
- configuracao por ambiente (`TSE_INGESTION_*`); DESLIGADO por padrao -- o
  processo fica vivo e ocioso, sem tocar o TSE;
- singleton por origem via advisory lock (locking.py). Sem a trava o worker
  fica em espera (standby) e tenta de novo no proximo ciclo;
- cada UF de cada ciclo e uma transacao; qualquer erro faz rollback e o
  ultimo estado valido permanece;
- SIGTERM/SIGINT encerram de forma limpa: a espera e interrompida e uma
  ingestao em andamento e abortada com rollback;
- um heartbeat em arquivo alimenta o healthcheck do container.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import bu_reconciliation
from .bu_ingestion import BuIngestion, BuScope
from .client import TseClient
from .config import (
    TODOS, TseIngestionSettings, TseSettings, load_ingestion_settings, settings_for_origem,
)
from .ingestion import IngestScope, TseIngestion
from .locking import IngestionLock

logger = logging.getLogger("pesquisa360.tse.worker")

# Sem heartbeat por mais que isto o worker e considerado travado.
HEALTH_MIN_SECONDS = 120.0
HEALTH_INTERVALS = 4


class ShutdownRequested(Exception):
    """Pedido de parada recebido no meio de uma ingestao (provoca rollback)."""


def health_max_age(interval_seconds: float) -> float:
    return max(HEALTH_MIN_SECONDS, HEALTH_INTERVALS * interval_seconds)


class TseIngestorWorker:
    def __init__(self, settings: TseIngestionSettings, tse_settings: TseSettings, engine,
                 session_factory, *, transport=None, stop_event: threading.Event | None = None):
        self.settings = settings
        self.tse_settings = tse_settings
        self.session_factory = session_factory
        self.transport = transport
        self.stop_event = stop_event or threading.Event()
        self.lock = IngestionLock(engine, settings.origem)
        self._last_standby_log = 0.0

    # ------------------------------------------------------------------ loop
    def run(self, max_iterations: int | None = None) -> int:
        """Executa ciclos ate receber parada (ou `max_iterations`, para QA/testes)."""
        s = self.settings
        logger.info(
            "tse_ingestor_start %s", json.dumps({
                "enabled": s.enabled, "origem": s.origem, "ufs": list(s.ufs),
                "cargos": list(s.cargos), "interval_seconds": s.interval_seconds,
                "bu_enabled": s.bu_enabled, "bu_batch_size": s.bu_batch_size,
                "requests_per_second": self.tse_settings.requests_per_second,
                "base_url": self.tse_settings.base_url, "pid": os.getpid(),
            }))
        iterations = 0
        try:
            while not self.stop_event.is_set():
                started = time.monotonic()
                self._iteration()
                iterations += 1
                if max_iterations is not None and iterations >= max_iterations:
                    break
                self.stop_event.wait(max(0.0, s.interval_seconds - (time.monotonic() - started)))
        finally:
            self.lock.release()
            self._heartbeat("stopped")
            logger.info("tse_ingestor_stop %s", json.dumps({"iterations": iterations}))
        return 0

    def _iteration(self) -> None:
        if not self.settings.enabled:
            self._heartbeat("disabled")
            return
        try:
            acquired = self.lock.try_acquire()
        except Exception as exc:  # banco indisponivel: tenta de novo no proximo ciclo
            self._heartbeat("db_unavailable")
            logger.error("tse_ingestor_lock_error %s", json.dumps({"erro": _erro(exc)}))
            return
        if not acquired:
            self._heartbeat("standby")
            # Um aviso por minuto basta: o standby e um estado saudavel.
            if time.monotonic() - self._last_standby_log >= 60 or not self._last_standby_log:
                self._last_standby_log = time.monotonic()
                logger.warning("tse_ingestor_standby %s", json.dumps({
                    "origem": self.settings.origem,
                    "motivo": "advisory lock em uso por outro ingestor ou pela CLI"}))
            return
        record = self.run_cycle()
        self._heartbeat(record["status"])

    # ----------------------------------------------------------------- ciclo
    def run_cycle(self) -> dict:
        """Uma passada por todas as UFs. Nunca levanta: erros viram `status`."""
        s = self.settings
        started = time.monotonic()
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "origem": s.origem, "ufs": list(s.ufs), "cargos": list(s.cargos), "pleito": None,
            # UFs que mudaram (lista curta) e QUANTOS municipios mudaram: a lista de
            # municipios nao cabe em log quando o escopo cresce.
            "ea14_changed": [], "ea15_changed": 0, "requests": 0,
            "200": 0, "304": 0, "404": 0, "429": 0, "ea20": 0,
            "snapshots_new": 0, "totalizacoes_new": 0, "errors": [], "status": "ok",
        }
        with TseClient(settings=self.tse_settings, transport=self.transport,
                       sleep=self._sleep) as client:
            for uf in s.ufs:
                if self.stop_event.is_set():
                    record["status"] = "interrupted"
                    break
                scope = IngestScope(pleito=s.pleito, uf=uf, cargos=s.cargos,
                                    municipios=s.municipios, zonas_de=s.zonas_de,
                                    origem=s.origem)
                session = self.session_factory()
                try:
                    report = TseIngestion(session, client, self.tse_settings).run(scope)
                except ShutdownRequested:
                    session.rollback()
                    record["status"] = "interrupted"
                    break
                except Exception as exc:
                    # Rollback: nada do ciclo desta UF fica gravado pela metade.
                    session.rollback()
                    record["status"] = "error"
                    record["errors"].append({"uf": uf, "erro": _erro(exc)})
                    continue
                finally:
                    session.close()
                self._somar(record, report)
                if s.bu_enabled and not self._boletins(client, uf, report.pleito, record):
                    break
            record["requests"] = len(client.requests)
            for _t, url, status in client.requests:
                if str(status) in ("200", "304", "404", "429"):
                    record[str(status)] += 1
                if status == 200 and url.endswith("-u.json"):
                    record["ea20"] += 1
        record["duration_ms"] = round((time.monotonic() - started) * 1000)
        nivel = logging.ERROR if record["status"] == "error" else logging.INFO
        logger.log(nivel, "tse_ingest_cycle %s", json.dumps(record, ensure_ascii=False))
        return record

    def _boletins(self, client, uf: str, pleito: str, record: dict) -> bool:
        """Um lote de BUs da UF. Devolve False se o worker deve parar o ciclo.

        Roda depois do EA20 e com sessao propria: uma falha aqui nao desfaz a
        totalizacao ja gravada, e cada boletim e a sua propria transacao.
        """
        s = self.settings
        bu = record.setdefault("bu", {})
        municipios = () if TODOS in s.bu_municipios else s.bu_municipios
        session = self.session_factory()
        try:
            relatorio = BuIngestion(
                session, client, self.tse_settings, archive_dir=s.bu_archive_dir,
            ).run(BuScope(origem=s.origem, pleito=pleito, uf=uf,
                          batch_size=s.bu_batch_size, municipios=municipios))
            for chave, valor in relatorio.contadores().items():
                bu[chave] = bu.get(chave, 0) + valor
            conferencia = bu_reconciliation.conferir_zonas(
                session, s.origem, pleito, uf, sorted(relatorio.zonas), s.cargos)
            for chave, valor in conferencia.items():
                bu[chave] = bu.get(chave, 0) + valor
        except ShutdownRequested:
            session.rollback()
            record["status"] = "interrupted"
            return False
        except Exception as exc:
            session.rollback()
            record["status"] = "error"
            record["errors"].append({"uf": uf, "etapa": "bu", "erro": _erro(exc)})
        finally:
            session.close()
        return True

    @staticmethod
    def _somar(record: dict, report) -> None:
        record["pleito"] = report.pleito
        record["ea14_changed"] = sorted(set(record["ea14_changed"]) | {
            k for k in report.abrangencias_alteradas if k.startswith("uf:")})
        record["ea15_changed"] += sum(
            1 for k in report.abrangencias_alteradas if k.startswith("mun:"))
        record["snapshots_new"] += report.snapshots_novos
        record["totalizacoes_new"] += report.totalizacoes_novas

    # ---------------------------------------------------------------- suporte
    def _sleep(self, seconds: float) -> None:
        """Espera do cliente HTTP: mantem o heartbeat e respeita o pedido de parada."""
        self._heartbeat("running")
        if self.stop_event.wait(seconds):
            raise ShutdownRequested()

    def _heartbeat(self, status: str) -> None:
        payload = {"at": time.time(), "status": status, "pid": os.getpid(),
                   "origem": self.settings.origem,
                   "interval_seconds": self.settings.interval_seconds}
        path = self.settings.heartbeat_file
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            logger.error("tse_ingestor_heartbeat_error %s", json.dumps({"erro": _erro(exc)}))


def _erro(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


def healthcheck(heartbeat_file: Path, now: float | None = None) -> tuple[bool, str]:
    """Saudavel = heartbeat recente e processo nao encerrado."""
    try:
        beat = json.loads(Path(heartbeat_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "sem heartbeat"
    age = (time.time() if now is None else now) - float(beat.get("at", 0))
    limite = health_max_age(float(beat.get("interval_seconds") or 0))
    if beat.get("status") == "stopped":
        return False, "worker encerrado"
    if age > limite:
        return False, f"heartbeat ha {age:.0f}s (limite {limite:.0f}s)"
    return True, f"{beat.get('status')} ha {age:.0f}s"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Ingestor automatico do TSE")
    parser.add_argument("--healthcheck", action="store_true",
                        help="sai com 0 se o worker esta vivo e batendo")
    parser.add_argument("--max-iterations", type=int, default=None,
                        help="encerra apos N ciclos (QA); padrao: roda ate ser parado")
    args = parser.parse_args(argv)

    settings = load_ingestion_settings()
    if args.healthcheck:
        ok, detalhe = healthcheck(settings.heartbeat_file)
        print(detalhe)
        return 0 if ok else 1

    logging.basicConfig(level=os.environ.get("TSE_INGESTION_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(message)s")
    # O cliente HTTP nao deve despejar cada requisicao no log do ciclo.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    from pesquisa360.db.session import SessionLocal, engine  # exige DATABASE_URL

    worker = TseIngestorWorker(settings, settings_for_origem(settings.origem), engine, SessionLocal)

    def _parar(signum, _frame):
        logger.info("tse_ingestor_signal %s", json.dumps({"signal": signum}))
        worker.stop_event.set()

    signal.signal(signal.SIGTERM, _parar)
    signal.signal(signal.SIGINT, _parar)
    return worker.run(max_iterations=args.max_iterations)


if __name__ == "__main__":
    sys.exit(main())
