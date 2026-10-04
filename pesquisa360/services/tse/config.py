"""Configuracao do servico TSE, lida do ambiente com defaults seguros.

Nao existe credencial neste fluxo: os arquivos de divulgacao sao publicos.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

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


# --------------------------------------------------------- ingestao automatica
OFICIAL, SIMULADO = "OFICIAL", "SIMULADO"
# Ambiente oficial de simulacao das Eleicoes 2026, documentado pelo TSE.
SIMULADO_BASE_URL = "https://resultados-sim.tse.jus.br/simulado"
SIMULADO_AMBIENTE = "simulado2026"

UFS = frozenset(
    "ac al am ap ba ce df es go ma mg ms mt pa pb pe pi pr rj rn ro rr rs sc se sp to".split()
)
TODOS = "*"
INTERVALO_PADRAO_SEGUNDOS = 20
INTERVALO_MINIMO_SEGUNDOS = 15
INTERVALO_MAXIMO_SEGUNDOS = 3600


def settings_for_origem(origem: str, settings: TseSettings | None = None) -> TseSettings:
    """Aponta o cliente para o ambiente do TSE correspondente a origem."""
    settings = settings or load_settings()
    if origem == SIMULADO:
        return replace(settings, base_url=SIMULADO_BASE_URL, ambiente=SIMULADO_AMBIENTE)
    if origem == OFICIAL:
        return settings
    raise ValueError(f"Origem TSE invalida: {origem!r}")


@dataclass(frozen=True)
class TseIngestionSettings:
    """Escopo e ritmo do ingestor automatico (worker). Desligado por padrao."""

    enabled: bool = False
    origem: str = OFICIAL
    ufs: tuple[str, ...] = ()
    cargos: tuple[str, ...] = ("0001", "0003", "0005", "0006", "0007")
    # (TODOS,) = todos os municipios da UF; () = nenhum.
    municipios: tuple[str, ...] = (TODOS,)
    zonas_de: tuple[str, ...] = ()
    interval_seconds: float = INTERVALO_PADRAO_SEGUNDOS
    # None = descoberto no EA11 (pleito mais recente que disputa os cargos).
    pleito: str | None = None
    heartbeat_file: Path = Path(tempfile.gettempdir()) / "tse_ingestor.heartbeat"

    def __post_init__(self):
        if self.origem not in (OFICIAL, SIMULADO):
            raise ValueError("TSE_INGESTION_ORIGIN deve ser OFICIAL ou SIMULADO")
        invalidas = [uf for uf in self.ufs if uf not in UFS]
        if invalidas:
            raise ValueError(f"TSE_INGESTION_UFS com UF invalida: {', '.join(invalidas)}")
        if self.enabled and not self.ufs:
            raise ValueError("TSE_INGESTION_UFS e obrigatoria quando a ingestao esta habilitada")
        if not self.cargos:
            raise ValueError("TSE_INGESTION_CARGOS nao pode ser vazia")
        for cargo in self.cargos:
            if not (len(cargo) == 4 and cargo.isdigit()):
                raise ValueError(f"TSE_INGESTION_CARGOS com cargo invalido: {cargo!r}")
        for nome, codigos in (("MUNICIPIOS", self.municipios), ("ZONAS_DE", self.zonas_de)):
            for codigo in codigos:
                if codigo != TODOS and not (len(codigo) == 5 and codigo.isdigit()):
                    raise ValueError(f"TSE_INGESTION_{nome} com municipio invalido: {codigo!r}")
        if not INTERVALO_MINIMO_SEGUNDOS <= self.interval_seconds <= INTERVALO_MAXIMO_SEGUNDOS:
            raise ValueError(
                f"TSE_INGESTION_INTERVAL_SECONDS deve ficar entre {INTERVALO_MINIMO_SEGUNDOS} "
                f"e {INTERVALO_MAXIMO_SEGUNDOS}")
        if self.pleito is not None and not self.pleito.isdigit():
            raise ValueError("TSE_INGESTION_PLEITO deve ser numerico")


def _lista(valor: str | None) -> tuple[str, ...]:
    return tuple(item.strip() for item in (valor or "").replace(";", ",").split(",") if item.strip())


def _municipios(valor: str | None, padrao: tuple[str, ...]) -> tuple[str, ...]:
    if valor is None:
        return padrao
    itens = _lista(valor)
    if any(item.casefold() in ("todos", TODOS) for item in itens):
        return (TODOS,)
    return tuple(item.zfill(5) if item.isdigit() else item for item in itens)


def _booleano(valor: str | None) -> bool:
    texto = (valor or "").strip().casefold()
    if texto in ("", "0", "false", "no", "nao", "off"):
        return False
    if texto in ("1", "true", "yes", "sim", "on"):
        return True
    raise ValueError(f"TSE_INGESTION_ENABLED invalido: {valor!r}")


def load_ingestion_settings(environ=None) -> TseIngestionSettings:
    env = os.environ if environ is None else environ
    padrao = TseIngestionSettings()
    cargos = _lista(env.get("TSE_INGESTION_CARGOS"))
    return TseIngestionSettings(
        enabled=_booleano(env.get("TSE_INGESTION_ENABLED")),
        origem=(env.get("TSE_INGESTION_ORIGIN") or padrao.origem).strip().upper(),
        ufs=tuple(uf.lower() for uf in _lista(env.get("TSE_INGESTION_UFS"))),
        cargos=tuple(c.zfill(4) if c.isdigit() else c for c in cargos) or padrao.cargos,
        municipios=_municipios(env.get("TSE_INGESTION_MUNICIPIOS"), padrao.municipios),
        zonas_de=_municipios(env.get("TSE_INGESTION_ZONAS_DE"), padrao.zonas_de),
        interval_seconds=float(env.get("TSE_INGESTION_INTERVAL_SECONDS", padrao.interval_seconds)),
        pleito=(env.get("TSE_INGESTION_PLEITO") or "").strip() or None,
        heartbeat_file=Path(env.get("TSE_INGESTION_HEARTBEAT_FILE") or padrao.heartbeat_file),
    )
