"""EA11 (configuracao das eleicoes), EA12 (municipios) e montagem de URLs.

Todos os diretorios derivam dos templates `arq[].dir` do EA11. Somente a
localizacao do proprio EA11 e fixa: ela e o ponto de partida da descoberta.
"""

from __future__ import annotations

import unicodedata
from datetime import date, datetime

from .normalization import (
    CargoConfig, EleicaoConfig, Municipio, PleitoConfig, cargo_code, to_datetime, to_int,
)


def ea11_url(base_url: str, ambiente: str) -> str:
    return f"{base_url}/{ambiente}/comum/config/ele-c.json"


def parse_ea11(doc: dict, *, base_url: str, ambiente: str, pleito: str) -> PleitoConfig:
    matches = [p for p in doc.get("pl", []) if p["cd"] == str(pleito)]
    if not matches:
        raise LookupError(f"Pleito {pleito} ausente do EA11")
    pl = matches[0]
    eleicoes = []
    for ele in pl["e"]:
        cargos: dict[str, CargoConfig] = {}
        for abr in ele.get("abr", []):
            for cp in abr.get("cp", []):
                codigo = cargo_code(cp["cd"])
                cargos[codigo] = CargoConfig(codigo=codigo, nome=cp["ds"], tipo=cp["tp"])
        eleicoes.append(EleicaoConfig(
            codigo=ele["cd"], nome=ele["nm"], turno=to_int(ele["t"]), tipo=ele["tp"],
            codigo_segundo_turno=ele.get("cdt2") or None,
            abrangencias=tuple(a["cd"] for a in ele.get("abr", [])),
            cargos=tuple(cargos.values()),
        ))
    return PleitoConfig(
        base_url=base_url, ambiente=ambiente, fase=doc.get("f"), ciclo=pl["c"],
        pleito=pl["cd"], data=pl["dt"], idg=doc.get("idg"),
        gerado_em=to_datetime(doc.get("dg"), doc.get("hg")),
        eleicoes=tuple(eleicoes),
        templates={a["tp"]: a["dir"] for a in doc.get("arq", [])},
    )


def discover_pleito(doc: dict, cargos, hoje: date | None = None) -> str:
    """Pleito mais recente, ja realizado, que disputa TODOS os cargos pedidos.

    Evita fixar o codigo do pleito na operacao: o EA11 e a fonte. Um segundo
    turno (so Presidente/Governador) ou uma suplementar nao disputam o conjunto
    completo de cargos e por isso nao substituem o pleito geral.
    """
    hoje = hoje or date.today()
    pedidos = {cargo_code(c) for c in cargos}
    candidatos = []
    for pl in doc.get("pl", []):
        disputados = {cargo_code(cp["cd"]) for ele in pl.get("e", [])
                      for abr in ele.get("abr", []) for cp in abr.get("cp", [])}
        try:
            data = datetime.strptime(pl.get("dt", ""), "%d/%m/%Y").date()
        except ValueError:
            continue
        if pedidos <= disputados and data <= hoje:
            candidatos.append((data, pl["cd"]))
    if not candidatos:
        raise LookupError(
            f"Nenhum pleito ja realizado no EA11 disputa os cargos {sorted(pedidos)}")
    mais_recente = max(data for data, _cd in candidatos)
    escolhidos = sorted(cd for data, cd in candidatos if data == mais_recente)
    if len(escolhidos) > 1:
        raise LookupError(
            f"Pleito ambiguo no EA11 para os cargos {sorted(pedidos)}: {escolhidos}. "
            "Informe o pleito explicitamente.")
    return escolhidos[0]


def election_for_cargo(config: PleitoConfig, cargo: str) -> EleicaoConfig:
    """Eleicao do pleito que disputa o cargo (ex.: 0006 -> 6259, e nao 6257)."""
    cargo = cargo_code(cargo)
    for eleicao in config.eleicoes:
        if any(c.codigo == cargo for c in eleicao.cargos):
            return eleicao
    raise LookupError(f"Cargo {cargo} nao pertence a nenhuma eleicao do pleito {config.pleito}")


def get_election(config: PleitoConfig, codigo: str) -> EleicaoConfig:
    for eleicao in config.eleicoes:
        if eleicao.codigo == str(codigo):
            return eleicao
    raise LookupError(f"Eleicao {codigo} ausente do pleito {config.pleito}")


