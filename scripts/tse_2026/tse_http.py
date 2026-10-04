"""Cliente HTTP conservador para os arquivos de divulgacao do TSE (POC, somente leitura).

Usa apenas a biblioteca padrao: a POC roda fora do container da API e nao
adiciona dependencia ao projeto. Nao existe credencial neste fluxo.

Garantias:
- so acessa o host oficial de resultados;
- limite de requisicoes muito abaixo do teto do TSE (100 req/IP/s);
- GET condicional (ETag / Last-Modified) com tratamento de 304;
- 404 e resposta definitiva dentro da execucao: nunca e repetido;
- retry com backoff exponencial apenas para falha transitoria;
- snapshot local por conteudo (SHA-256), sem sobrescrever versao diferente.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

HOST_OFICIAL = "resultados.tse.jus.br"
USER_AGENT = "Pesquisa360-TSE-POC/0.1 (prova de conceito somente leitura)"
STATUS_TRANSITORIOS = {429, 500, 502, 503, 504}


@dataclass
class FetchResult:
    url: str
    status: int
    body: bytes | None = None
    etag: str | None = None
    last_modified: str | None = None
    elapsed_ms: float = 0.0
    sha256: str | None = None
    snapshot: str | None = None
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return self.body is not None

    @property
    def not_found(self) -> bool:
        return self.status == 404

    def json(self):
        return json.loads(self.body.decode("utf-8"))


class TseHttpClient:
    def __init__(
        self,
        artifacts_dir: Path,
        max_rps: float = 2.0,
        timeout: float = 20.0,
        max_retries: int = 3,
    ):
        if not 0 < max_rps <= 5:
            raise ValueError("max_rps da POC deve ficar entre 0 e 5 req/s")
        self.artifacts_dir = Path(artifacts_dir)
        self.min_interval = 1.0 / max_rps
        self.max_rps = max_rps
        self.timeout = timeout
        self.max_retries = max_retries
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.request_log: list[dict] = []
        self._last_request_at = 0.0
        self._not_found: dict[str, FetchResult] = {}
        self._index_path = self.artifacts_dir / "_cache" / "index.json"
        self._log_path = self.artifacts_dir / "_logs" / f"requests-{self.run_id}.jsonl"
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, dict] = (
            json.loads(self._index_path.read_text(encoding="utf-8"))
            if self._index_path.exists()
            else {}
        )

    # ------------------------------------------------------------------ API
    def get(self, url: str, kind: str) -> FetchResult:
        """Busca `url`; `kind` (ea11, ea20, bu...) define a pasta do snapshot."""
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != HOST_OFICIAL:
            raise ValueError(f"URL fora da infraestrutura oficial do TSE: {url}")

        if url in self._not_found:
            self._log(url, kind, 404, None, None, 0.0, None, nota="404 ja conhecido; nao repetido")
            return self._not_found[url]

        cached = self._index.get(url)
        if cached and not (self.artifacts_dir / cached["snapshot"]).exists():
            cached = None

        result = self._request_with_retry(url, cached)

        if result.status == 304 and cached:
            result.body = (self.artifacts_dir / cached["snapshot"]).read_bytes()
            result.etag = result.etag or cached.get("etag")
            result.last_modified = result.last_modified or cached.get("last_modified")
            result.sha256 = cached["sha256"]
            result.snapshot = cached["snapshot"]
            result.from_cache = True
        elif result.status == 200:
            result.sha256 = hashlib.sha256(result.body).hexdigest()
            result.snapshot = self._save_snapshot(url, kind, result)
            self._index[url] = {
                "etag": result.etag,
                "last_modified": result.last_modified,
                "sha256": result.sha256,
                "snapshot": result.snapshot,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            self._index_path.write_text(
                json.dumps(self._index, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        elif result.status == 404:
            self._not_found[url] = result

        self._log(
            url, kind, result.status, result.etag, result.last_modified,
            result.elapsed_ms, result.sha256, snapshot=result.snapshot,
        )
        return result

    def annotate_last(self, **extra) -> None:
        """Acrescenta ao ultimo registro dados conhecidos so apos o parse (ex.: IDG)."""
        if self.request_log:
            self.request_log[-1].update(extra)
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"annotate": self.request_log[-1]["url"], **extra}) + "\n")

    def rate_limit_report(self) -> dict:
        """Evidencia para o TESTE 12: pico de req/s e ausencia de 404 repetido."""
        reais = [r for r in self.request_log if r.get("network")]
        times = sorted(r["t_monotonic"] for r in reais)
        peak = 0
        for i, start in enumerate(times):
            peak = max(peak, sum(1 for t in times[i:] if t - start < 1.0))
        hits_404: dict[str, int] = {}
        for r in reais:
            if r["status"] == 404:
                hits_404[r["url"]] = hits_404.get(r["url"], 0) + 1
        return {
            "requisicoes_de_rede": len(reais),
            "limite_configurado_rps": self.max_rps,
            "pico_observado_em_1s": peak,
            "urls_404": len(hits_404),
            "404_repetidos": {u: n for u, n in hits_404.items() if n > 1},
        }

    # ------------------------------------------------------------- internos
    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _request_with_retry(self, url: str, cached: dict | None) -> FetchResult:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json, */*"}
        if cached:
            if cached.get("etag"):
                headers["If-None-Match"] = cached["etag"]
            if cached.get("last_modified"):
                headers["If-Modified-Since"] = cached["last_modified"]

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            started = time.monotonic()
            retry_after = None
            try:
                req = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read()
                    return FetchResult(
                        url=url, status=resp.status, body=body,
                        etag=resp.headers.get("ETag"),
                        last_modified=resp.headers.get("Last-Modified"),
                        elapsed_ms=(time.monotonic() - started) * 1000,
                    )
            except urllib.error.HTTPError as exc:
                elapsed = (time.monotonic() - started) * 1000
                if exc.code not in STATUS_TRANSITORIOS:
                    # 304, 404 e demais 4xx sao respostas definitivas: sem retry.
                    return FetchResult(
                        url=url, status=exc.code,
                        etag=exc.headers.get("ETag"),
                        last_modified=exc.headers.get("Last-Modified"),
                        elapsed_ms=elapsed,
                    )
                last_error = exc
                retry_after = exc.headers.get("Retry-After")
                self._log(url, "retry", exc.code, None, None, elapsed, None,
                          nota=f"transitorio; tentativa {attempt + 1}")
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last_error = exc
                self._log(url, "retry", 0, None, None,
                          (time.monotonic() - started) * 1000, None,
                          nota=f"{type(exc).__name__}; tentativa {attempt + 1}")
            if attempt < self.max_retries:
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2.0 ** attempt
                time.sleep(min(delay, 30.0))
        raise RuntimeError(f"Falha transitoria persistente em {url}: {last_error}")

    def _save_snapshot(self, url: str, kind: str, result: FetchResult) -> str:
        name = Path(urlparse(url).path).name
        stem, _, suffix = name.rpartition(".")
        target_dir = self.artifacts_dir / kind
        target_dir.mkdir(parents=True, exist_ok=True)
        # O nome carrega o hash do conteudo: versoes diferentes nunca colidem.
        target = target_dir / f"{stem}__{result.sha256[:12]}.{suffix}"
        if not target.exists():
            target.write_bytes(result.body)
        return target.relative_to(self.artifacts_dir).as_posix()

    def _log(self, url, kind, status, etag, last_modified, elapsed_ms, sha256,
             snapshot=None, nota=None) -> None:
        entry = {
            "at": datetime.now(timezone.utc).isoformat(),
            # Instante de INICIO da requisicao: e o que o limite de taxa controla.
            "t_monotonic": self._last_request_at,
            "network": not (nota or "").startswith("404 ja conhecido"),
            "kind": kind, "url": url, "status": status, "etag": etag,
            "last_modified": last_modified, "elapsed_ms": round(elapsed_ms, 1),
            "sha256": sha256, "snapshot": snapshot,
        }
        if nota:
            entry["nota"] = nota
        self.request_log.append(entry)
        with self._log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def result_summary(result: FetchResult) -> dict:
    data = asdict(result)
    data.pop("body")
    return data
