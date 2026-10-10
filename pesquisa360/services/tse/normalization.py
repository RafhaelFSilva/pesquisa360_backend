"""DTOs internos e conversoes dos formatos do TSE.

Os arquivos EA trazem tudo como texto: inteiros como "1.234" ou "1234",
percentuais com virgula, data e hora em campos separados no horario de
Brasilia.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

# O Brasil nao adota horario de verao desde 2019; offset fixo evita depender
# de base de fusos (ausente por padrao no Windows).
BRASILIA = timezone(timedelta(hours=-3))


def to_int(value) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return int(str(value).replace(".", ""))


def to_pct(value) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    return float(str(value).replace(".", "").replace(",", "."))


def to_datetime(data: str | None, hora: str | None) -> datetime | None:
    """`dd/mm/aaaa` + `hh:mm:ss` (Brasilia) -> datetime com fuso; vazio -> None."""
    if not data or not hora:
        return None
    return datetime.strptime(f"{data} {hora}", "%d/%m/%Y %H:%M:%S").replace(tzinfo=BRASILIA)


OFICIAL = "OFICIAL"
SIMULADO = "SIMULADO"
_ORIGEM_POR_FASE = {"o": OFICIAL, "s": SIMULADO}


def origem_from_fase(fase: str | None) -> str:
    """Todo arquivo EA traz `f`: "o" (oficial) ou "s" (simulado).

    O EA18 oficial de 2026 traz a fase em maiuscula ("O"): a comparacao
    ignora caixa.

    A origem vem do proprio payload, nunca do nome do host: e ela que impede
    que dado de simulacao seja gravado ou lido como resultado oficial.
    """
    try:
        return _ORIGEM_POR_FASE[fase.casefold()]
    except (KeyError, AttributeError):
        raise ValueError(f"Fase TSE desconhecida: {fase!r}") from None


def cargo_code(value) -> str:
    return f"{int(value):04d}"


@dataclass(frozen=True)
class CargoConfig:
    codigo: str
    nome: str
    tipo: str


@dataclass(frozen=True)
class EleicaoConfig:
    codigo: str
    nome: str
    turno: int
    tipo: str
    codigo_segundo_turno: str | None
    abrangencias: tuple[str, ...]
    cargos: tuple[CargoConfig, ...]


@dataclass(frozen=True)
class PleitoConfig:
    base_url: str
    ambiente: str
    fase: str | None
    ciclo: str
    pleito: str
    data: str
    idg: str | None
    gerado_em: datetime | None
    eleicoes: tuple[EleicaoConfig, ...]
    templates: dict[str, str] = field(hash=False, compare=False, default_factory=dict)

    @property
    def origem(self) -> str:
        return origem_from_fase(self.fase)


@dataclass(frozen=True)
class Municipio:
    codigo: str
    nome: str
    codigo_ibge: str | None
    capital: bool
    zonas: tuple[str, ...]


@dataclass(frozen=True)
class LinhaAcompanhamento:
    tipo: str            # "uf", "mun" ou "br"
    codigo: str
    andamento: str | None
    ultima_totalizacao: datetime | None
    secoes: int | None
    secoes_totalizadas: int | None
    eleitorado: int | None
    comparecimento: int | None
    abstencao: int | None
    secoes_nao_instaladas: int | None = None
    secoes_nao_apuradas: int | None = None

    @property
    def chave(self) -> str:
        return f"{self.tipo}:{self.codigo}"

    @property
    def fingerprint(self) -> str:
        """Se mudar, a abrangencia precisa de nova consulta de EA20."""
        return "|".join(str(v) for v in (
            self.andamento,
            self.ultima_totalizacao.isoformat() if self.ultima_totalizacao else None,
            self.secoes_totalizadas, self.comparecimento,
        ))


@dataclass(frozen=True)
class Acompanhamento:
    eleicao: str
    turno: int | None
    idg: str | None
    gerado_em: datetime | None
    linhas: tuple[LinhaAcompanhamento, ...]
    fase: str | None = None

    def fingerprints(self) -> dict[str, str]:
        return {linha.chave: linha.fingerprint for linha in self.linhas}


@dataclass(frozen=True)
class FederacaoResultado:
    numero: str
    sigla: str
    nome: str
    partidos: tuple[str, ...]


@dataclass(frozen=True)
class PartidoResultado:
    numero: str
    sigla: str
    nome: str
    federacao_numero: str | None
    votos_nominais: int | None
    votos_legenda: int | None
    destinacao_voto: str | None = None


@dataclass(frozen=True)
class CandidatoResultado:
    sqcand: str
    numero: str
    nome: str
    nome_urna: str
    partido_numero: str
    situacao: str | None
    eleito: str | None
    votos: int | None
    percentual: float | None
    # `dvt`: "Válido", "Anulado sub judice"... Voto de candidato nao valido
    # aparece em `votos` mas NAO entra nos votos nominais validos.
    destinacao_voto: str | None = None

    @property
    def voto_valido(self) -> bool:
        dvt = self.destinacao_voto
        return dvt is None or dvt.casefold().startswith("válido")


@dataclass(frozen=True)
class Ea20:
    eleicao: str
    turno: int | None
    tipo_abrangencia: str      # "uf", "mu" ou "zona" conforme o arquivo
    codigo_abrangencia: str
    cargo: str
    cargo_nome: str | None
    vagas: int | None
    idg: str | None
    gerado_em: datetime | None
    ultima_totalizacao: datetime | None
    andamento: str | None
    totalizacao_final: str | None
    secoes: int | None
    secoes_totalizadas: int | None
    eleitorado: int | None
    comparecimento: int | None
    abstencao: int | None
    votos_total: int | None
    votos_validos: int | None
    votos_nominais: int | None
    votos_legenda: int | None
    votos_brancos: int | None
    votos_nulos: int | None
    quociente_eleitoral: int | None
    federacoes: tuple[FederacaoResultado, ...]
    partidos: tuple[PartidoResultado, ...]
    candidatos: tuple[CandidatoResultado, ...]
    fase: str | None = None
    votos_anulados: int | None = None
    votos_anulados_sub_judice: int | None = None

    @property
    def origem(self) -> str:
        return origem_from_fase(self.fase)


@dataclass(frozen=True)
class Secao:
    municipio: str
    zona: str
    secao: str
    secao_principal: str | None   # preenchido apenas em secao agregada
    agregadas: tuple[str, ...]    # preenchido apenas em secao principal
    # `da`/`ha`: data/hora do arquivo auxiliar (EA18); so existe apos a
    # totalizacao da secao e muda quando ha novo arquivo de urna.
    auxiliar_em: datetime | None = None

    @property
    def eh_principal(self) -> bool:
        return self.secao_principal is None

    @property
    def eh_agregada(self) -> bool:
        return self.secao_principal is not None


@dataclass(frozen=True)
class MunicipioSecoes:
    codigo: str
    nome: str
    secoes: tuple[Secao, ...]


@dataclass(frozen=True)
class ConfiguracaoSecoes:
    pleito: str | None
    uf: str
    idg: str | None
    gerado_em: datetime | None
    municipios: tuple[MunicipioSecoes, ...]
    fase: str | None = None


@dataclass(frozen=True)
class ArquivoUrna:
    nome: str
    tipo: str   # bu, rdv, log, vota...


@dataclass(frozen=True)
class HashSecao:
    hash: str | None
    situacao: str | None
    recebido_em: datetime | None
    arquivos: tuple[ArquivoUrna, ...]


@dataclass(frozen=True)
class AuxiliarSecao:
    idg: str | None
    gerado_em: datetime | None
    situacao: str | None
    hashes: tuple[HashSecao, ...]
    fase: str | None = None
