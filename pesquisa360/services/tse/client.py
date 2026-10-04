"""Cliente HTTP do TSE: politica unica de taxa, retry, cache condicional e 404.

Sincrono por decisao: sessoes SQLAlchemy e a CLI de ingestao sao sincronas e
a taxa (poucas requisicoes por segundo, em serie) nao se beneficia de
`AsyncClient`. Os validadores de cache (ETag / Last-Modified) vem de quem
chama -- em producao, do ultimo snapshot persistido.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from .config import TseSettings, load_settings

STATUS_TRANSITORIOS = frozenset({429, 500, 502, 503, 504})
BACKOFF_MAXIMO_SEGUNDOS = 30.0


class TseHttpError(RuntimeError):
    """Resposta definitiva inesperada (4xx diferente de 404)."""

    def __init__(self, url: str, status: int):
        super().__init__(f"TSE respondeu HTTP {status} para {url}")
        self.url, self.status = url, status


class TseTransientError(RuntimeError):
    """Falha transitoria que persistiu apos todas as tentativas."""


@dataclass
class TseResponse:
    url: str
    status: int
    content: bytes | None = None
    etag: str | None = None
    last_modified: str | None = None
    elapsed_ms: float = 0.0

    @property
    def not_modified(self) -> bool:
        return self.status == 304

    @property
    def not_found(self) -> bool:
        return self.status == 404


@dataclass
class TseClient:
    settings: TseSettings = field(default_factory=load_settings)
    transport: httpx.BaseTransport | None = None
    sleep: callable = time.sleep
    clock: callable = time.monotonic

    def __post_init__(self):
        self._http = httpx.Client(
            timeout=self.settings.request_timeout,
            headers={"User-Agent": self.settings.user_agent, "Accept": "application/json, */*"},
            transport=self.transport,
            follow_redirects=False,
        )
        self._host = urlparse(self.settings.base_url).hostname
        self._min_interval = 1.0 / self.settings.requests_per_second
        self._last_request_at: float | None = None
        self._not_found: set[str] = set()
        # (inicio, url, status) de cada requisicao de rede: evidencia de taxa.
        self.requests: list[tuple[float, str, int]] = []

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def get(self, url: str, *, etag: str | None = None,
            last_modified: str | None = None) -> TseResponse:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != self._host:
            raise ValueError(f"URL fora da infraestrutura oficial do TSE: {url}")
        if url in self._not_found:
            # 404 e definitivo dentro da execucao: nunca e repetido.
            return TseResponse(url=url, status=404)

        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        last_error: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            started = self._throttle()
            retry_after = None
            try:
                resp = self._http.get(url, headers=headers)
            except httpx.TransportError as exc:
                self.requests.append((started, url, 0))
                last_error = exc
            else:
                self.requests.append((started, url, resp.status_code))
                result = TseResponse(
                    url=url, status=resp.status_code,
                    etag=resp.headers.get("ETag"),
                    last_modified=resp.headers.get("Last-Modified"),
                    elapsed_ms=(self.clock() - started) * 1000,
                )
                if resp.status_code == 200:
                    result.content = resp.content
                    return result
                if resp.status_code == 304:
                    return result
                if resp.status_code == 404:
                    self._not_found.add(url)
                    return result
                if resp.status_code not in STATUS_TRANSITORIOS:
                    raise TseHttpError(url, resp.status_code)
                last_error = TseHttpError(url, resp.status_code)
                retry_after = resp.headers.get("Retry-After")
            if attempt < self.settings.max_retries:
                self.sleep(self._backoff(attempt, retry_after))
        raise TseTransientError(f"Falha transitoria persistente em {url}: {last_error}")

    def peak_requests_per_second(self) -> int:
        times = sorted(t for t, _url, _status in self.requests)
        return max(
            (sum(1 for t in times[i:] if t - start < 1.0) for i, start in enumerate(times)),
            default=0,
        )

    def _throttle(self) -> float:
        if self._last_request_at is not None:
            wait = self._min_interval - (self.clock() - self._last_request_at)
            if wait > 0:
                self.sleep(wait)
        self._last_request_at = self.clock()
        return self._last_request_at

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None) -> float:
        if retry_after and retry_after.strip().isdigit():
            return min(float(retry_after), BACKOFF_MAXIMO_SEGUNDOS)
        return min(2.0 ** attempt, BACKOFF_MAXIMO_SEGUNDOS)
