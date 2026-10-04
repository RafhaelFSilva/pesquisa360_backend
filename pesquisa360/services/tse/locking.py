"""Singleton do ingestor TSE por PostgreSQL advisory lock (ADR-088).

Um unico processo por ORIGEM grava dados do TSE de cada vez -- o worker
automatico e a CLI manual disputam a mesma trava. O lock e de SESSAO: vive
numa conexao dedicada e e solto pelo proprio PostgreSQL se o processo morrer
ou a conexao cair, entao nao existe trava orfa.

Chave: `pg_try_advisory_lock(LOCK_NAMESPACE, <origem>)`, dois int4.
    LOCK_NAMESPACE = 5526341  (0x545345, "TSE" em ASCII)
    OFICIAL = 1, SIMULADO = 2
"""

from __future__ import annotations

import threading

from sqlalchemy import text
from sqlalchemy.engine import Engine

LOCK_NAMESPACE = 0x545345
ORIGEM_KEYS = {"OFICIAL": 1, "SIMULADO": 2}

# Fora do PostgreSQL (SQLite dos testes) nao ha advisory lock: a exclusao e
# garantida apenas dentro do proprio processo.
_locais: set[tuple[str, int]] = set()
_locais_guard = threading.Lock()


class IngestionLock:
    def __init__(self, engine: Engine, origem: str):
        if origem not in ORIGEM_KEYS:
            raise ValueError(f"Origem TSE invalida: {origem!r}")
        self.engine = engine
        self.origem = origem
        self.key = ORIGEM_KEYS[origem]
        self._postgres = engine.dialect.name == "postgresql"
        self._conn = None
        self._local_key = (str(engine.url), self.key)
        self._held_local = False

    def try_acquire(self) -> bool:
        """Tenta obter a trava sem bloquear. True se este objeto a detem."""
        if self.held():
            return True
        if not self._postgres:
            with _locais_guard:
                if self._local_key in _locais:
                    return False
                _locais.add(self._local_key)
                self._held_local = True
                return True
        # AUTOCOMMIT: a conexao que segura a trava nunca fica "idle in transaction".
        conn = self.engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            acquired = conn.execute(
                text("SELECT pg_try_advisory_lock(:namespace, :key)"),
                {"namespace": LOCK_NAMESPACE, "key": self.key},
            ).scalar()
        except Exception:
            conn.close()
            raise
        if not acquired:
            conn.close()
            return False
        self._conn = conn
        return True

    def held(self) -> bool:
        """A trava continua nossa? Conexao perdida = trava perdida."""
        if not self._postgres:
            return self._held_local
        if self._conn is None:
            return False
        try:
            self._conn.execute(text("SELECT 1"))
            return True
        except Exception:
            self._descartar_conexao()
            return False

    def release(self) -> None:
        if not self._postgres:
            if self._held_local:
                with _locais_guard:
                    _locais.discard(self._local_key)
                self._held_local = False
            return
        if self._conn is None:
            return
        try:
            self._conn.execute(
                text("SELECT pg_advisory_unlock(:namespace, :key)"),
                {"namespace": LOCK_NAMESPACE, "key": self.key},
            )
        except Exception:
            pass  # conexao ja perdida: o PostgreSQL soltou a trava sozinho
        self._descartar_conexao()

    def _descartar_conexao(self) -> None:
        conn, self._conn = self._conn, None
        try:
            conn.invalidate()
            conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.release()