# ---------------------------------------------------------------------- URLs
def _dir(config: PleitoConfig, tp: str, **params) -> str:
    if tp not in config.templates:
        raise LookupError(f"EA11 sem template de diretorio '{tp}'")
    template = config.templates[tp]
    values = {"base": config.base_url, "ambiente": config.ambiente, "ciclo": config.ciclo,
              "cd_pleito": config.pleito, **params}
    for key, value in values.items():
        template = template.replace(f"<{key}>", str(value))
    if "<" in template:
        raise ValueError(f"Template '{tp}' com parametro nao resolvido: {template}")
    return template


def ea12_url(config: PleitoConfig, eleicao: str) -> str:
    return f"{_dir(config, 'cm', cd_eleicao=eleicao)}/mun-e{int(eleicao):06d}-cm.json"


def ea14_url(config: PleitoConfig, eleicao: str) -> str:
    return f"{_dir(config, 'ab', cd_eleicao=eleicao, uf='br')}/br-e{int(eleicao):06d}-ab.json"


def ea15_url(config: PleitoConfig, eleicao: str, uf: str) -> str:
    return f"{_dir(config, 'ab', cd_eleicao=eleicao, uf=uf)}/{uf}-e{int(eleicao):06d}-ab.json"


def ea20_url(config: PleitoConfig, eleicao: str, uf: str, cargo: str,
             municipio: str | None = None, zona: str | None = None) -> str:
    """`<uf>[<municipio>][-z<zona>]-c<cargo>-e<eleicao>-u.json`."""
    if zona and not municipio:
        raise ValueError("EA20 de zona exige municipio")
    name = uf
    if municipio:
        name += f"{int(municipio):05d}"
    if zona:
        name += f"-z{int(zona):04d}"
    name += f"-c{int(cargo):04d}-e{int(eleicao):06d}-u.json"
    return f"{_dir(config, 'u', cd_eleicao=eleicao, uf=uf)}/{name}"


def ea16_url(config: PleitoConfig, uf: str) -> str:
    return f"{_dir(config, 'cs', uf=uf)}/{uf}-p{int(config.pleito):06d}-cs.json"


def ea18_dir(config: PleitoConfig, uf: str, municipio: str, zona: str, secao: str) -> str:
    return _dir(config, "aux", uf=uf, municipio=f"{int(municipio):05d}",
                zona=f"{int(zona):04d}", secao=f"{int(secao):04d}")


def ea18_url(config: PleitoConfig, uf: str, municipio: str, zona: str, secao: str) -> str:
    name = (f"p{int(config.pleito):06d}-{uf}-m{int(municipio):05d}"
            f"-z{int(zona):04d}-s{int(secao):04d}-aux.json")
    return f"{ea18_dir(config, uf, municipio, zona, secao)}/{name}"


def urna_file_url(config: PleitoConfig, uf: str, municipio: str, zona: str, secao: str,
                  hash_: str, nome: str) -> str:
    return f"{ea18_dir(config, uf, municipio, zona, secao)}/{hash_}/{nome}"


# ---------------------------------------------------------------------- EA12
def parse_ea12(doc: dict, uf: str) -> tuple[Municipio, ...]:
    for abr in doc["abr"]:
        if abr["cd"] == uf:
            return tuple(Municipio(
                codigo=m["cd"], nome=m["nm"], codigo_ibge=m.get("cdi"),
                capital=m.get("c") == "s", zonas=tuple(m.get("z", [])),
            ) for m in abr["mu"])
    raise LookupError(f"UF {uf} ausente do EA12")


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text)
                   if unicodedata.category(c) != "Mn").casefold()


def find_municipio(municipios, *, codigo: str | None = None,
                   nome: str | None = None) -> Municipio:
    """Por codigo TSE ou por nome exato (sem acento/caixa). Ambiguidade e erro."""
    if codigo:
        found = [m for m in municipios if m.codigo == f"{int(codigo):05d}"]
    elif nome:
        found = [m for m in municipios if _fold(m.nome) == _fold(nome)]
    else:
        raise ValueError("Informe codigo ou nome do municipio")
    if len(found) != 1:
        raise LookupError(f"Municipio '{codigo or nome}' nao identificado de forma unica")
    return found[0]
