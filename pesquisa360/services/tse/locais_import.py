"""Metadados oficiais dos locais de votacao: leitura do CSV do TSE, conciliacao e importacao.

Fonte: "Eleitorado por local de votacao" (Portal de Dados Abertos do TSE),
arquivo `eleitorado_local_votacao_<ano>_<UF>.csv` -- Latin 1, campos entre
aspas separados por ponto e virgula, uma linha por SECAO e por TURNO (o arquivo
passa a trazer as linhas do 2o turno quando ele e convocado).

O que este modulo faz e o que NAO faz:
- traz nome, endereco e bairro; nunca voto;
- casa com o Boletim de Urna SO pela chave oficial (municipio + zona +
  codigo). Nao compara nomes, nao aproxima e nao usa endereco para achar
  codigo;
- o codigo que o BU grava e o do local ORIGINAL (o cadastrado). O CSV traz
  tambem o local "utilizado no pleito", que difere quando o original estava
  indisponivel e o TRE designou um temporario. A chave usa as colunas
  `*_ORIGINAL`; o bairro so e aproveitado das secoes que votaram no proprio
  local original (a coluna de bairro descreve o local utilizado);
- se algum local do BU ficar sem correspondencia ou ambiguo, nada e gravado;
- o turno e sempre informado por quem chama e filtra as linhas ANTES de
  qualquer agrupamento: turnos nunca se somam nem completam um ao outro.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from pesquisa360.db.models_tse import (
    TseBoletimUrna, TseBuControle, TseEleicao, TseLocalVotacao, TseSecao,
)

from .normalization import BRASILIA

FONTE = "TSE_ELEITORADO_LOCAL_VOTACAO"
MAX_CSV_BYTES = 80 * 1024 * 1024
COLUNAS = (
    "DT_GERACAO", "HH_GERACAO", "AA_ELEICAO", "DT_ELEICAO", "NR_TURNO", "SG_UF", "CD_MUNICIPIO",
    "NR_ZONA", "NR_SECAO", "NR_LOCAL_VOTACAO", "NM_BAIRRO", "NR_LOCAL_VOTACAO_ORIGINAL",
    "NM_LOCAL_VOTACAO_ORIGINAL", "DS_ENDERECO_LOCVT_ORIGINAL",
)
_VAZIOS = {"", "#NULO", "#NULO#", "#NE", "#NE#", "-1"}


class FonteInvalida(ValueError):
    """Arquivo que nao e o CSV esperado, ou que nao corresponde ao pleito/UF pedidos."""


class ConciliacaoDivergente(RuntimeError):
    """Ha local do BU sem metadado ou ambiguo: a importacao e recusada."""


@dataclass(frozen=True)
class LocalFonte:
    municipio: str
    zona: str
    codigo: str
    nome: str
    endereco: str | None
    bairro: str | None
    secoes: int
    realocadas: int


@dataclass
class FonteLocais:
    uf: str
    data_eleicao: str
    turno: int
    gerada_em: datetime | None
    sha256: str                      # do arquivo oficial inteiro (todos os turnos)
    linhas: int                      # linhas do turno pedido
    linhas_arquivo: int = 0          # linhas do arquivo, de todos os turnos
    turnos: dict[str, int] = field(default_factory=dict)    # NR_TURNO -> linhas no arquivo
    locais: dict[tuple[str, str, str], LocalFonte] = field(default_factory=dict)
    # chave -> conjunto de (nome, endereco) distintos encontrados para o mesmo codigo
    ambiguos: dict[tuple[str, str, str], list] = field(default_factory=dict)
    invalidas: int = 0


def _texto(valor: str | None) -> str | None:
    limpo = " ".join((valor or "").split())
    return None if limpo in _VAZIOS else limpo


def ler_fonte(conteudo: bytes, uf: str, turno: int) -> FonteLocais:
    """Le o CSV oficial de UMA UF e agrupa as linhas (por secao) de UM turno em locais ORIGINAIS."""
    if isinstance(turno, bool) or not isinstance(turno, int) or turno <= 0:
        raise ValueError(f"turno deve ser um inteiro positivo (recebido: {turno!r}).")
    if len(conteudo) > MAX_CSV_BYTES:
        raise FonteInvalida(f"CSV com {len(conteudo)} bytes excede o limite.")
    texto = conteudo.decode("latin-1")          # codificacao documentada no leia-me do TSE
    leitor = csv.DictReader(io.StringIO(texto), delimiter=";", quotechar='"')
    faltando = [c for c in COLUNAS if c not in (leitor.fieldnames or [])]
    if faltando:
        raise FonteInvalida(f"CSV sem as colunas esperadas: {', '.join(faltando)}")
    uf = uf.lower()
    grupos: dict[tuple[str, str, str], dict] = {}
    cabecalho, linhas, invalidas = None, 0, 0
    turnos: dict[str, int] = {}
    for linha in leitor:
        # O turno e o primeiro filtro: linha de outro turno nao entra em contagem nenhuma.
        turno_da_linha = (linha["NR_TURNO"] or "").strip()
        turnos[turno_da_linha] = turnos.get(turno_da_linha, 0) + 1
        if not turno_da_linha.isdigit() or int(turno_da_linha) != turno:
            continue
        linhas += 1
        municipio, zona = linha["CD_MUNICIPIO"].strip(), linha["NR_ZONA"].strip()
        codigo, nome = linha["NR_LOCAL_VOTACAO_ORIGINAL"].strip(), _texto(linha["NM_LOCAL_VOTACAO_ORIGINAL"])
        if (linha["SG_UF"].strip().lower() != uf or not municipio.isdigit() or not zona.isdigit()
                or not codigo.isdigit() or int(codigo) <= 0 or not nome):
            invalidas += 1
            continue
        cabecalho = cabecalho or linha
        chave = (municipio.zfill(5), zona.zfill(4), codigo.zfill(4))
        grupo = grupos.setdefault(chave, {"variantes": {}, "bairros": {}, "secoes": 0, "realocadas": 0})
        variante = (nome, _texto(linha["DS_ENDERECO_LOCVT_ORIGINAL"]))
        grupo["variantes"][variante] = grupo["variantes"].get(variante, 0) + 1
        grupo["secoes"] += 1
        if linha["NR_LOCAL_VOTACAO"].strip() == codigo:
            bairro = _texto(linha["NM_BAIRRO"])
            if bairro:
                grupo["bairros"][bairro] = grupo["bairros"].get(bairro, 0) + 1
        else:
            grupo["realocadas"] += 1
    if not linhas:
        encontrados = ", ".join(sorted(t or "(vazio)" for t in turnos)) or "nenhum"
        raise FonteInvalida(
            f"CSV sem nenhuma linha do turno {turno} (turnos no arquivo: {encontrados}).")
    if cabecalho is None:
        raise FonteInvalida(
            f"CSV sem nenhuma linha valida da UF {uf.upper()} no turno {turno}.")
    try:
        gerada = datetime.strptime(f"{cabecalho['DT_GERACAO']} {cabecalho['HH_GERACAO']}",
                                   "%d/%m/%Y %H:%M:%S").replace(tzinfo=BRASILIA)
    except ValueError:
        gerada = None
    fonte = FonteLocais(uf=uf, data_eleicao=cabecalho["DT_ELEICAO"].strip(),
                        turno=turno, gerada_em=gerada,
                        sha256=hashlib.sha256(conteudo).hexdigest(), linhas=linhas,
                        linhas_arquivo=sum(turnos.values()), turnos=dict(sorted(turnos.items())),
                        invalidas=invalidas)
    for chave, grupo in grupos.items():
        if len(grupo["variantes"]) > 1:
            # Mesmo codigo oficial com nomes/enderecos diferentes: nao se escolhe um.
            fonte.ambiguos[chave] = sorted(grupo["variantes"], key=lambda v: (v[0], v[1] or ""))
            continue
        (nome, endereco), = grupo["variantes"]
        bairro = grupo["bairros"] and (None if len(grupo["bairros"]) > 1
                                       else next(iter(grupo["bairros"])))
        fonte.locais[chave] = LocalFonte(
            municipio=chave[0], zona=chave[1], codigo=chave[2], nome=nome[:200],
            endereco=endereco[:300] if endereco else None,
            bairro=bairro[:120] if bairro else None,
            secoes=grupo["secoes"], realocadas=grupo["realocadas"])
    return fonte


def locais_do_bu(session: Session, origem: str, pleito: str, uf: str) -> set[tuple[str, str, str]]:
    """(municipio, zona, codigo) de cada local presente nos BUs correntes da UF."""
    rows = session.execute(
        select(TseSecao.municipio_codigo, TseSecao.zona, TseBoletimUrna.local_votacao)
        .join(TseBuControle, TseBuControle.secao_id == TseSecao.id)
        .join(TseBoletimUrna, TseBoletimUrna.id == TseBuControle.boletim_id)
        .where(TseSecao.origem == origem, TseSecao.pleito == str(pleito), TseSecao.uf == uf.lower(),
               TseSecao.eh_principal.is_(True), TseBoletimUrna.local_votacao.is_not(None))
        .distinct())
    return {(m, z, f"{int(c):04d}") for m, z, c in rows}


def conciliar(session: Session, fonte: FonteLocais, origem: str, pleito: str) -> dict:
    """BU x CSV pela chave oficial. Somente leitura."""
    bu = locais_do_bu(session, origem, pleito, fonte.uf)
    csv_ok, ambiguos = set(fonte.locais), set(fonte.ambiguos)
    return {
        "bu": len(bu), "csv": len(csv_ok) + len(ambiguos),
        "match": len(bu & csv_ok),
        "bu_only": sorted(bu - csv_ok - ambiguos),
        "csv_only": sorted((csv_ok | ambiguos) - bu),
        "ambiguous": sorted(bu & ambiguos),
    }


def _validar_pleito(session: Session, fonte: FonteLocais, origem: str, pleito: str) -> None:
    eleicoes = list(session.scalars(select(TseEleicao).where(
        TseEleicao.origem == origem, TseEleicao.pleito == str(pleito))))
    if not eleicoes:
        raise FonteInvalida(f"Pleito {pleito} ({origem}) não existe na base de apuração.")
    datas = {e.data_eleicao.strftime("%d/%m/%Y") for e in eleicoes if e.data_eleicao}
    if datas and fonte.data_eleicao not in datas:
        raise FonteInvalida(
            f"CSV da eleição de {fonte.data_eleicao}, mas o pleito {pleito} é de {', '.join(sorted(datas))}.")


def importar(session: Session, conteudo: bytes, *, origem: str, pleito: str, uf: str,
             turno: int, fonte_url: str | None = None, dry_run: bool = False) -> dict:
    """Importa (ou atualiza) os metadados dos locais de uma UF, de UM turno. Idempotente.

    `turno` e o da eleicao que esta sendo enriquecida (o pleito). `source_hash`
    continua sendo o do arquivo oficial inteiro, nao o de um recorte por turno.

    A -> A: nada muda. A -> B: os campos que mudaram sao atualizados no mesmo
    registro (a chave e o pleito: outra eleicao e outro registro). Recusa
    gravar se algum local dos BUs ficar sem metadado ou ambiguo.
    """
    fonte = ler_fonte(conteudo, uf, turno)
    _validar_pleito(session, fonte, origem, pleito)
    conciliacao = conciliar(session, fonte, origem, pleito)
    relatorio = {
        "origem": origem, "pleito": str(pleito), "uf": fonte.uf, "fonte": FONTE,
        "fonte_url": fonte_url, "source_hash": fonte.sha256,
        "fonte_gerada_em": fonte.gerada_em.isoformat() if fonte.gerada_em else None,
        "data_eleicao": fonte.data_eleicao, "turno": fonte.turno, "linhas": fonte.linhas,
        "linhas_arquivo": fonte.linhas_arquivo, "turnos_no_arquivo": fonte.turnos,
        "locais": len(fonte.locais),
        "invalidos": fonte.invalidas, "ambiguos_na_fonte": len(fonte.ambiguos),
        "conciliacao": {**conciliacao, "bu_only": conciliacao["bu_only"][:50],
                        "csv_only": conciliacao["csv_only"][:50]},
        "inseridos": 0, "atualizados": 0, "inalterados": 0, "dry_run": dry_run,
    }
    if conciliacao["bu_only"] or conciliacao["ambiguous"]:
        raise ConciliacaoDivergente(
            f"{len(conciliacao['bu_only'])} local(is) do BU sem metadado e "
            f"{len(conciliacao['ambiguous'])} ambíguo(s): nada foi gravado.")
    if dry_run:
        return relatorio

    existentes = {(r.municipio_codigo, r.zona, r.codigo_local): r for r in session.scalars(
        select(TseLocalVotacao).where(TseLocalVotacao.origem == origem,
                                      TseLocalVotacao.pleito == str(pleito),
                                      TseLocalVotacao.uf == fonte.uf))}
    for chave, local in fonte.locais.items():
        dados = {"nome": local.nome, "endereco": local.endereco, "bairro": local.bairro,
                 "secoes_cadastradas": local.secoes, "secoes_realocadas": local.realocadas}
        row = existentes.get(chave)
        if row is None:
            session.add(TseLocalVotacao(
                origem=origem, pleito=str(pleito), uf=fonte.uf, municipio_codigo=local.municipio,
                zona=local.zona, codigo_local=local.codigo, fonte=FONTE, fonte_url=fonte_url,
                fonte_gerada_em=fonte.gerada_em, source_hash=fonte.sha256, **dados))
            relatorio["inseridos"] += 1
        elif any(getattr(row, campo) != valor for campo, valor in dados.items()):
            for campo, valor in dados.items():
                setattr(row, campo, valor)
            row.fonte, row.fonte_url = FONTE, fonte_url
            row.fonte_gerada_em, row.source_hash = fonte.gerada_em, fonte.sha256
            relatorio["atualizados"] += 1
        else:
            # Mesmo conteudo: o registro (e a marca da fonte que o gravou) fica como esta.
            relatorio["inalterados"] += 1
    session.flush()
    return relatorio


def cobertura(session: Session, origem: str, pleito: str, uf: str) -> dict:
    """Locais dos BUs x metadados gravados (depois da importacao)."""
    bu = locais_do_bu(session, origem, pleito, uf)
    gravados = {(r.municipio_codigo, r.zona, r.codigo_local): r for r in session.scalars(
        select(TseLocalVotacao).where(TseLocalVotacao.origem == origem,
                                      TseLocalVotacao.pleito == str(pleito),
                                      TseLocalVotacao.uf == uf.lower()))}
    com = bu & set(gravados)
    return {"locais_bu": len(bu), "com_metadado": len(com), "sem_metadado": sorted(bu - set(gravados)),
            "sem_nome": sum(1 for k in com if not gravados[k].nome),
            "sem_endereco": sum(1 for k in com if not gravados[k].endereco),
            "sem_bairro": sum(1 for k in com if not gravados[k].bairro),
            "com_secoes_realocadas": sum(1 for k in com if gravados[k].secoes_realocadas),
            "metadados_gravados": len(gravados)}
