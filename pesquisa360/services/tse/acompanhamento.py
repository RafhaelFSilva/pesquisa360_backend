"""EA14 (acompanhamento Brasil) e EA15 (acompanhamento UF): gatilhos de mudanca.

Os dois arquivos tem o mesmo contrato. O EA14 lista as UFs (e `br`); o EA15
lista os municipios da UF (e a propria `uf`). A impressao de cada linha diz
se aquela abrangencia precisa de nova consulta de EA20.
"""

from __future__ import annotations

from .normalization import Acompanhamento, LinhaAcompanhamento, to_datetime, to_int


def parse_acompanhamento(doc: dict) -> Acompanhamento:
    linhas = []
    for abr in doc["abr"]:
        s, e = abr.get("s", {}), abr.get("e", {})
        linhas.append(LinhaAcompanhamento(
            tipo=abr["tpabr"], codigo=abr["cdabr"], andamento=abr.get("and"),
            ultima_totalizacao=to_datetime(abr.get("dt"), abr.get("ht")),
            secoes=to_int(s.get("ts")), secoes_totalizadas=to_int(s.get("st")),
            eleitorado=to_int(e.get("te")), comparecimento=to_int(e.get("c")),
            abstencao=to_int(e.get("a")),
            secoes_nao_instaladas=to_int(s.get("sni")),
            secoes_nao_apuradas=to_int(s.get("sna")),
        ))
    return Acompanhamento(
        eleicao=doc.get("ele"), turno=to_int(doc.get("t")), idg=doc.get("idg"),
        gerado_em=to_datetime(doc.get("dg"), doc.get("hg")), linhas=tuple(linhas),
        fase=doc.get("f"),
    )


def changed_keys(previous: dict[str, str] | None, current: dict[str, str]) -> list[str]:
    """Chaves (`uf:ap`, `mun:06050`) cuja impressao mudou; sem estado anterior, todas."""
    if previous is None:
        return sorted(current)
    return sorted(k for k, v in current.items() if previous.get(k) != v)
