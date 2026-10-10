"""Leituras analiticas do nivel SECAO, a partir do Boletim de Urna (ADR-090).

O BU e a fonte oficial somente da secao; UF, municipio e zona continuam vindo
do EA20 (analytics.py). Nada aqui soma BUs para produzir zona, nem divide a
zona para produzir secao.

Regras do nivel secao:
- secao existente sem BU ingerido responde 200 com `result_available: false`
  e status `AGUARDANDO_BU` -- nunca zero voto, nunca 404;
- secao agregada nao tem urna: o resultado e o do BU da principal, marcado
  `resultado_agregado` com as secoes do grupo. Os votos NAO sao repartidos
  nem copiados entre as secoes do grupo;
- votos validos = nominais + legenda do boletim. O BU nao informa destinacao
  do voto: voto anulado sub judice e voto em votavel fora da lista de
  candidatos aparecem nele como nominais. Na totalizacao (EA20) o TSE retira
  o primeiro dos validos e soma o segundo aos nulos -- por isso a secao nao
  e comparada com a zona pelo total de validos (ver bu_reconciliation);
- candidato ausente de um BU ingerido tem zero voto naquela urna (o boletim
  lista todos os votaveis que receberam voto).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select

from pesquisa360.db.models_tse import (
    TseAbrangencia, TseBoletimUrna, TseBuCargo, TseBuControle, TseBuVoto, TseCandidato,
    TseCargo, TseEleicao, TseFederacao, TsePartido, TseSecao,
)

from .bu_store import ERRO

BU_DISPONIVEL, AGUARDANDO_BU, ERRO_PROCESSAMENTO = (
    "BU_DISPONIVEL", "AGUARDANDO_BU", "ERRO_PROCESSAMENTO")


def status_bu(controle: TseBuControle | None) -> str:
    if controle is not None and controle.boletim_id is not None:
        return BU_DISPONIVEL
    if controle is not None and controle.status == ERRO:
        return ERRO_PROCESSAMENTO
    return AGUARDANDO_BU


def nome_do_grupo(secoes: list[str]) -> str:
    return f"Seção {secoes[0]}" if len(secoes) == 1 else "Seções " + " + ".join(secoes)


@dataclass
class ContextoSecao:
    eleicao: TseEleicao
    zona: TseAbrangencia
    alvo: TseSecao              # a secao pedida
    principal: TseSecao         # a dona da urna (a propria, ou a principal da agregada)
    grupo: list[str]            # principal + agregadas, em ordem
    controle: TseBuControle | None
    boletim: TseBoletimUrna | None


class SecaoAnalytics:
    def __init__(self, base):
        # `base` e o TseAnalytics: reaproveita resolucao de eleicao/cargo/zona.
        self.base = base
        self.session = base.session

    # ------------------------------------------------------------- resolucao
    def contexto(self, eleicao: TseEleicao, uf: str, municipio: str | None, zona: str | None,
                 secao: str) -> ContextoSecao:
        from .analytics import TseNotFound

        if not zona:
            raise ValueError("Seção exige zona.")
        abr_zona = self.base._abrangencia(eleicao, uf, municipio, zona)
        base = [TseSecao.origem == eleicao.origem, TseSecao.pleito == eleicao.pleito,
                TseSecao.uf == abr_zona.uf, TseSecao.municipio_codigo == municipio,
                TseSecao.zona == zona]
        alvo = self.session.scalars(select(TseSecao).where(*base, TseSecao.secao == secao)).first()
        if alvo is None:
            raise TseNotFound("Seção não encontrada nesta zona.")
        numero = alvo.secao_principal or alvo.secao
        grupo = list(self.session.scalars(
            select(TseSecao).where(*base, (TseSecao.secao == numero)
                                   | (TseSecao.secao_principal == numero))
            .order_by(TseSecao.eh_principal.desc(), TseSecao.secao)))
        principal = next((s for s in grupo if s.eh_principal), alvo)
        controle = self.session.scalars(
            select(TseBuControle).where(TseBuControle.secao_id == principal.id)).first()
        boletim = (self.session.get(TseBoletimUrna, controle.boletim_id)
                   if controle is not None and controle.boletim_id else None)
        return ContextoSecao(eleicao=eleicao, zona=abr_zona, alvo=alvo, principal=principal,
                             grupo=[s.secao for s in grupo], controle=controle, boletim=boletim)

    def _abrangencia(self, ctx: ContextoSecao) -> dict:
        from .analytics import _abrangencia

        return {**_abrangencia(ctx.zona), "tipo": "SECAO", "secao": ctx.alvo.secao}

    def _secao(self, ctx: ContextoSecao, disponivel: bool | None = None) -> dict:
        from .analytics import _dt

        b = ctx.boletim
        return {
            "numero": ctx.alvo.secao, "principal": ctx.alvo.eh_principal,
            "secao_principal": ctx.alvo.secao_principal,
            "secoes_do_grupo": ctx.grupo, "resultado_agregado": len(ctx.grupo) > 1,
            "section_exists": True,
            "result_available": b is not None if disponivel is None else disponivel,
            "status": status_bu(ctx.controle),
            "bu": None if b is None else {
                "sha256": b.sha256, "tipo": b.tipo_bu, "local_votacao": b.local_votacao,
                "comparecimento": b.comparecimento,
                # Hora local da urna, como no boletim (sem fuso).
                "emitido_em_local": b.emitido_local.isoformat() if b.emitido_local else None,
                "recebido_em": _dt(b.recebido_em), "processado_em": _dt(b.processado_em),
                "versao_votacao": b.versao_votacao,
            },
        }

    def _bu_cargo(self, ctx: ContextoSecao, cargo: TseCargo) -> TseBuCargo | None:
        if ctx.boletim is None:
            return None
        return self.session.scalars(select(TseBuCargo).where(
            TseBuCargo.boletim_id == ctx.boletim.id, TseBuCargo.cargo_id == cargo.id,
            TseBuCargo.eleicao_id == ctx.eleicao.id)).first()

    def _vagas(self, ctx: ContextoSecao, cargo: TseCargo) -> int | None:
        total = self.base.repo.latest_totalizacao(ctx.zona.id, cargo.id)
        return total.vagas if total else None

    # ------------------------------------------------------------------ resumo
    def resumo(self, eleicao: TseEleicao, uf: str, municipio, zona, secao: str) -> dict:
        from .analytics import _eleicao

        ctx = self.contexto(eleicao, uf, municipio, zona, secao)
        cargos = []
        if ctx.boletim is not None:
            for bc, cargo in self.session.execute(
                    select(TseBuCargo, TseCargo).join(TseCargo, TseCargo.id == TseBuCargo.cargo_id)
                    .where(TseBuCargo.boletim_id == ctx.boletim.id,
                           TseBuCargo.eleicao_id == eleicao.id)
                    .order_by(TseCargo.codigo)):
                cargos.append({"codigo": cargo.codigo, "nome": cargo.nome,
                               "vagas": self._vagas(ctx, cargo),
                               "totalizacao": totalizacao_bu(bc, ctx.boletim)})
        referencia = cargos[0]["totalizacao"] if cargos else None
        return {
            "eleicao": _eleicao(eleicao), "origem": eleicao.origem, "uf": ctx.zona.uf,
            "abrangencia": self._abrangencia(ctx), "cargos": cargos,
            "ultima_atualizacao": referencia["gerado_em"] if referencia else None,
            "totalizacao": referencia, "fonte": "BU",
            "secao": self._secao(ctx, bool(cargos)),
        }

    # ------------------------------------------------------------------- cargo
    def _linhas(self, bc: TseBuCargo) -> list[dict]:
        from .analytics import _pct

        validos = bc.votos_nominais + bc.votos_legenda
        rows = self.session.execute(
            select(TseBuVoto, TseCandidato, TsePartido, TseFederacao)
            .outerjoin(TseCandidato, TseCandidato.id == TseBuVoto.candidato_id)
            .outerjoin(TsePartido, TsePartido.id == TseBuVoto.partido_id)
            .outerjoin(TseFederacao, TseFederacao.id == TsePartido.federacao_id)
            .where(TseBuVoto.bu_cargo_id == bc.id, TseBuVoto.tipo == "NOMINAL")
            .order_by(TseBuVoto.votos.desc(), TseCandidato.nome_urna, TseBuVoto.numero))
        return [{
            "posicao": posicao, "sqcand": cand.sqcand if cand else None, "numero": voto.numero,
            "nome": cand.nome if cand else None,
            # Votavel do boletim que nao esta na lista de candidatos divulgada (EA20).
            "nome_urna": cand.nome_urna if cand else f"Nº {voto.numero} (fora da lista de candidatos)",
            "partido": {"numero": voto.partido_numero, "sigla": par.sigla if par else None,
                        "nome": par.nome if par else None},
            "federacao": ({"numero": fed.numero, "sigla": fed.sigla, "nome": fed.nome}
                          if fed else None),
            "votos": voto.votos, "percentual": _pct(voto.votos, validos),
            # O boletim nao traz situacao, eleito nem destinacao do voto.
            "situacao": None, "eleito": None, "destinacao_voto": None, "voto_valido": True,
        } for posicao, (voto, cand, par, fed) in enumerate(rows, start=1)]

    def _cabecalho(self, ctx: ContextoSecao, cargo: TseCargo, bc: TseBuCargo | None) -> dict:
        from .analytics import _eleicao

        return {
            "eleicao": _eleicao(ctx.eleicao), "origem": ctx.eleicao.origem,
            "cargo": {"codigo": cargo.codigo, "nome": cargo.nome, "vagas": self._vagas(ctx, cargo)},
            "abrangencia": self._abrangencia(ctx),
            "totalizacao": totalizacao_bu(bc, ctx.boletim) if bc else None,
            "fonte": "BU", "secao": self._secao(ctx, bc is not None),
        }

    def resultado_cargo(self, eleicao, cargo: TseCargo, uf, municipio, zona, secao,
                        limite: int | None = None) -> dict:
        ctx = self.contexto(eleicao, uf, municipio, zona, secao)
        bc = self._bu_cargo(ctx, cargo)
        candidatos = self._linhas(bc) if bc else []
        return {**self._cabecalho(ctx, cargo, bc), "total_candidatos": len(candidatos),
                "candidatos": candidatos[:limite] if limite else candidatos}

    def nominatas(self, eleicao, cargo: TseCargo, uf, municipio, zona, secao) -> dict:
        ctx = self.contexto(eleicao, uf, municipio, zona, secao)
        bc = self._bu_cargo(ctx, cargo)
        nominatas = []
        if bc is not None:
            grupos: dict[tuple, dict] = {}

            def grupo_de(par: dict, fed: dict | None) -> dict:
                chave = ("FEDERACAO", fed["numero"]) if fed else ("PARTIDO", par["numero"])
                grupo = grupos.setdefault(chave, {
                    "tipo": chave[0], "numero": chave[1], "sigla": (fed or par)["sigla"],
                    "nome": (fed or par)["nome"], "partidos": {}, "candidatos": [],
                    "votos_legenda": 0})
                grupo["partidos"][par["numero"]] = par
                return grupo

            for linha in self._linhas(bc):
                grupo_de(linha["partido"], linha["federacao"])["candidatos"].append(linha)
            # Legenda OFICIAL do boletim, por partido: nunca derivada de candidato.
            for voto, par, fed in self.session.execute(
                    select(TseBuVoto, TsePartido, TseFederacao)
                    .outerjoin(TsePartido, TsePartido.id == TseBuVoto.partido_id)
                    .outerjoin(TseFederacao, TseFederacao.id == TsePartido.federacao_id)
                    .where(TseBuVoto.bu_cargo_id == bc.id, TseBuVoto.tipo == "LEGENDA")):
                partido = {"numero": voto.partido_numero, "sigla": par.sigla if par else None,
                           "nome": par.nome if par else None}
                federacao = ({"numero": fed.numero, "sigla": fed.sigla, "nome": fed.nome}
                             if fed else None)
                grupo_de(partido, federacao)["votos_legenda"] += voto.votos
            for grupo in grupos.values():
                candidatos = [{**c, "posicao_geral": c["posicao"], "posicao": i}
                              for i, c in enumerate(grupo["candidatos"], start=1)]
                nominais = sum(c["votos"] for c in candidatos)
                nominatas.append({
                    **grupo,
                    "partidos": sorted(grupo["partidos"].values(), key=lambda p: p["numero"]),
                    "candidatos": candidatos, "votos_nominais": nominais,
                    "votos_nominais_validos": nominais,
                    "total": nominais + grupo["votos_legenda"],
                })
            nominatas.sort(key=lambda n: (-n["total"], n["sigla"] or ""))
        return {**self._cabecalho(ctx, cargo, bc), "nominatas": nominatas}

    def candidato(self, eleicao, cand: TseCandidato, municipio, zona, secao) -> dict:
        from .analytics import _eleicao

        ctx = self.contexto(eleicao, cand.uf, municipio, zona, secao)
        bc = self._bu_cargo(ctx, cand.cargo)
        consolidado = None
        if bc is not None:
            linha = next((l for l in self._linhas(bc) if l["sqcand"] == cand.sqcand), None)
            # BU ingerido sem o candidato: zero voto nesta urna (dado, nao ausencia).
            consolidado = {
                "votos": linha["votos"] if linha else 0,
                "percentual": linha["percentual"] if linha else 0.0,
                "situacao": None, "eleito": None, "destinacao_voto": None, "voto_valido": True,
                "posicao": linha["posicao"] if linha else None,
            }
        return {
            "eleicao": _eleicao(eleicao), "origem": eleicao.origem,
            "candidato": self.base._identidade(cand), "abrangencia": self._abrangencia(ctx),
            "consolidado": consolidado,
            "totalizacao": totalizacao_bu(bc, ctx.boletim) if bc else None,
            "fonte": "BU", "secao": self._secao(ctx, bc is not None),
        }

    # ------------------------------------------------------------ territorio
    def opcoes(self, eleicao: TseEleicao, abr_zona: TseAbrangencia) -> list[dict]:
        """Secoes da zona (EA16) com a situacao do BU de cada uma. Duas consultas."""
        secoes = list(self.session.scalars(
            select(TseSecao).where(
                TseSecao.origem == eleicao.origem, TseSecao.pleito == eleicao.pleito,
                TseSecao.uf == abr_zona.uf, TseSecao.municipio_codigo == abr_zona.municipio_codigo,
                TseSecao.zona == abr_zona.zona)
            .order_by(TseSecao.secao)))
        principais = {s.secao: s for s in secoes if s.eh_principal}
        rows = list(self.session.execute(
            select(TseBuControle, TseBoletimUrna.local_votacao)
            .outerjoin(TseBoletimUrna, TseBoletimUrna.id == TseBuControle.boletim_id)
            .where(TseBuControle.secao_id.in_([s.id for s in principais.values()])))) if principais else []
        controles = {c.secao_id: c for c, _local in rows}
        locais = {c.secao_id: f"{local:04d}" if local else None for c, local in rows}
        agregadas: dict[str, list[str]] = {}
        for s in secoes:
            if not s.eh_principal:
                agregadas.setdefault(s.secao_principal, []).append(s.secao)
        itens = []
        for s in secoes:
            dona = s if s.eh_principal else principais.get(s.secao_principal)
            status = status_bu(controles.get(dona.id)) if dona is not None else AGUARDANDO_BU
            grupo = agregadas.get(dona.secao if dona is not None else s.secao, [])
            itens.append({
                "secao": s.secao, "zona": s.zona, "principal": s.eh_principal,
                "secao_principal": s.secao_principal,
                # Data do arquivo de urna no EA16: a secao ja foi recebida pelo TSE.
                "recebida": (dona or s).auxiliar_em is not None,
                "agregadas": agregadas.get(s.secao, []) if s.eh_principal else [],
                "resultado_agregado": bool(grupo),
                "bu_status": status, "resultado_disponivel": status == BU_DISPONIVEL,
                # Agregadas apontam ao local da urna do grupo (BU da principal).
                "local_votacao": locais.get(dona.id) if dona else None,
            })
        return itens

    # ----------------------------------------------------------- distribuicao
    def partes_da_zona(self, eleicao: TseEleicao, cargo: TseCargo, abr_zona: TseAbrangencia,
                       cand_ids: list[int], grupos: dict[str, set[int]]) -> dict:
        """Votos de candidatos e nominatas em TODAS as urnas da zona, em lote.

        Uma parte por urna (secao principal + suas agregadas). O numero de
        consultas e fixo: nao cresce com secoes, candidatos nem nominatas.
        Devolve `partes` (metadados) e `votos[(parte, chave_do_item)]`.
        """
        secoes = list(self.session.scalars(
            select(TseSecao).where(
                TseSecao.origem == eleicao.origem, TseSecao.pleito == eleicao.pleito,
                TseSecao.uf == abr_zona.uf, TseSecao.municipio_codigo == abr_zona.municipio_codigo,
                TseSecao.zona == abr_zona.zona)
            .order_by(TseSecao.secao)))
        principais = [s for s in secoes if s.eh_principal]
        if not principais:
            return {"partes": [], "votos": {}, "validos": {}}
        agregadas: dict[str, list[str]] = {}
        for s in secoes:
            if not s.eh_principal:
                agregadas.setdefault(s.secao_principal, []).append(s.secao)
        ids = [s.id for s in principais]
        controles = {c.secao_id: c for c in self.session.scalars(
            select(TseBuControle).where(TseBuControle.secao_id.in_(ids)))}
        boletim_ids = [c.boletim_id for c in controles.values() if c.boletim_id]
        por_boletim: dict[int, tuple[TseBuCargo, TseBoletimUrna]] = {}
        if boletim_ids:
            for bc, boletim in self.session.execute(
                    select(TseBuCargo, TseBoletimUrna)
                    .join(TseBoletimUrna, TseBoletimUrna.id == TseBuCargo.boletim_id)
                    .where(TseBuCargo.boletim_id.in_(boletim_ids), TseBuCargo.cargo_id == cargo.id,
                           TseBuCargo.eleicao_id == eleicao.id)):
                por_boletim[boletim.id] = (bc, boletim)
        bc_ids = [bc.id for bc, _b in por_boletim.values()]

        nominais_cand: dict[tuple[int, int], int] = {}
        if cand_ids and bc_ids:
            for bc_id, cand_id, votos in self.session.execute(
                    select(TseBuVoto.bu_cargo_id, TseBuVoto.candidato_id, TseBuVoto.votos)
                    .where(TseBuVoto.bu_cargo_id.in_(bc_ids), TseBuVoto.tipo == "NOMINAL",
                           TseBuVoto.candidato_id.in_(cand_ids))):
                nominais_cand[(bc_id, cand_id)] = votos
        por_partido: dict[tuple[int, int], int] = {}
        partido_ids = {pid for pids in grupos.values() for pid in pids}
        if partido_ids and bc_ids:
            # Nominais + legenda oficial do boletim, por partido.
            for bc_id, partido_id, soma in self.session.execute(
                    select(TseBuVoto.bu_cargo_id, TseBuVoto.partido_id, func.sum(TseBuVoto.votos))
                    .where(TseBuVoto.bu_cargo_id.in_(bc_ids),
                           TseBuVoto.partido_id.in_(partido_ids))
                    .group_by(TseBuVoto.bu_cargo_id, TseBuVoto.partido_id)):
                por_partido[(bc_id, partido_id)] = int(soma or 0)

        partes, votos, validos = [], {}, {}
        for s in principais:
            grupo = [s.secao] + agregadas.get(s.secao, [])
            controle = controles.get(s.id)
            par = por_boletim.get(controle.boletim_id) if controle and controle.boletim_id else None
            partes.append({
                "codigo": s.secao, "nome": nome_do_grupo(grupo),
                "municipio_codigo": s.municipio_codigo, "zona": s.zona, "secao": s.secao,
                "secoes_do_grupo": grupo, "resultado_agregado": len(grupo) > 1,
                "bu_status": status_bu(controle),
                "totalizacao": totalizacao_bu(par[0], par[1]) if par else None,
            })
            if par is None:
                continue        # sem BU: ausencia de dado, nao zero
            bc = par[0]
            validos[s.secao] = bc.votos_nominais + bc.votos_legenda
            for cand_id in cand_ids:
                votos[(s.secao, cand_id)] = nominais_cand.get((bc.id, cand_id), 0)
            for chave, pids in grupos.items():
                votos[(s.secao, chave)] = sum(por_partido.get((bc.id, pid), 0) for pid in pids)
        return {"partes": partes, "votos": votos, "validos": validos}

    def distribuicao_da_secao(self, eleicao, cargo: TseCargo, uf, municipio, zona, secao,
                              cands: list[TseCandidato], grupos: dict[str, dict],
                              descrever_candidato, descrever_grupo) -> dict:
        """Recorte = uma secao: nivel minimo. Totais do BU, sem partes."""
        from .analytics import _pct

        ctx = self.contexto(eleicao, uf, municipio, zona, secao)
        bc = self._bu_cargo(ctx, cargo)
        linhas = {l["sqcand"]: l for l in self._linhas(bc)} if bc else {}
        validos = (bc.votos_nominais + bc.votos_legenda) if bc else None
        por_partido: dict[int, int] = {}
        if bc is not None and grupos:
            por_partido = {pid: int(soma or 0) for pid, soma in self.session.execute(
                select(TseBuVoto.partido_id, func.sum(TseBuVoto.votos))
                .where(TseBuVoto.bu_cargo_id == bc.id).group_by(TseBuVoto.partido_id))}
        itens = []
        for cand in cands:
            linha = linhas.get(cand.sqcand)
            total = None if bc is None else (linha["votos"] if linha else 0)
            itens.append({**descrever_candidato(cand), "total_votos": total,
                          "percentual": _pct(total, validos) if total is not None else None,
                          "posicao": linha["posicao"] if linha else None,
                          "soma_das_partes": None, "partes": []})
        for chave, grupo in grupos.items():
            total = None if bc is None else sum(por_partido.get(pid, 0)
                                                for pid in grupo["partido_ids"])
            itens.append({**descrever_grupo(chave, grupo), "total_votos": total,
                          "percentual": _pct(total, validos) if total is not None else None,
                          "posicao": None, "soma_das_partes": None, "partes": []})
        return {**self._cabecalho(ctx, cargo, bc), "nivel": "secao", "partes": [], "itens": itens,
                "partes_podem_divergir": False, "secao_disponivel": True}


def totalizacao_bu(bc: TseBuCargo, boletim: TseBoletimUrna) -> dict:
    """Mesmas chaves da totalizacao do EA20; o que o BU nao tem fica nulo.

    Nao ha andamento nem contagem de secoes: um boletim e uma urna, e "1 de 1"
    nao teria o significado da totalizacao do TSE.
    """
    from .analytics import _dt

    return {
        "idg": None, "gerado_em": _dt(boletim.recebido_em),
        "ultima_totalizacao": _dt(boletim.recebido_em), "capturado_em": _dt(boletim.processado_em),
        "andamento": None, "totalizacao_final": False,
        "secoes_total": None, "secoes_totalizadas": None, "percentual_secoes": None,
        "eleitores": bc.eleitores_aptos, "comparecimento": bc.comparecimento,
        "abstencoes": bc.eleitores_aptos - bc.comparecimento,
        "votos_validos": bc.votos_nominais + bc.votos_legenda,
        "votos_nominais": bc.votos_nominais, "votos_legenda": bc.votos_legenda,
        "votos_brancos": bc.votos_brancos, "votos_nulos": bc.votos_nulos,
        "votos_anulados_sub_judice": None,
    }
