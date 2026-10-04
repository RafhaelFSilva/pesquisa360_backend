"""EA20 -- resultado unificado: a fonte oficial consolidada (UF, municipio, zona).

`tpabr` vale `uf`, `mu` ou `zona`. No arquivo de zona, `cdabr` traz apenas o
numero da zona: o municipio vem do contexto da requisicao, nao do payload.
"""

from __future__ import annotations

from .normalization import (
    CandidatoResultado, Ea20, FederacaoResultado, PartidoResultado, cargo_code,
    to_datetime, to_int, to_pct,
)


def parse_ea20(doc: dict) -> Ea20:
    if len(doc.get("carg", [])) != 1:
        raise ValueError("EA20 deve conter exatamente um cargo")
    s, e, v = doc.get("s", {}), doc.get("e", {}), doc.get("v", {})
    carg = doc["carg"][0]
    partidos, candidatos = [], []
    for agr in carg.get("agr", []):
        for par in agr.get("par", []):
            partidos.append(PartidoResultado(
                numero=par["n"], sigla=par["sg"], nome=par["nm"],
                federacao_numero=par.get("nfed") or None,
                votos_nominais=to_int(par.get("tvtn")), votos_legenda=to_int(par.get("tvtl")),
                destinacao_voto=par.get("dvt") or None,
            ))
            for cand in par.get("cand", []):
                candidatos.append(CandidatoResultado(
                    sqcand=cand["sqcand"], numero=cand["n"], nome=cand["nm"],
                    nome_urna=cand["nmu"], partido_numero=par["n"],
                    situacao=cand.get("st") or None, eleito=cand.get("e") or None,
                    votos=to_int(cand.get("vap")), percentual=to_pct(cand.get("pvap")),
                    destinacao_voto=cand.get("dvt") or None,
                ))
    return Ea20(
        eleicao=doc.get("ele"), turno=to_int(doc.get("t")),
        tipo_abrangencia=doc.get("tpabr"), codigo_abrangencia=doc.get("cdabr"),
        cargo=cargo_code(carg["cd"]), cargo_nome=carg.get("nmn"), vagas=to_int(carg.get("nv")),
        idg=doc.get("idg"), gerado_em=to_datetime(doc.get("dg"), doc.get("hg")),
        ultima_totalizacao=to_datetime(doc.get("dt"), doc.get("ht")),
        andamento=doc.get("and"), totalizacao_final=doc.get("tf"),
        secoes=to_int(s.get("ts")), secoes_totalizadas=to_int(s.get("st")),
        eleitorado=to_int(e.get("te")), comparecimento=to_int(e.get("c")),
        abstencao=to_int(e.get("a")),
        votos_total=to_int(v.get("tv")), votos_validos=to_int(v.get("vv")),
        votos_nominais=to_int(v.get("vnom")), votos_legenda=to_int(v.get("vl")),
        votos_brancos=to_int(v.get("vb")), votos_nulos=to_int(v.get("tvn")),
        quociente_eleitoral=to_int(carg.get("qe")),
        federacoes=tuple(FederacaoResultado(
            numero=f["n"], sigla=f["sg"], nome=f["nm"], partidos=tuple(f.get("npar", [])),
        ) for f in carg.get("fed", [])),
        partidos=tuple(partidos), candidatos=tuple(candidatos),
        fase=doc.get("f"), votos_anulados=to_int(v.get("van")),
        votos_anulados_sub_judice=to_int(v.get("vansj")),
    )


def get_candidate(resultado: Ea20, *, sqcand: str | None = None,
                  numero: str | None = None) -> CandidatoResultado | None:
    """Localiza por `sqcand` (identidade oficial); `numero` e fallback contextual.

    Nome nunca identifica candidato. Numero ambiguo levanta erro em vez de
    escolher um candidato arbitrariamente.
    """
    if sqcand:
        return next((c for c in resultado.candidatos if c.sqcand == str(sqcand)), None)
    if numero:
        found = [c for c in resultado.candidatos if c.numero == str(numero)]
        if len(found) > 1:
            raise LookupError(
                f"Numero {numero} ambiguo no EA20 {resultado.codigo_abrangencia}")
        return found[0] if found else None
    raise ValueError("Informe sqcand ou numero")
