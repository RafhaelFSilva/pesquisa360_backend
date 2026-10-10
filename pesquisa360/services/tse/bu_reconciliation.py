"""Conferencia BU x EA20 por zona (ADR-094). SOMENTE LEITURA.

Soma os BUs correntes das secoes de uma zona e compara com o EA20 da zona.
Nunca altera, corrige ou substitui valor algum: o EA20 continua sendo a fonte
oficial da zona e o BU a fonte oficial da secao.

- NOT_COMPARABLE: falta o EA20 da zona ou nao ha BU;
- PARTIAL: nem toda urna da zona tem BU ingerido, ou o EA20 da zona ainda nao
  fechou -- as duas fontes nao cobrem o mesmo conjunto de secoes. Nao e erro;
- MATCH / DIVERGENT: cobertura igual (zona totalizada no EA20 e BU de todas as
  urnas). So entao uma diferenca de valor e divergencia.

Como o TSE totaliza (identidades verificadas com os 451 BUs oficiais da zona
0002 de Macapa, 04/10/2026, nos cinco cargos):

- voto nominal do BU em votavel que NAO esta na lista de candidatos do EA20
  entra nos NULOS do EA20:  EA20.nulos = BU.nulos + BU.fora_da_lista;
- voto de candidato listado conta no candidato mesmo quando anulado sub
  judice (o EA20 so o retira de nominais/validos), entao a comparacao e feita
  candidato a candidato, e nao pelo total de nominais.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pesquisa360.db.models_tse import (
    TseBuCargo, TseBuControle, TseBuVoto, TseCandidato, TseCargo, TseEleicao, TsePartido,
    TseResultadoCandidato, TseResultadoPartido, TseSecao, TseTotalizacao,
)

from .repository import TseRepository, abrangencia_key

MATCH, PARTIAL, DIVERGENT, NOT_COMPARABLE = "MATCH", "PARTIAL", "DIVERGENT", "NOT_COMPARABLE"
MAX_DIVERGENCIAS = 50


def boletins_correntes(origem: str, pleito: str, uf: str, municipio: str, zona: str):
    """Subconsulta: id do BU corrente de cada secao principal da zona."""
    return (select(TseBuControle.boletim_id)
            .join(TseSecao, TseSecao.id == TseBuControle.secao_id)
            .where(TseSecao.origem == origem, TseSecao.pleito == str(pleito), TseSecao.uf == uf,
                   TseSecao.municipio_codigo == municipio, TseSecao.zona == zona,
                   TseBuControle.boletim_id.is_not(None)))


def reconciliar_zona(session: Session, eleicao: TseEleicao, cargo: TseCargo, uf: str,
                     municipio: str, zona: str) -> dict:
    repo = TseRepository(session)
    abr = repo.get_abrangencia(eleicao.id, abrangencia_key(uf, municipio, zona))
    total: TseTotalizacao | None = repo.latest_totalizacao(abr.id, cargo.id) if abr else None
    principais = session.scalar(select(func.count(TseSecao.id)).where(
        TseSecao.origem == eleicao.origem, TseSecao.pleito == eleicao.pleito, TseSecao.uf == uf,
        TseSecao.municipio_codigo == municipio, TseSecao.zona == zona,
        TseSecao.eh_principal.is_(True))) or 0
    correntes = boletins_correntes(eleicao.origem, eleicao.pleito, uf, municipio, zona)
    com_bu, comparecimento, _nominais, legenda, brancos, nulos = session.execute(
        select(func.count(TseBuCargo.id), func.sum(TseBuCargo.comparecimento),
               func.sum(TseBuCargo.votos_nominais), func.sum(TseBuCargo.votos_legenda),
               func.sum(TseBuCargo.votos_brancos), func.sum(TseBuCargo.votos_nulos))
        .where(TseBuCargo.boletim_id.in_(correntes), TseBuCargo.cargo_id == cargo.id,
               TseBuCargo.eleicao_id == eleicao.id)).one()

    resultado = {
        "status": NOT_COMPARABLE, "motivo": None,
        "municipio_codigo": municipio, "zona": zona,
        "cargo": {"codigo": cargo.codigo, "nome": cargo.nome},
        "cobertura": {
            "secoes_principais": principais, "com_bu": com_bu,
            "ea20_secoes_total": total.secoes_total if total else None,
            "ea20_secoes_totalizadas": total.secoes_totalizadas if total else None,
        },
        "campos": [], "divergencias": [],
    }
    if total is None:
        resultado["motivo"] = "SEM_EA20_DA_ZONA"
        return resultado
    if not com_bu:
        resultado["motivo"] = "SEM_BU"
        return resultado
    if com_bu < principais:
        resultado.update(status=PARTIAL, motivo="BU_FALTANDO")
        return resultado
    if total.secoes_totalizadas != total.secoes_total or total.secoes_total != com_bu:
        # O EA20 e os BUs nao cobrem o mesmo conjunto de secoes neste instante.
        resultado.update(status=PARTIAL, motivo="JANELAS_DIFERENTES")
        return resultado

    votos_bu = {(tipo, cand_id, partido_id): int(soma) for tipo, cand_id, partido_id, soma in
                session.execute(
                    select(TseBuVoto.tipo, TseBuVoto.candidato_id, TseBuVoto.partido_id,
                           func.sum(TseBuVoto.votos))
                    .join(TseBuCargo, TseBuCargo.id == TseBuVoto.bu_cargo_id)
                    .where(TseBuCargo.boletim_id.in_(correntes), TseBuCargo.cargo_id == cargo.id,
                           TseBuCargo.eleicao_id == eleicao.id)
                    .group_by(TseBuVoto.tipo, TseBuVoto.candidato_id, TseBuVoto.partido_id))}
    ea20_cand = {cid: (votos, numero, nome) for cid, votos, numero, nome in session.execute(
        select(TseResultadoCandidato.candidato_id, TseResultadoCandidato.votos,
               TseCandidato.numero, TseCandidato.nome_urna)
        .join(TseCandidato, TseCandidato.id == TseResultadoCandidato.candidato_id)
        .where(TseResultadoCandidato.totalizacao_id == total.id))}
    ea20_leg = {pid: (votos or 0, numero) for pid, votos, numero in session.execute(
        select(TseResultadoPartido.partido_id, TseResultadoPartido.votos_legenda,
               TsePartido.numero)
        .join(TsePartido, TsePartido.id == TseResultadoPartido.partido_id)
        .where(TseResultadoPartido.totalizacao_id == total.id))}

    bu_cand: dict[int, int] = {}
    bu_legenda: dict[int | None, int] = {}
    fora_da_lista = 0
    for (tipo, cand_id, partido_id), soma in votos_bu.items():
        if tipo == "LEGENDA":
            bu_legenda[partido_id] = bu_legenda.get(partido_id, 0) + soma
        elif cand_id in ea20_cand:
            bu_cand[cand_id] = bu_cand.get(cand_id, 0) + soma
        else:
            # Votavel fora da lista de candidatos do EA20 da zona.
            fora_da_lista += soma

    def campo(nome, ea20, bu, **extra):
        if ea20 is None:
            return
        resultado["campos"].append(
            {"campo": nome, "ea20": ea20, "bu": int(bu or 0),
             "diferenca": ea20 - int(bu or 0), **extra})

    campo("comparecimento", total.comparecimento, comparecimento)
    campo("votos_brancos", total.votos_brancos, brancos)
    # O TSE soma aos nulos o voto dado a votavel fora da lista de candidatos.
    campo("votos_nulos", total.votos_nulos, int(nulos or 0) + fora_da_lista,
          bu_nulos=int(nulos or 0), bu_fora_da_lista=fora_da_lista)
    campo("votos_legenda", total.votos_legenda, legenda)
    campo("votos_dos_candidatos", sum(v for v, _n, _m in ea20_cand.values()),
          sum(bu_cand.values()))
    for cid in sorted(set(ea20_cand) | set(bu_cand)):
        ea20, numero, nome = ea20_cand.get(cid, (0, None, None))
        bu = bu_cand.get(cid, 0)
        if ea20 != bu:
            resultado["divergencias"].append(
                {"tipo": "CANDIDATO", "numero": numero, "nome": nome, "ea20": ea20, "bu": bu})
    for pid in sorted(set(ea20_leg) | set(bu_legenda), key=lambda v: v or 0):
        ea20, numero = ea20_leg.get(pid, (0, None))
        bu = bu_legenda.get(pid, 0)
        if ea20 != bu:
            resultado["divergencias"].append(
                {"tipo": "LEGENDA", "numero": numero, "nome": None, "ea20": ea20, "bu": bu})

    difere = bool(resultado["divergencias"]) or any(c["diferenca"] for c in resultado["campos"])
    resultado["total_divergencias"] = len(resultado["divergencias"])
    resultado["divergencias"] = resultado["divergencias"][:MAX_DIVERGENCIAS]
    resultado["status"] = DIVERGENT if difere else MATCH
    return resultado


def conferir_zonas(session: Session, origem: str, pleito: str, uf: str,
                   zonas: list[tuple[str, str]], cargos: tuple[str, ...]) -> dict[str, int]:
    """Contadores de conferencia das zonas tocadas por um lote (para o log do worker)."""
    contagem = {"bu_reconciled_match": 0, "bu_reconciled_partial": 0,
                "bu_reconciled_divergent": 0}
    if not zonas:
        return contagem
    chave = {MATCH: "bu_reconciled_match", PARTIAL: "bu_reconciled_partial",
             DIVERGENT: "bu_reconciled_divergent"}
    for codigo in cargos:
        cargo = session.scalars(select(TseCargo).where(TseCargo.codigo == codigo)).first()
        if cargo is None:
            continue
        eleicao = session.scalars(
            select(TseEleicao).join(TseTotalizacao, TseTotalizacao.eleicao_id == TseEleicao.id)
            .where(TseEleicao.origem == origem, TseEleicao.pleito == str(pleito),
                   TseTotalizacao.cargo_id == cargo.id).limit(1)).first()
        if eleicao is None:
            continue
        for municipio, zona in zonas:
            status = reconciliar_zona(session, eleicao, cargo, uf, municipio, zona)["status"]
            if status in chave:
                contagem[chave[status]] += 1
    return contagem
