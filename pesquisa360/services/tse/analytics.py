"""Leituras analiticas da Apuracao TSE para a API (somente consulta).

Devolve dicionarios serializaveis -- nunca ORM, nunca o payload bruto do TSE.
Tudo e FACTUAL: ordenacao por votos e somas. Nao ha previsao de vagas nem
inferencia de eleito; `situacao`/`eleito` sao repassados como o TSE publicou.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from pesquisa360.db.models_tse import (
    TseAbrangencia, TseCandidato, TseCargo, TseEleicao, TseFederacao, TsePartido,
    TseResultadoCandidato, TseResultadoPartido, TseSecao, TseTotalizacao,
)

from . import bu_reconciliation, reconciliation
from .analytics_secao import SecaoAnalytics
from .analytics_local import LocalAnalytics
from .normalization import BRASILIA, cargo_code
from .repository import TseRepository, abrangencia_key


# Limite de itens por consulta de distribuicao: mantem a URL e a resposta
# (itens x partes) em tamanho de dashboard. O painel aceita ate este numero.
MAX_ITENS_DISTRIBUICAO = 20


def _soma(valores) -> int | None:
    presentes = [v for v in valores if v is not None]
    return sum(presentes) if presentes else None


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
            "municipio_nome": a.municipio_nome, "zona": a.zona, "secao": None}


def _voto_valido(destinacao: str | None) -> bool:
    return destinacao is None or destinacao.casefold().startswith("válido")


class TseAnalytics:
    def __init__(self, session: Session):
        self.session = session
        self.repo = TseRepository(session)
        # Nivel SECAO: fonte = Boletim de Urna (ADR-090). Acima dele, EA20.
        self.secoes = SecaoAnalytics(self)
        self.locais = LocalAnalytics(self)

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

    def resumo(self, eleicao_id: int, uf: str, origem: str | None = None,
               municipio: str | None = None, zona: str | None = None,
               secao: str | None = None, local_votacao: str | None = None) -> dict:
        """Totalizacao de cada cargo na abrangencia: UF, municipio, zona (EA20) ou secao (BU)."""
        eleicao = self.eleicao(eleicao_id, origem)
        if local_votacao:
            if secao:
                self.locais.validar_secao(eleicao, uf, municipio, zona, local_votacao, secao)
            else:
                return self.locais.resumo(eleicao, uf, municipio, zona, local_votacao)
        if secao:
            return self.secoes.resumo(eleicao, uf, municipio, zona, secao)
        abr = self._abrangencia(eleicao, uf, municipio, zona)
        cargos = []
        for item in self._cargos_da_eleicao(eleicao.id):
            cargo = self._cargo(item["codigo"])
            total = self.repo.latest_totalizacao(abr.id, cargo.id)
            if total is not None:
                cargos.append({**item, "vagas": total.vagas, "totalizacao": _totalizacao(total)})
        # A totalizacao de referencia e a mais recente entre os cargos do recorte.
        referencia = max((c["totalizacao"] for c in cargos),
                         key=lambda t: t["gerado_em"] or "", default=None)
        return {
            "eleicao": _eleicao(eleicao), "origem": eleicao.origem, "uf": abr.uf,
            "abrangencia": _abrangencia(abr), "cargos": cargos,
            "ultima_atualizacao": referencia["gerado_em"] if referencia else None,
            "totalizacao": referencia,
        }

    # ------------------------------------------------------------ territorio
    def opcoes_territoriais(self, eleicao_id: int, uf: str, municipio: str | None = None,
                            zona: str | None = None, origem: str | None = None,
                            local_votacao: str | None = None) -> dict:
        """Opcoes do proximo nivel territorial: municipios, zonas ou secoes.

        So lista o que tem resultado oficial ingerido para a eleicao (municipio
        e zona) ou cadastro no EA16 (secao). Municipio fora da UF, zona fora do
        municipio ou qualquer codigo inexistente respondem 404 -- nunca os
        dados de outro recorte. Cada secao traz a situacao do seu Boletim de
        Urna: o voto por secao so existe onde o BU ja foi ingerido (ADR-090).
        """
        eleicao = self.eleicao(eleicao_id, origem)
        if local_votacao and not zona:
            raise ValueError('Local de votação exige município e zona.')
        abr_uf = self._abrangencia(eleicao, uf)
        abr_municipio = self._abrangencia(eleicao, uf, municipio) if municipio else None
        abr_zona = self._abrangencia(eleicao, uf, municipio, zona) if zona else None
        com_resultado = select(TseTotalizacao.abrangencia_id).where(
            TseTotalizacao.eleicao_id == eleicao.id)

        if abr_zona is not None:
            nivel = "secoes"
            itens = self.secoes.opcoes(eleicao, abr_zona)
            locais = self.locais.listar(eleicao, abr_zona, itens)
            if local_votacao:
                if not any(l['codigo'] == local_votacao for l in locais):
                    raise TseNotFound('Local de votação não encontrado nesta zona.')
                itens = [s for s in itens if s['local_votacao'] == local_votacao]
        elif abr_municipio is not None:
            nivel = "zonas"
            zonas = self.session.scalars(
                select(TseAbrangencia).where(
                    TseAbrangencia.parent_id == abr_municipio.id, TseAbrangencia.tipo == "ZONA",
                    TseAbrangencia.id.in_(com_resultado))
                .order_by(TseAbrangencia.zona))
            itens = [{"zona": z.zona} for z in zonas]
        else:
            nivel = "municipios"
            municipios = self.session.scalars(
                select(TseAbrangencia).where(
                    TseAbrangencia.parent_id == abr_uf.id, TseAbrangencia.tipo == "MUNICIPIO",
                    TseAbrangencia.id.in_(com_resultado))
                .order_by(TseAbrangencia.municipio_nome, TseAbrangencia.municipio_codigo))
            itens = [{"codigo": m.municipio_codigo, "nome": m.municipio_nome} for m in municipios]

        return {
            "eleicao": _eleicao(eleicao), "origem": eleicao.origem, "uf": abr_uf.uf,
            "municipio": ({"codigo": abr_municipio.municipio_codigo,
                           "nome": abr_municipio.municipio_nome} if abr_municipio else None),
            "zona": abr_zona.zona if abr_zona else None,
            "nivel": nivel, "itens": itens,
            "locais": locais if abr_zona is not None else [],
            "local_votacao": local_votacao,
            # O filtro por secao existe; o voto de cada uma depende do seu BU
            # (`bu_status` / `resultado_disponivel` em cada item).
            "votos_por_secao_disponiveis": True,
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
                        origem: str | None = None, limite: int | None = None,
                        secao: str | None = None, local_votacao: str | None = None) -> dict:
        if local_votacao:
            eleicao = self.eleicao(eleicao_id, origem)
            if secao:
                self.locais.validar_secao(eleicao, uf, municipio, zona, local_votacao, secao)
            else:
                return self.locais.resultado(eleicao, self._cargo(cargo_codigo), uf, municipio, zona, local_votacao, limite)
        if secao:
            return self.secoes.resultado_cargo(
                self.eleicao(eleicao_id, origem), self._cargo(cargo_codigo), uf, municipio,
                zona, secao, limite)
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
                  origem: str | None = None, secao: str | None = None,
                  local_votacao: str | None = None) -> dict:
        """Partido isolado ou federacao -> candidatos, com posicao ordinal na nominata."""
        if local_votacao:
            eleicao = self.eleicao(eleicao_id, origem)
            if secao:
                self.locais.validar_secao(eleicao, uf, municipio, zona, local_votacao, secao)
            else:
                return self.locais.nominatas(eleicao, self._cargo(cargo_codigo), uf, municipio, zona, local_votacao)
        if secao:
            return self.secoes.nominatas(
                self.eleicao(eleicao_id, origem), self._cargo(cargo_codigo), uf, municipio,
                zona, secao)
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

    # ----------------------------------------------------------- distribuicao
    def distribuicao(self, eleicao_id: int, cargo_codigo: str, uf: str,
                     candidatos: list[str] | None = None, partidos: list[str] | None = None,
                     federacoes: list[str] | None = None, municipio: str | None = None,
                     zona: str | None = None, origem: str | None = None,
                     secao: str | None = None, local_votacao: str | None = None,
                     nivel: str | None = None) -> dict:
        """Distribuicao territorial de candidatos e nominatas, em lote.

        UF -> por municipio; municipio -> por zona; zona -> por secao (uma
        parte por urna, votos do Boletim de Urna); secao -> nivel minimo.
        Cada parte usa o SEU resultado oficial mais recente: as partes podem
        estar em janelas diferentes e nada e reconciliado com o total do
        recorte. O total de uma zona e sempre o do EA20 -- a soma dos BUs e
        so `soma_das_partes`.

        Dois percentuais distintos por parte:
        - `percentual_item`: quanto dos votos do item NO RECORTE veio da parte;
        - `percentual_parte`: participacao do item nos votos validos DA PARTE
          (para candidato, o percentual publicado pelo TSE).

        Nominata = mesma definicao da pagina Proporcional: votos nominais
        validos dos candidatos + votos de legenda oficiais do partido.
        """
        candidatos = list(dict.fromkeys(candidatos or []))
        partidos = list(dict.fromkeys(partidos or []))
        federacoes = list(dict.fromkeys(federacoes or []))
        pedidos = len(candidatos) + len(partidos) + len(federacoes)
        if pedidos == 0:
            raise ValueError("Informe ao menos um candidato ou uma nominata.")
        if pedidos > MAX_ITENS_DISTRIBUICAO:
            raise ValueError(f"No máximo {MAX_ITENS_DISTRIBUICAO} itens por consulta.")
        if (local_votacao or nivel == 'locais_votacao') and (not zona or not municipio):
            raise ValueError('Local de votação exige município e zona.')
        if secao or local_votacao:
            eleicao, cargo = self.eleicao(eleicao_id, origem), self._cargo(cargo_codigo)
            abr = total = None
        else:
            eleicao, cargo, abr, total = self._contexto_cargo(
                eleicao_id, cargo_codigo, uf, municipio, zona, origem)

        # ---- itens pedidos (validados contra a eleicao e o cargo)
        cands = {c.sqcand: c for c in self.session.scalars(
            select(TseCandidato).where(TseCandidato.eleicao_id == eleicao.id,
                                       TseCandidato.cargo_id == cargo.id,
                                       TseCandidato.sqcand.in_(candidatos)))} if candidatos else {}
        faltando = [sq for sq in candidatos if sq not in cands]
        if faltando:
            raise TseNotFound(f"Candidato não encontrado para o cargo: {', '.join(faltando)}.")
        todos_partidos = list(self.session.scalars(
            select(TsePartido).where(TsePartido.eleicao_id == eleicao.id))) \
            if (partidos or federacoes or cands) else []
        por_id = {p.id: p for p in todos_partidos}
        por_numero = {p.numero: p for p in todos_partidos}
        feds = {f.id: f for f in self.session.scalars(
            select(TseFederacao).where(TseFederacao.eleicao_id == eleicao.id))} \
            if (partidos or federacoes or cands) else {}
        fed_por_numero = {f.numero: f for f in feds.values()}
        grupos: dict[str, dict] = {}
        for numero in federacoes:
            fed = fed_por_numero.get(numero)
            if fed is None:
                raise TseNotFound(f"Federação não encontrada: {numero}.")
            grupos[f"FEDERACAO:{fed.numero}"] = {"fed": fed, "partido": None}
        for numero in partidos:
            par = por_numero.get(numero)
            if par is None:
                raise TseNotFound(f"Partido não encontrado: {numero}.")
            # Partido federado: a nominata e a da federacao (como na Proporcional).
            fed = feds.get(par.federacao_id) if par.federacao_id else None
            if fed is not None:
                grupos[f"FEDERACAO:{fed.numero}"] = {"fed": fed, "partido": None}
            else:
                grupos[f"PARTIDO:{par.numero}"] = {"fed": None, "partido": par}
        for grupo in grupos.values():
            grupo["partido_ids"] = (
                {p.id for p in todos_partidos if p.federacao_id == grupo["fed"].id}
                if grupo["fed"] is not None else {grupo["partido"].id})

        def descrever_candidato(cand: TseCandidato) -> dict:
            partido = por_id[cand.partido_id]
            fed = feds.get(partido.federacao_id) if partido.federacao_id else None
            return {"tipo": "CANDIDATO", "id": cand.sqcand, "nome": cand.nome_urna,
                    "numero": cand.numero, "partido": partido.sigla,
                    "federacao": fed.sigla if fed else None}

        def descrever_grupo(chave: str, grupo: dict) -> dict:
            fed, par = grupo["fed"], grupo["partido"]
            return {"tipo": "FEDERACAO" if fed is not None else "PARTIDO", "id": chave,
                    "nome": (fed or par).nome, "numero": (fed or par).numero,
                    "sigla": (fed or par).sigla,
                    "partidos": sorted(por_id[pid].sigla for pid in grupo["partido_ids"])}

        if secao:
            if local_votacao:
                self.locais.validar_secao(eleicao, uf, municipio, zona, local_votacao, secao)
            return self.secoes.distribuicao_da_secao(
                eleicao, cargo, uf, municipio, zona, secao, [cands[sq] for sq in candidatos],
                grupos, descrever_candidato, descrever_grupo)

        if local_votacao or nivel == 'locais_votacao':
            return self.locais.distribuicao(eleicao, cargo, uf, municipio, zona, local_votacao,
                [cands[sq] for sq in candidatos], grupos, descrever_candidato, descrever_grupo,
                _totalizacao(total))

        # ---- partes do recorte e a totalizacao corrente de cada uma
        if zona:
            nivel, partes = "zona", []
        elif municipio:
            nivel, partes = "zonas", [a for a in self.repo.children(abr.id) if a.tipo == "ZONA"]
        else:
            nivel, partes = "municipios", [a for a in self.repo.children(abr.id)
                                           if a.tipo == "MUNICIPIO"]
        ids = [abr.id] + [p.id for p in partes]
        correntes = select(func.max(TseTotalizacao.id)).where(
            TseTotalizacao.cargo_id == cargo.id, TseTotalizacao.abrangencia_id.in_(ids)
        ).group_by(TseTotalizacao.abrangencia_id)
        tot_por_abr = {t.abrangencia_id: t for t in self.session.scalars(
            select(TseTotalizacao).where(TseTotalizacao.id.in_(correntes)))}
        partes = [p for p in partes if p.id in tot_por_abr]      # so partes com resultado
        tot_ids = [t.id for t in tot_por_abr.values()]
        abr_por_tot = {t.id: abr_id for abr_id, t in tot_por_abr.items()}

        # ---- votos: uma consulta por natureza, para todas as partes de uma vez
        votos_cand: dict[tuple[int, int], tuple] = {}
        if cands:
            ids_cand = [c.id for c in cands.values()]
            for tot_id, cand_id, votos, pct in self.session.execute(
                    select(TseResultadoCandidato.totalizacao_id, TseResultadoCandidato.candidato_id,
                           TseResultadoCandidato.votos, TseResultadoCandidato.percentual)
                    .where(TseResultadoCandidato.totalizacao_id.in_(tot_ids),
                           TseResultadoCandidato.candidato_id.in_(ids_cand))):
                votos_cand[(abr_por_tot[tot_id], cand_id)] = (votos, pct)
        posicoes = {}
        if cands:
            ordem = self.session.execute(
                select(TseResultadoCandidato.candidato_id)
                .join(TseCandidato, TseCandidato.id == TseResultadoCandidato.candidato_id)
                .where(TseResultadoCandidato.totalizacao_id == total.id)
                .order_by(TseResultadoCandidato.votos.desc(), TseCandidato.nome_urna,
                          TseCandidato.sqcand)).scalars().all()
            posicoes = {cand_id: i for i, cand_id in enumerate(ordem, start=1)}
        nominais: dict[tuple[int, int], int] = {}
        legenda: dict[tuple[int, int], int] = {}
        ids_partido = {pid for g in grupos.values() for pid in g["partido_ids"]}
        if ids_partido:
            # Mesmo criterio de `_voto_valido`: destinacao ausente ou "Válido...".
            valido = or_(TseResultadoCandidato.destinacao_voto.is_(None),
                         TseResultadoCandidato.destinacao_voto.like("V_lido%"))
            for tot_id, partido_id, soma in self.session.execute(
                    select(TseResultadoCandidato.totalizacao_id, TseCandidato.partido_id,
                           func.sum(TseResultadoCandidato.votos))
                    .join(TseCandidato, TseCandidato.id == TseResultadoCandidato.candidato_id)
                    .where(TseResultadoCandidato.totalizacao_id.in_(tot_ids),
                           TseCandidato.partido_id.in_(ids_partido), valido)
                    .group_by(TseResultadoCandidato.totalizacao_id, TseCandidato.partido_id)):
                nominais[(abr_por_tot[tot_id], partido_id)] = int(soma or 0)
            for tot_id, partido_id, votos, dvt in self.session.execute(
                    select(TseResultadoPartido.totalizacao_id, TseResultadoPartido.partido_id,
                           TseResultadoPartido.votos_legenda, TseResultadoPartido.destinacao_voto)
                    .where(TseResultadoPartido.totalizacao_id.in_(tot_ids),
                           TseResultadoPartido.partido_id.in_(ids_partido))):
                legenda[(abr_por_tot[tot_id], partido_id)] = (votos or 0) if _voto_valido(dvt) else 0

        def votos_grupo(abr_id: int, partido_ids) -> int | None:
            if abr_id not in tot_por_abr:
                return None
            return sum(nominais.get((abr_id, pid), 0) + legenda.get((abr_id, pid), 0)
                       for pid in partido_ids)

        def linha_partes(votos_de, pct_de, total_item):
            linhas = []
            for parte in partes:
                votos = votos_de(parte.id)
                linhas.append({
                    "codigo": parte.zona if nivel == "zonas" else parte.municipio_codigo,
                    "votos": votos,
                    "percentual_item": _pct(votos, total_item) if votos is not None else None,
                    "percentual_parte": pct_de(parte.id, votos),
                })
            return linhas

        def pct_grupo(abr_id, votos):
            return _pct(votos, tot_por_abr[abr_id].votos_validos) if votos is not None else None

        itens = []
        for sqcand in candidatos:
            cand = cands[sqcand]
            votos_total, pct_total = votos_cand.get((abr.id, cand.id), (None, None))
            partes_item = linha_partes(
                lambda abr_id, c=cand: votos_cand.get((abr_id, c.id), (None, None))[0],
                lambda abr_id, _v, c=cand: (
                    float(votos_cand[(abr_id, c.id)][1])
                    if votos_cand.get((abr_id, c.id), (None, None))[1] is not None else None),
                votos_total)
            itens.append({
                **descrever_candidato(cand),
                "total_votos": votos_total,
                "percentual": float(pct_total) if pct_total is not None else None,
                "posicao": posicoes.get(cand.id),
                "soma_das_partes": _soma([p["votos"] for p in partes_item]),
                "partes": partes_item,
            })
        for chave, grupo in grupos.items():
            votos_total = votos_grupo(abr.id, grupo["partido_ids"])
            partes_item = linha_partes(
                lambda abr_id, g=grupo: votos_grupo(abr_id, g["partido_ids"]), pct_grupo,
                votos_total)
            itens.append({
                **descrever_grupo(chave, grupo),
                "total_votos": votos_total,
                "percentual": pct_grupo(abr.id, votos_total),
                "posicao": None,
                "soma_das_partes": _soma([p["votos"] for p in partes_item]),
                "partes": partes_item,
            })

        ordenadas = sorted(partes, key=lambda p: (p.zona or "", p.municipio_nome or "",
                                                 p.municipio_codigo or ""))
        ordem_partes = {p.id: i for i, p in enumerate(ordenadas)}
        posicao_codigo = {(p.zona if nivel == "zonas" else p.municipio_codigo): ordem_partes[p.id]
                          for p in partes}
        for item in itens:
            item["partes"].sort(key=lambda linha: posicao_codigo[linha["codigo"]])

        partes_saida = [{
            "codigo": p.zona if nivel == "zonas" else p.municipio_codigo,
            "nome": f"Zona {p.zona}" if nivel == "zonas" else p.municipio_nome,
            "municipio_codigo": p.municipio_codigo, "zona": p.zona, "secao": None,
            "totalizacao": _totalizacao(tot_por_abr[p.id]),
        } for p in ordenadas]
        cobertura = None
        if nivel == "zona":
            # Zona -> secoes: uma parte por urna, com os votos do BU de cada uma.
            urnas = self.secoes.partes_da_zona(
                eleicao, cargo, abr, [c.id for c in cands.values()],
                {chave: g["partido_ids"] for chave, g in grupos.items()})
            if urnas["partes"]:
                nivel, partes_saida = "secoes", urnas["partes"]
                chaves = [cands[sq].id for sq in candidatos] + list(grupos)
                for item, chave in zip(itens, chaves):
                    item["partes"] = [{
                        "codigo": parte["codigo"],
                        "votos": urnas["votos"].get((parte["codigo"], chave)),
                        "percentual_item": (
                            _pct(urnas["votos"][(parte["codigo"], chave)], item["total_votos"])
                            if (parte["codigo"], chave) in urnas["votos"] else None),
                        "percentual_parte": (
                            _pct(urnas["votos"][(parte["codigo"], chave)],
                                 urnas["validos"][parte["codigo"]])
                            if (parte["codigo"], chave) in urnas["votos"] else None),
                    } for parte in urnas["partes"]]
                    item["soma_das_partes"] = _soma([p["votos"] for p in item["partes"]])
                cobertura = {"partes": len(urnas["partes"]),
                             "com_resultado": len(urnas["validos"])}
        return {
            "eleicao": _eleicao(eleicao), "origem": eleicao.origem,
            "cargo": {"codigo": cargo.codigo, "nome": cargo.nome, "vagas": total.vagas},
            "abrangencia": _abrangencia(abr), "totalizacao": _totalizacao(total),
            "nivel": nivel,
            "partes": partes_saida,
            "itens": itens,
            # As partes podem estar em janelas diferentes: nada e reconciliado.
            "partes_podem_divergir": nivel != "zona",
            "secao_disponivel": True,
            # So no nivel de secoes: quantas urnas a zona tem e quantas ja tem BU.
            "cobertura": cobertura,
        }

    # ---------------------------------------------------------- conferencia BU
    def conferencia_bu(self, eleicao_id: int, cargo_codigo: str, uf: str, municipio: str,
                       zona: str | None = None, origem: str | None = None) -> dict:
        """BU x EA20 por zona (uma zona, ou todas as do municipio). Somente leitura."""
        eleicao = self.eleicao(eleicao_id, origem)
        cargo = self._cargo(cargo_codigo)
        if zona:
            zonas = [self._abrangencia(eleicao, uf, municipio, zona)]
        else:
            pai = self._abrangencia(eleicao, uf, municipio)
            zonas = [a for a in self.repo.children(pai.id) if a.tipo == "ZONA"]
        itens = [bu_reconciliation.reconciliar_zona(
            self.session, eleicao, cargo, z.uf, z.municipio_codigo, z.zona) for z in zonas]
        return {
            "eleicao": _eleicao(eleicao), "origem": eleicao.origem,
            "cargo": {"codigo": cargo.codigo, "nome": cargo.nome},
            "itens": itens,
            # A conferencia nunca altera nada: EA20 = zona, BU = secao.
            "somente_leitura": True,
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

    def candidato(self, sqcand: str, eleicao_id: int, origem: str | None = None,
                  municipio: str | None = None, zona: str | None = None,
                  secao: str | None = None, local_votacao: str | None = None) -> dict:
        """Dados do candidato e seu resultado oficial na abrangencia (padrao: UF)."""
        eleicao = self.eleicao(eleicao_id, origem)
        cand = self._candidato(eleicao, sqcand)
        if local_votacao:
            if secao:
                self.locais.validar_secao(eleicao, cand.uf, municipio, zona, local_votacao, secao)
            else:
                result = self.locais.resultado(eleicao, cand.cargo, cand.uf, municipio, zona, local_votacao)
                linha = next((l for l in result['candidatos'] if l['sqcand'] == sqcand), None)
                zero = {'votos': 0, 'percentual': 0.0 if result['totalizacao']['votos_validos'] else None,
                    'situacao': None, 'eleito': None, 'destinacao_voto': None, 'voto_valido': True, 'posicao': None} if result['totalizacao'] else None
                return {k:v for k,v in result.items() if k not in ('candidatos','total_candidatos','cargo')} | {
                    'candidato': self._identidade(cand), 'consolidado': zero if linha is None else {
                        k:linha[k] for k in ('votos','percentual','situacao','eleito','destinacao_voto','voto_valido','posicao')}}
        if secao:
            return self.secoes.candidato(eleicao, cand, municipio, zona, secao)
        abr = self._abrangencia(eleicao, cand.uf, municipio, zona)
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
