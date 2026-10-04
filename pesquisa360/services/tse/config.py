"""Configuracao do servico TSE, lida do ambiente com defaults seguros.

Nao existe credencial neste fluxo: os arquivos de divulgacao sao publicos.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# O TSE informa teto de 100 requisicoes/IP/segundo. O servico opera muito
# abaixo disso e recusa configuracao acima deste limite interno.
LIMITE_INTERNO_RPS = 10.0


@dataclass(frozen=True)
class TseSettings:
    base_url: str = "https://resultados.tse.jus.br"
    ambiente: str = "oficial"
    request_timeout: float = 20.0
    max_retries: int = 3
    requests_per_second: float = 2.0
    user_agent: str = "Pesquisa360-TSE/1.0 (ingestao oficial somente leitura)"

    def __post_init__(self):
        if not self.base_url.startswith("https://"):
            raise ValueError("TSE_BASE_URL deve usar https")
        if not 0 < self.requests_per_second <= LIMITE_INTERNO_RPS:
            raise ValueError(
                f"TSE_REQUESTS_PER_SECOND deve ficar entre 0 e {LIMITE_INTERNO_RPS:g}"
            )
        if self.request_timeout <= 0:
            raise ValueError("TSE_REQUEST_TIMEOUT deve ser positivo")
        if self.max_retries < 0:
            raise ValueError("TSE_MAX_RETRIES nao pode ser negativo")
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))


def load_settings(environ=None) -> TseSettings:
    env = os.environ if environ is None else environ
    defaults = TseSettings()
    return TseSettings(
        base_url=env.get("TSE_BASE_URL", defaults.base_url),
        ambiente=env.get("TSE_AMBIENTE", defaults.ambiente),
        request_timeout=float(env.get("TSE_REQUEST_TIMEOUT", defaults.request_timeout)),
        max_retries=int(env.get("TSE_MAX_RETRIES", defaults.max_retries)),
        requests_per_second=float(
            env.get("TSE_REQUESTS_PER_SECOND", defaults.requests_per_second)
        ),
    )
