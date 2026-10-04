"""Leituras analiticas da Apuracao TSE para a API (somente consulta).

Devolve dicionarios serializaveis -- nunca ORM, nunca o payload bruto do TSE.
Tudo e FACTUAL: ordenacao por votos e somas. Nao ha previsao de vagas nem
inferencia de eleito; `situacao`/`eleito` sao repassados como o TSE publicou.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from pesquisa360.db.models_tse import (
    TseAbrangencia, TseCandidato, TseCargo, TseEleicao, TseFederacao, TsePartido,
    TseResultadoCandidato, TseResultadoPartido, TseTotalizacao,
)

from . import reconciliation
from .normalization import BRASILIA, cargo_code
from .repository import TseRepository, abrangencia_key


class TseNotFound(LookupError):
    """Recurso inexistente na base TSE ingerida (vira 404 na API)."""


def _dt(value: datetime | None) -> str | None:
    """ISO-8601 em UTC. SQLite devolve sem fuso (horario de Brasilia gravado)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=BRASILIA)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _pct(parte, total) -> float | None:
    return round(100 * parte / total, 2) if total else None


def _eleicao(e: TseEleicao) -> dict:
    return {
        "id": e.id, "origem": e.origem, "pleito": e.pleito,
        "codigo_eleicao": e.codigo_eleicao, "nome": e.nome, "turno": e.turno,
        "data_eleicao": e.data_eleicao.isoformat() if e.data_eleicao else None,
    }


def _totalizacao(t: TseTotalizacao | None) -> dict | None:
    if t is None:
        return None
    return {
        "idg": t.idg, "gerado_em": _dt(t.gerado_em),
        "ultima_totalizacao": _dt(t.ultima_totalizacao), "capturado_em": _dt(t.capturado_em),
        "andamento": t.andamento, "totalizacao_final": t.totalizacao_final == "s",
        "secoes_total": t.secoes_total, "secoes_totalizadas": t.secoes_totalizadas,
        "percentual_secoes": _pct(t.secoes_totalizadas or 0, t.secoes_total),
        "eleitores": t.eleitores, "comparecimento": t.comparecimento,
        "abstencoes": t.abstencoes, "votos_validos": t.votos_validos,
        "votos_nominais": t.votos_nominais, "votos_legenda": t.votos_legenda,
        "votos_brancos": t.votos_brancos, "votos_nulos": t.votos_nulos,
        "votos_anulados_sub_judice": t.votos_anulados_sub_judice,
    }


def _abrangencia(a: TseAbrangencia) -> dict:
    return {"tipo": a.tipo, "uf": a.uf, "municipio_codigo": a.municipio_codigo,
            "municipio_nome": a.municipio_nome, "zona": a.zona}


def _voto_valido(destinacao: str | None) -> bool:
    return destinacao is None or destinacao.casefold().startswith("válido")


