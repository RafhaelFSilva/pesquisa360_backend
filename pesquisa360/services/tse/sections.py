"""EA16 (municipio -> zona -> secao) e EA18 (arquivos de urna de uma secao).

Semantica de agregacao do EA16: `nsa` (na secao principal) lista as secoes
agregadas a ela; `nsp` (na agregada) aponta a principal. Secao agregada nao
tem urna propria -- seus votos estao no BU da principal -- e por isso nao e
contada como urna. O total de secoes do EA14/EA15/EA20 equivale ao numero de
secoes PRINCIPAIS.
"""

from __future__ import annotations

from .normalization import (
    ArquivoUrna, AuxiliarSecao, ConfiguracaoSecoes, HashSecao, MunicipioSecoes, Secao,
    to_datetime,
)

SITUACAO_HASH_TOTALIZADO = "totalizado"


def parse_ea16(doc: dict, uf: str) -> ConfiguracaoSecoes:
    abr = next((a for a in doc["abr"] if a["cd"] == uf), None)
    if abr is None:
        raise LookupError(f"UF {uf} ausente do EA16")
    municipios = []
    for mu in abr["mu"]:
        secoes = [Secao(
            municipio=mu["cd"], zona=zon["cd"], secao=sec["ns"],
            secao_principal=sec.get("nsp") or None,
            agregadas=tuple(sec.get("nsa", [])),
            auxiliar_em=to_datetime(sec.get("da"), sec.get("ha")),
        ) for zon in mu["zon"] for sec in zon["sec"]]
        municipios.append(MunicipioSecoes(codigo=mu["cd"], nome=mu["nm"], secoes=tuple(secoes)))
    return ConfiguracaoSecoes(
        pleito=doc.get("cdp"), uf=uf, idg=doc.get("idg"),
        gerado_em=to_datetime(doc.get("dg"), doc.get("hg")), municipios=tuple(municipios),
        fase=doc.get("f"),
    )


def parse_ea18(doc: dict) -> AuxiliarSecao:
    return AuxiliarSecao(
        idg=doc.get("idg"), gerado_em=to_datetime(doc.get("dg"), doc.get("hg")),
        situacao=doc.get("st"), fase=doc.get("f"),
        hashes=tuple(HashSecao(
            hash=h.get("hash") or None, situacao=h.get("st"),
            recebido_em=to_datetime(h.get("dr"), h.get("hr")),
            arquivos=tuple(ArquivoUrna(nome=a["nm"], tipo=a["tp"]) for a in h.get("arq", [])),
        ) for h in doc.get("hashes", [])),
    )


def current_bu(auxiliar: AuxiliarSecao) -> tuple[HashSecao, ArquivoUrna] | None:
    """BU do hash em situacao 'Totalizado'; hashes em outra situacao sao ignorados.

    Uma secao pode estar 'Totalizada' sem arquivo publicado: no ambiente de
    simulacao o EA18 traz `hashes: [{"arq": []}]`, sem hash nem situacao.
    Durante a apuracao o EA18 oficial traz a secao como 'Recebida' e o hash
    como 'Recebido': o arquivo existe, mas ainda nao foi totalizado, entao
    nao ha BU corrente -- os metadados sao registrados mesmo assim.
    """
    for h in auxiliar.hashes:
        if h.hash and (h.situacao or "").casefold() == SITUACAO_HASH_TOTALIZADO:
            for arquivo in h.arquivos:
                if arquivo.tipo == "bu":
                    return h, arquivo
    return None
