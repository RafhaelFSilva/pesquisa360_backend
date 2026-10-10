"""Decodificador do Boletim de Urna (BU) das Eleicoes 2026.

Funcao pura: recebe bytes e devolve um modelo interno tipado. Nao acessa
banco, nao faz HTTP e nao conhece FastAPI.

A estrutura vem EXCLUSIVAMENTE do `bu.asn1` oficial do TSE (pacote
"formato-arquivos-de-bu-rdv-e-assinatura-digital", 2026), copiado sem
alteracao para `asn1/bu-2026.asn1` e conferido por SHA-256 antes de compilar.
O procedimento e o do codigo de referencia do proprio pacote
(`python/bu_dump.py`): codec BER, envelope generico e, dentro dele, a entidade
do boletim. Nenhum byte e interpretado fora do schema.

O BU e entrada externa nao confiavel: tamanho limitado, comprimento TLV
conferido e qualquer falha de schema vira `BuInvalido`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from .normalization import OFICIAL, SIMULADO

SPEC_PATH = Path(__file__).parent / "asn1" / "bu-2026.asn1"
SPEC_SHA256 = "ef64bf723f774403b423ed0b617f5a83a3f50c0023d0003e18cd29170404ab81"
# BUs reais do AP tem ~10 KB; o maior boletim plausivel fica muito abaixo disto.
MAX_BU_BYTES = 2 * 1024 * 1024

NOMINAL, LEGENDA, BRANCO, NULO, SEM_CANDIDATO = (
    "NOMINAL", "LEGENDA", "BRANCO", "NULO", "SEM_CANDIDATO")
_TIPO_VOTO = {1: NOMINAL, 2: BRANCO, 3: NULO, 4: LEGENDA, 5: SEM_CANDIDATO}
_TIPO_CARGO = {1: "MAJORITARIO", 2: "PROPORCIONAL", 3: "CONSULTA"}
# `Fase` do schema. Treinamento nao e simulado nem oficial: nunca e ingerido.
TREINAMENTO = "TREINAMENTO"
_ORIGEM_POR_FASE = {1: SIMULADO, 2: OFICIAL, 3: TREINAMENTO}
_ENVELOPE_BU = 1


class BuInvalido(ValueError):
    """Bytes que nao sao um BU valido segundo a especificacao oficial de 2026."""


@dataclass(frozen=True)
class VotoBU:
    """`TotalVotosVotavel`. `numero` e o votavel: candidato (nominal) ou partido (legenda)."""

    tipo: str
    votos: int
    partido: str | None = None
    numero: str | None = None


@dataclass(frozen=True)
class CargoBU:
    """`TotalVotosCargo` com o comparecimento do `ResultadoVotacao` que o contem."""

    codigo: str            # "0006"; cargo/consulta livre vem do `numeroCargoConsultaLivre`
    constitucional: bool
    tipo: str              # MAJORITARIO | PROPORCIONAL | CONSULTA
    ordem_impressao: int
    comparecimento: int
    votos: tuple[VotoBU, ...]

    def _soma(self, tipo: str) -> int:
        return sum(v.votos for v in self.votos if v.tipo == tipo)

    @property
    def nominais(self) -> int:
        return self._soma(NOMINAL)

    @property
    def legenda(self) -> int:
        return self._soma(LEGENDA)

    @property
    def brancos(self) -> int:
        return self._soma(BRANCO)

    @property
    def nulos(self) -> int:
        return self._soma(NULO)


@dataclass(frozen=True)
class EleicaoBU:
    """`ResultadoVotacaoPorEleicao`."""

    codigo: str
    aptos: int
    aptos_secao: int
    aptos_tte: int
    cargos: tuple[CargoBU, ...]


@dataclass(frozen=True)
class UrnaBU:
    tipo_urna: int
    tipo_arquivo: int
    versao_votacao: str
    numero_interno: int
    codigo_carga: str
    carga_em: datetime
    historico_codigos_carga: tuple[str, ...]


@dataclass(frozen=True)
class DecodedBU:
    """BU de uma urna. A identificacao e a da secao em que a urna foi instalada.

    O schema NAO lista secoes agregadas: um BU identifica uma unica secao (a
    principal). A relacao principal -> agregadas vem do EA16.

    Datas (`DataHoraJE`) nao trazem fuso: sao a hora local da urna.
    """

    origem: str                 # OFICIAL | SIMULADO | TREINAMENTO (campo `fase`)
    pleito: str | None          # so quando `idEleitoral` e um pleito
    municipio: str              # "06050"
    zona: str                   # "0002"
    secao: str                  # "0069"
    local: int
    tipo: str                   # SECAO | SA (Sistema de Apuracao)
    gerado_em: datetime
    emitido_em: datetime
    abertura_em: datetime | None
    encerramento_em: datetime | None
    comparecimento: int
    urna: UrnaBU
    eleicoes: tuple[EleicaoBU, ...]

    def eleicao(self, codigo: str) -> EleicaoBU | None:
        return next((e for e in self.eleicoes if e.codigo == str(codigo)), None)


@lru_cache(maxsize=1)
def _spec():
    """Compila o schema uma unica vez por processo, depois de conferir o arquivo."""
    import asn1tools

    texto = SPEC_PATH.read_bytes()
    digest = hashlib.sha256(texto).hexdigest()
    if digest != SPEC_SHA256:
        raise RuntimeError(
            f"bu-2026.asn1 difere da especificacao oficial registrada (sha256 {digest})")
    return asn1tools.compile_string(texto.decode("utf-8"), codec="ber", numeric_enums=True)


def _comprimento_tlv(data: bytes) -> int | None:
    """Tamanho total (cabecalho + conteudo) da SEQUENCE externa; None se nao for uma."""
    if len(data) < 2 or data[0] != 0x30:
        return None
    first = data[1]
    if first < 0x80:
        return 2 + first
    n = first & 0x7F
    if n == 0 or len(data) < 2 + n:
        return None
    return 2 + n + int.from_bytes(data[2:2 + n], "big")


def _data(valor: str) -> datetime:
    return datetime.strptime(valor, "%Y%m%dT%H%M%S")


def _secao_id(ident: dict) -> tuple[str, str, str, int]:
    mz = ident["municipioZona"]
    return (f"{mz['municipio']:05d}", f"{mz['zona']:04d}", f"{ident['secao']:04d}",
            ident["local"])


def _cargo(tipo_cargo: int, comparecimento: int, total: dict) -> CargoBU:
    escolha, valor = total["codigoCargo"]
    votos = []
    for v in total["votosVotaveis"]:
        tipo = _TIPO_VOTO[v["tipoVoto"]]
        ident = v.get("identificacaoVotavel")
        if tipo in (NOMINAL, LEGENDA) and ident is None:
            raise BuInvalido(f"Voto {tipo} sem identificação do votável.")
        votos.append(VotoBU(
            tipo=tipo, votos=v["quantidadeVotos"],
            partido=str(ident["partido"]) if ident else None,
            numero=str(ident["codigo"]) if ident else None))
    return CargoBU(
        codigo=f"{valor:04d}", constitucional=escolha == "cargoConstitucional",
        tipo=_TIPO_CARGO[tipo_cargo], ordem_impressao=total["ordemImpressao"],
        comparecimento=comparecimento, votos=tuple(votos))


def decode_bu(raw_bytes: bytes) -> DecodedBU:
    """Decodifica um arquivo `*-bu.dat` (envelope + boletim) pelo schema oficial de 2026."""
    if not isinstance(raw_bytes, (bytes, bytearray)):
        raise BuInvalido("BU deve ser uma sequência de bytes.")
    raw = bytes(raw_bytes)
    if not raw:
        raise BuInvalido("BU vazio.")
    if len(raw) > MAX_BU_BYTES:
        raise BuInvalido(f"BU com {len(raw)} bytes excede o limite de {MAX_BU_BYTES}.")
    if _comprimento_tlv(raw) != len(raw):
        raise BuInvalido("BU truncado ou com bytes excedentes (comprimento ASN.1 não confere).")

    spec = _spec()
    try:
        envelope = spec.decode("EntidadeEnvelopeGenerico", raw)
        if envelope["tipoEnvelope"] != _ENVELOPE_BU:
            raise BuInvalido(f"Envelope não é de BU (tipoEnvelope={envelope['tipoEnvelope']}).")
        if envelope.get("seguranca") is not None:
            raise BuInvalido("Conteúdo do envelope está cifrado.")
        conteudo = envelope["conteudo"]
        if _comprimento_tlv(conteudo) != len(conteudo):
            raise BuInvalido("Conteúdo do envelope truncado ou com bytes excedentes.")
        bu = spec.decode("EntidadeBoletimUrna", conteudo)

        municipio, zona, secao, local = _secao_id(bu["identificacaoSecao"])
        escolha_env, ident_env = envelope["identificacao"]
        if (escolha_env != "identificacaoSecaoEleitoral"
                or _secao_id(ident_env)[:3] != (municipio, zona, secao)):
            raise BuInvalido("Seção do envelope difere da seção do boletim.")
        if envelope["fase"] != bu["fase"]:
            raise BuInvalido("Fase do envelope difere da fase do boletim.")

        tipo_id, valor_id = bu["cabecalho"]["idEleitoral"]
        escolha_dados, dados = bu["dadosSecaoSA"]
        de_secao = escolha_dados == "dadosSecao"
        eleicoes = []
        for resultado in bu["resultadosVotacaoPorEleicao"]:
            cargos = [_cargo(rv["tipoCargo"], rv["qtdComparecimento"], total)
                      for rv in resultado["resultadosVotacao"]
                      for total in rv["totaisVotosCargo"]]
            codigos = [c.codigo for c in cargos]
            if len(set(codigos)) != len(codigos):
                raise BuInvalido("Cargo repetido na mesma eleição do boletim.")
            eleicoes.append(EleicaoBU(
                codigo=str(resultado["idEleicao"]), aptos=resultado["qtdEleitoresAptos"],
                aptos_secao=resultado["qtdEleitoresAptosSecao"],
                aptos_tte=resultado["qtdEleitoresAptosTTE"], cargos=tuple(cargos)))
        ids = [e.codigo for e in eleicoes]
        if len(set(ids)) != len(ids):
            raise BuInvalido("Eleição repetida no boletim.")

        urna = bu["urna"]
        carga = urna["correspondenciaResultado"]["carga"]
        return DecodedBU(
            origem=_ORIGEM_POR_FASE[bu["fase"]],
            pleito=str(valor_id) if tipo_id == "idPleito" else None,
            municipio=municipio, zona=zona, secao=secao, local=local,
            tipo="SECAO" if de_secao else "SA",
            gerado_em=_data(bu["cabecalho"]["dataGeracao"]),
            emitido_em=_data(bu["dataHoraEmissao"]),
            abertura_em=_data(dados["dataHoraAbertura"]) if de_secao else None,
            encerramento_em=_data(dados["dataHoraEncerramento"]) if de_secao else None,
            comparecimento=bu["qtdEleitoresCompareceram"],
            urna=UrnaBU(
                tipo_urna=urna["tipoUrna"], tipo_arquivo=urna["tipoArquivo"],
                versao_votacao=urna["versaoVotacao"],
                numero_interno=carga["numeroInternoUrna"], codigo_carga=carga["codigoCarga"],
                carga_em=_data(carga["dataHoraCarga"]),
                historico_codigos_carga=tuple(bu["historicoCodigosCarga"])),
            eleicoes=tuple(eleicoes))
    except BuInvalido:
        raise
    except Exception as exc:
        # DecodeError/ConstraintsError do asn1tools, campo ausente, data fora do
        # formato, enum desconhecido: tudo e "nao e um BU valido de 2026".
        raise BuInvalido(f"BU incompatível com a especificação oficial 2026: "
                         f"{type(exc).__name__}: {str(exc)[:200]}") from exc