class TseAnalytics:
    def __init__(self, session: Session):
        self.session = session
        self.repo = TseRepository(session)

    # ------------------------------------------------------------- resolucao
    def eleicao(self, eleicao_id: int, origem: str | None = None) -> TseEleicao:
        row = self.session.get(TseEleicao, eleicao_id)
        # `origem` informada e conferida: pedir OFICIAL nunca devolve SIMULADO.
        if row is None or (origem and row.origem != origem):
            raise TseNotFound("Eleição não encontrada.")
        return row

    def _cargo(self, codigo: str) -> TseCargo:
        try:
            codigo = cargo_code(codigo)
        except (TypeError, ValueError):
            raise TseNotFound("Cargo não encontrado.") from None
        row = self.session.scalars(select(TseCargo).where(TseCargo.codigo == codigo)).first()
        if row is None:
            raise TseNotFound("Cargo não encontrado.")
        return row

    def _abrangencia(self, eleicao: TseEleicao, uf: str, municipio: str | None = None,
                     zona: str | None = None) -> TseAbrangencia:
        if zona and not municipio:
            raise ValueError("Zona exige município.")
        row = self.repo.get_abrangencia(
            eleicao.id, abrangencia_key(uf.lower(), municipio or None, zona or None))
        if row is None:
            raise TseNotFound("Abrangência não encontrada para esta eleição.")
        return row

    def _candidato(self, eleicao: TseEleicao, sqcand: str) -> TseCandidato:
        row = self.repo.find_candidato(eleicao.id, sqcand=sqcand)
        if row is None:
            raise TseNotFound("Candidato não encontrado nesta eleição.")
        return row

    # --------------------------------------------------------------- eleicoes
    def listar_eleicoes(self, origem: str | None = None) -> list[dict]:
        """Eleicoes que ja tem resultado ingerido, com UFs e cargos disponiveis."""
        query = select(TseEleicao).order_by(TseEleicao.origem, TseEleicao.codigo_eleicao)
        if origem:
            query = query.where(TseEleicao.origem == origem)
        saida = []
        for e in self.session.scalars(query):
            ufs = sorted(self.session.scalars(
                select(TseAbrangencia.uf).join(
                    TseTotalizacao, TseTotalizacao.abrangencia_id == TseAbrangencia.id)
                .where(TseAbrangencia.eleicao_id == e.id, TseAbrangencia.tipo == "UF")
                .distinct()))
            if not ufs:
                continue
            saida.append({**_eleicao(e), "ufs": ufs, "cargos": self._cargos_da_eleicao(e.id)})
        return saida

    def _cargos_da_eleicao(self, eleicao_id: int) -> list[dict]:
        rows = self.session.execute(
            select(TseCargo.codigo, TseCargo.nome)
            .join(TseTotalizacao, TseTotalizacao.cargo_id == TseCargo.id)
            .where(TseTotalizacao.eleicao_id == eleicao_id).distinct()
            .order_by(TseCargo.codigo))
        return [{"codigo": codigo, "nome": nome} for codigo, nome in rows]

    def resumo(self, eleicao_id: int, uf: str, origem: str | None = None) -> dict:
        eleicao = self.eleicao(eleicao_id, origem)
        abr = self._abrangencia(eleicao, uf)
        cargos = []
        for item in self._cargos_da_eleicao(eleicao.id):
            cargo = self._cargo(item["codigo"])
            total = self.repo.latest_totalizacao(abr.id, cargo.id)
            if total is not None:
                cargos.append({**item, "vagas": total.vagas, "totalizacao": _totalizacao(total)})
        # A totalizacao de referencia e a mais recente entre os cargos da UF.
        referencia = max((c["totalizacao"] for c in cargos),
                         key=lambda t: t["gerado_em"] or "", default=None)
        return {
            "eleicao": _eleicao(eleicao), "origem": eleicao.origem, "uf": abr.uf,
            "cargos": cargos,
            "ultima_atualizacao": referencia["gerado_em"] if referencia else None,
            "totalizacao": referencia,
        }

    # ------------------------------------------------------------------ cargo
    def _linhas(self, total: TseTotalizacao) -> list[dict]:
        """Candidatos da totalizacao, ordenados por votos (ordem factual)."""
        rows = self.session.execute(
            select(TseCandidato, TsePartido, TseFederacao, TseResultadoCandidato)
            .join(TseResultadoCandidato, TseResultadoCandidato.candidato_id == TseCandidato.id)
            .join(TsePartido, TsePartido.id == TseCandidato.partido_id)
            .outerjoin(TseFederacao, TseFederacao.id == TsePartido.federacao_id)
            .where(TseResultadoCandidato.totalizacao_id == total.id)
            .order_by(TseResultadoCandidato.votos.desc(), TseCandidato.nome_urna,
                      TseCandidato.sqcand))
        return [{
            "posicao": posicao, "sqcand": cand.sqcand, "numero": cand.numero,
            "nome": cand.nome, "nome_urna": cand.nome_urna,
            "partido": {"numero": par.numero, "sigla": par.sigla, "nome": par.nome},
            "federacao": ({"numero": fed.numero, "sigla": fed.sigla, "nome": fed.nome}
                          if fed else None),
            "votos": res.votos,
            "percentual": float(res.percentual) if res.percentual is not None else None,
            "situacao": res.situacao,
            "eleito": {"s": True, "n": False}.get(res.eleito),
            "destinacao_voto": res.destinacao_voto,
            "voto_valido": _voto_valido(res.destinacao_voto),
        } for posicao, (cand, par, fed, res) in enumerate(rows, start=1)]

    def _contexto_cargo(self, eleicao_id, cargo_codigo, uf, municipio, zona, origem):
        eleicao = self.eleicao(eleicao_id, origem)
        cargo = self._cargo(cargo_codigo)
        abr = self._abrangencia(eleicao, uf, municipio, zona)
        total = self.repo.latest_totalizacao(abr.id, cargo.id)
        if total is None:
            raise TseNotFound("Não há totalização deste cargo para a abrangência.")
        return eleicao, cargo, abr, total

    def resultado_cargo(self, eleicao_id: int, cargo_codigo: str, uf: str,
                        municipio: str | None = None, zona: str | None = None,
                        origem: str | None = None, limite: int | None = None) -> dict:
        eleicao, cargo, abr, total = self._contexto_cargo(
            eleicao_id, cargo_codigo, uf, municipio, zona, origem)
        candidatos = self._linhas(total)
        return {
            "eleicao": _eleicao(eleicao), "origem": eleicao.origem,
            "cargo": {"codigo": cargo.codigo, "nome": cargo.nome, "vagas": total.vagas},
            "abrangencia": _abrangencia(abr), "totalizacao": _totalizacao(total),
            "total_candidatos": len(candidatos),
            "candidatos": candidatos[:limite] if limite else candidatos,
        }

    def nominatas(self, eleicao_id: int, cargo_codigo: str, uf: str,
                  municipio: str | None = None, zona: str | None = None,
                  origem: str | None = None) -> dict:
        """Partido isolado ou federacao -> candidatos, com posicao ordinal na nominata."""
        eleicao, cargo, abr, total = self._contexto_cargo(
            eleicao_id, cargo_codigo, uf, municipio, zona, origem)
        legenda = {
            par.numero: (res.votos_legenda or 0) if _voto_valido(res.destinacao_voto) else 0
            for par, res in self.session.execute(
                select(TsePartido, TseResultadoPartido)
                .join(TseResultadoPartido, TseResultadoPartido.partido_id == TsePartido.id)
                .where(TseResultadoPartido.totalizacao_id == total.id))
        }
        grupos: dict[tuple, dict] = {}
        for linha in self._linhas(total):
            fed, par = linha["federacao"], linha["partido"]
            chave = ("FEDERACAO", fed["numero"]) if fed else ("PARTIDO", par["numero"])
            grupo = grupos.setdefault(chave, {
                "tipo": chave[0], "numero": chave[1],
                "sigla": (fed or par)["sigla"], "nome": (fed or par)["nome"],
                "partidos": {}, "candidatos": [],
            })
            grupo["partidos"][par["numero"]] = par
            grupo["candidatos"].append(linha)
        nominatas = []
        for grupo in grupos.values():
            candidatos = [{**c, "posicao_geral": c["posicao"], "posicao": i}
                          for i, c in enumerate(grupo["candidatos"], start=1)]
            validos = sum(c["votos"] for c in candidatos if c["voto_valido"])
            votos_legenda = sum(legenda.get(numero, 0) for numero in grupo["partidos"])
            nominatas.append({
                **grupo, "partidos": sorted(grupo["partidos"].values(), key=lambda p: p["numero"]),
                "candidatos": candidatos,
                "votos_nominais": sum(c["votos"] for c in candidatos),
                "votos_nominais_validos": validos,
                "votos_legenda": votos_legenda,
                "total": validos + votos_legenda,
            })
        nominatas.sort(key=lambda n: (-n["total"], n["sigla"]))
        return {
            "eleicao": _eleicao(eleicao), "origem": eleicao.origem,
            "cargo": {"codigo": cargo.codigo, "nome": cargo.nome, "vagas": total.vagas},
            "abrangencia": _abrangencia(abr), "totalizacao": _totalizacao(total),
            "nominatas": nominatas,
        }

    # -------------------------------------------------------------- candidato
    def _identidade(self, cand: TseCandidato) -> dict:
        fed = cand.partido.federacao
        return {
            "sqcand": cand.sqcand, "numero": cand.numero, "nome": cand.nome,
            "nome_urna": cand.nome_urna, "uf": cand.uf,
            "cargo": {"codigo": cand.cargo.codigo, "nome": cand.cargo.nome},
            "partido": {"numero": cand.partido.numero, "sigla": cand.partido.sigla,
                        "nome": cand.partido.nome},
            "federacao": ({"numero": fed.numero, "sigla": fed.sigla, "nome": fed.nome}
                          if fed else None),
        }

    def candidato(self, sqcand: str, eleicao_id: int, origem: str | None = None) -> dict:
        eleicao = self.eleicao(eleicao_id, origem)
        cand = self._candidato(eleicao, sqcand)
        abr = self._abrangencia(eleicao, cand.uf)
        total = self.repo.latest_totalizacao(abr.id, cand.cargo_id)
        linha = next((l for l in self._linhas(total) if l["sqcand"] == cand.sqcand), None) \
            if total else None
        return {
            "eleicao": _eleicao(eleicao), "origem": eleicao.origem,
            "candidato": self._identidade(cand), "abrangencia": _abrangencia(abr),
            "consolidado": None if linha is None else {
                k: linha[k] for k in ("votos", "percentual", "situacao", "eleito",
                                      "destinacao_voto", "voto_valido", "posicao")},
            "totalizacao": _totalizacao(total),
        }

    def territorio(self, sqcand: str, eleicao_id: int, group_by: str,
                   municipio: str | None = None, origem: str | None = None) -> dict:
        if group_by not in ("municipio", "zona"):
            raise ValueError("group_by deve ser 'municipio' ou 'zona'.")
        eleicao = self.eleicao(eleicao_id, origem)
        cand = self._candidato(eleicao, sqcand)
        abr_uf = self._abrangencia(eleicao, cand.uf)
        votos = self.repo.candidate_votes(cand.id)
        total_uf = next((v["votos"] for v in votos if v["abrangencia_id"] == abr_uf.id), None)

        if group_by == "municipio":
            pai, linhas = abr_uf, [v for v in votos if v["tipo"] == "MUNICIPIO"]
        else:
            linhas = [v for v in votos if v["tipo"] == "ZONA"
                      and (not municipio or v["municipio_codigo"] == municipio)]
            pai = self._abrangencia(eleicao, cand.uf, municipio) if municipio else None
        nomes = {a.municipio_codigo: a.municipio_nome
                 for a in self.repo.children(abr_uf.id)}
        linhas.sort(key=lambda v: (-v["votos"], v["chave"]))
        itens = [{
            "posicao": posicao, "municipio_codigo": v["municipio_codigo"],
            "municipio_nome": v["municipio_nome"] or nomes.get(v["municipio_codigo"]),
            "zona": v["zona"], "votos": v["votos"],
            "percentual_dos_votos_do_candidato": _pct(v["votos"], total_uf),
            "secoes_total": v["secoes_total"], "secoes_totalizadas": v["secoes_totalizadas"],
            "andamento": v["andamento"], "idg": v["idg"], "gerado_em": _dt(v["gerado_em"]),
        } for posicao, v in enumerate(linhas, start=1)]

        reconc = None
        if pai is not None:
            r = reconciliation.reconcile_abrangencia(self.repo, cand.id, pai)
            reconc = {"status": r["status"], "oficial": r["official"]["votes"],
                      "soma_das_partes": r["derived"]["votes"], "diferenca": r["difference"],
                      "partes": r["derived"]["parts"],
                      "partes_esperadas": r["derived"]["expected_parts"]}
        return {
            "eleicao": _eleicao(eleicao), "origem": eleicao.origem,
            "candidato": self._identidade(cand), "group_by": group_by,
            "municipio_codigo": municipio if group_by == "zona" else None,
            "total_votos_uf": total_uf, "itens": itens, "reconciliacao": reconc,
            # Granularidade por secao depende do BU oficial (ADR-078).
            "secao_disponivel": False,
        }

    def evolucao(self, sqcand: str, eleicao_id: int, municipio: str | None = None,
                 zona: str | None = None, origem: str | None = None) -> dict:
        eleicao = self.eleicao(eleicao_id, origem)
        cand = self._candidato(eleicao, sqcand)
        abr = self._abrangencia(eleicao, cand.uf, municipio, zona)
        pontos = [{
            "timestamp": _dt(h["ultima_totalizacao"] or h["gerado_em"]),
            "gerado_em": _dt(h["gerado_em"]), "idg": h["idg"], "votos": h["votos"],
            "percentual": h["percentual"], "andamento": h["andamento"],
            "secoes_totalizadas": h["secoes_totalizadas"], "secoes_total": h["secoes_total"],
            "percentual_secoes": _pct(h["secoes_totalizadas"] or 0, h["secoes_total"]),
        } for h in self.repo.candidate_history(cand.id, abr.id)]
        return {
            "eleicao": _eleicao(eleicao), "origem": eleicao.origem,
            "candidato": self._identidade(cand), "abrangencia": _abrangencia(abr),
            "pontos": pontos,
        }
