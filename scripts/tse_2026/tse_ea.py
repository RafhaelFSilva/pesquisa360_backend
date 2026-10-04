"""Leitura dos arquivos EA do TSE (POC): descoberta, URLs, parsers e reconciliacao.

Nada aqui grava em banco. Os caminhos derivam dos templates do EA11
(`arq[].dir`); somente a localizacao do proprio EA11 e fixa, por ser o ponto
de partida da descoberta.
"""

from __future__ import annotations

from pathlib import Path

BASE = "https://resultados.tse.jus.br"


# ------------------------------------------------------------------ numeros
def to_int(value) -> int | None:
    if value in (None, ""):
        return None
    return int(str(value).replace(".", ""))


def to_pct(value) -> float | None:
    if value in (None, ""):
        return None
    return float(str(value).replace(".", "").replace(",", "."))


# --------------------------------------------------------------------- EA11
def ea11_url(ambiente: str) -> str:
    return f"{BASE}/{ambiente}/comum/config/ele-c.json"


def normalize_ea11(doc: dict, ambiente: str, pleito: str) -> dict:
    """Configuracao normalizada de um pleito: ciclo, eleicoes, cargos e templates."""
    matches = [p for p in doc.get("pl", []) if p["cd"] == str(pleito)]
    if not matches:
        raise LookupError(f"Pleito {pleito} ausente do EA11")
    pl = matches[0]
    eleicoes = []
    for ele in pl["e"]:
        cargos = {}
        for abr in ele.get("abr", []):
            for cp in abr.get("cp", []):
                cargos[f"{int(cp['cd']):04d}"] = {"nome": cp["ds"], "tipo": cp["tp"],
                                                 "abrangencia": abr["cd"]}
        eleicoes.append({
            "codigo": ele["cd"], "nome": ele["nm"], "turno": to_int(ele["t"]),
            "tipo": ele["tp"], "codigo_2o_turno": ele.get("cdt2") or None,
            "abrangencias": [a["cd"] for a in ele.get("abr", [])],
            "cargos": cargos,
        })
    return {
        "host": BASE,
        "ambiente": ambiente,
        "fase": doc.get("f"),
        "gerado_em": f"{doc.get('dg')} {doc.get('hg')}",
        "idg": doc.get("idg"),
        "ciclo": pl["c"],
        "pleito": pl["cd"],
        "pleito_data": pl["dt"],
        "eleicoes": eleicoes,
        "templates": {a["tp"]: a["dir"] for a in doc.get("arq", [])},
    }


def election_for_cargo(config: dict, cargo: str) -> dict:
    for ele in config["eleicoes"]:
        if cargo in ele["cargos"]:
            return ele
    raise LookupError(f"Cargo {cargo} nao pertence a nenhuma eleicao do pleito {config['pleito']}")


def _dir(config: dict, tp: str, **params) -> str:
    template = config["templates"][tp]
    values = {"base": BASE, "ambiente": config["ambiente"], "ciclo": config["ciclo"],
              "cd_pleito": config["pleito"], **params}
    for key, value in values.items():
        template = template.replace(f"<{key}>", str(value))
    if "<" in template:
        raise ValueError(f"Template '{tp}' com parametro nao resolvido: {template}")
    return template


# ---------------------------------------------------------------------- URLs
def ea12_url(config, eleicao):
    return f"{_dir(config, 'cm', cd_eleicao=eleicao)}/mun-e{int(eleicao):06d}-cm.json"


def ea14_url(config, eleicao):
    return f"{_dir(config, 'ab', cd_eleicao=eleicao, uf='br')}/br-e{int(eleicao):06d}-ab.json"


def ea15_url(config, eleicao, uf):
    return f"{_dir(config, 'ab', cd_eleicao=eleicao, uf=uf)}/{uf}-e{int(eleicao):06d}-ab.json"


def ea20_url(config, eleicao, uf, cargo, municipio=None, zona=None):
    """<uf>[<municipio>][-z<zona>]-c<cargo>-e<eleicao>-u.json"""
    if zona and not municipio:
        raise ValueError("EA20 de zona exige municipio")
    name = uf
    if municipio:
        name += f"{int(municipio):05d}"
    if zona:
        name += f"-z{int(zona):04d}"
    name += f"-c{int(cargo):04d}-e{int(eleicao):06d}-u.json"
    return f"{_dir(config, 'u', cd_eleicao=eleicao, uf=uf)}/{name}"


def ea16_url(config, uf):
    return f"{_dir(config, 'cs', uf=uf)}/{uf}-p{int(config['pleito']):06d}-cs.json"


def ea18_dir(config, uf, municipio, zona, secao):
    return _dir(config, "aux", uf=uf, municipio=f"{int(municipio):05d}",
                zona=f"{int(zona):04d}", secao=f"{int(secao):04d}")


def ea18_url(config, uf, municipio, zona, secao):
    name = (f"p{int(config['pleito']):06d}-{uf}-m{int(municipio):05d}"
            f"-z{int(zona):04d}-s{int(secao):04d}-aux.json")
    return f"{ea18_dir(config, uf, municipio, zona, secao)}/{name}"


def urna_file_url(config, uf, municipio, zona, secao, hash_, nome):
    return f"{ea18_dir(config, uf, municipio, zona, secao)}/{hash_}/{nome}"


# ---------------------------------------------------------------------- EA12
def municipios_da_uf(ea12: dict, uf: str) -> list[dict]:
    for abr in ea12["abr"]:
        if abr["cd"] == uf:
            return [{"codigo": m["cd"], "codigo_ibge": m.get("cdi"), "nome": m["nm"],
                     "capital": m.get("c") == "s", "zonas": list(m.get("z", []))}
                    for m in abr["mu"]]
    raise LookupError(f"UF {uf} ausente do EA12")


def find_municipio(municipios: list[dict], nome: str | None) -> dict:
    """Por nome exato (sem acento/caixa); sem nome, a capital da UF."""
    import unicodedata

    def fold(text):
        return "".join(c for c in unicodedata.normalize("NFD", text)
                       if unicodedata.category(c) != "Mn").casefold()

    if nome:
        found = [m for m in municipios if fold(m["nome"]) == fold(nome)]
    else:
        found = [m for m in municipios if m["capital"]]
    if len(found) != 1:
        raise LookupError(f"Municipio '{nome or 'capital'}' nao identificado de forma unica")
    return found[0]


# ----------------------------------------------------------------- EA14/EA15
def parse_acompanhamento(doc: dict) -> dict:
    """EA14 (abrangencias = UFs + br) e EA15 (municipios + uf) tem o mesmo contrato."""
    linhas = []
    for abr in doc["abr"]:
        s, e = abr.get("s", {}), abr.get("e", {})
        linhas.append({
            "tpabr": abr["tpabr"], "cdabr": abr["cdabr"], "andamento": abr.get("and"),
            "ultima_totalizacao": f"{abr.get('dt', '')} {abr.get('ht', '')}".strip() or None,
            "secoes": to_int(s.get("ts")), "secoes_totalizadas": to_int(s.get("st")),
            "pct_secoes_totalizadas": to_pct(s.get("pst")),
            "eleitorado": to_int(e.get("te")), "comparecimento": to_int(e.get("c")),
            "abstencao": to_int(e.get("a")),
            "municipios_nao_recebidos": to_int(abr.get("munnr")),
            "municipios_parciais": to_int(abr.get("munpt")),
            "municipios_finalizados": to_int(abr.get("munf")),
        })
    return {"eleicao": doc.get("ele"), "turno": to_int(doc.get("t")), "fase": doc.get("f"),
            "gerado_em": f"{doc.get('dg')} {doc.get('hg')}", "idg": doc.get("idg"),
            "abrangencias": linhas}


def acompanhamento_fingerprints(parsed: dict) -> dict[str, str]:
    """Impressao por abrangencia: se mudar, a abrangencia precisa de nova consulta."""
    return {
        f"{l['tpabr']}:{l['cdabr']}": "|".join(str(l[k]) for k in (
            "andamento", "ultima_totalizacao", "secoes_totalizadas", "comparecimento"))
        for l in parsed["abrangencias"]
    }


def changed_abrangencias(previous: dict[str, str] | None, current: dict[str, str]) -> list[str]:
    if previous is None:
        return sorted(current)
    return sorted(k for k, v in current.items() if previous.get(k) != v)


# ---------------------------------------------------------------------- EA20
def parse_ea20(doc: dict) -> dict:
    s, e, v = doc.get("s", {}), doc.get("e", {}), doc.get("v", {})
    carg = doc["carg"][0]
    candidatos = []
    for agr in carg.get("agr", []):
        for par in agr.get("par", []):
            for cand in par.get("cand", []):
                candidatos.append({
                    "sqcand": cand["sqcand"], "numero": cand["n"], "nome": cand["nm"],
                    "nome_urna": cand["nmu"], "situacao": cand.get("st") or None,
                    "eleito": cand.get("e"), "votos": to_int(cand.get("vap")),
                    "percentual": to_pct(cand.get("pvap")),
                    "partido": par["sg"], "partido_numero": par["n"],
                    "agremiacao": agr["com"], "agremiacao_tipo": agr["tp"],
                })
    return {
        "eleicao": doc.get("ele"), "turno": to_int(doc.get("t")),
        "tpabr": doc.get("tpabr"), "cdabr": doc.get("cdabr"),
        "gerado_em": f"{doc.get('dg')} {doc.get('hg')}", "idg": doc.get("idg"),
        "ultima_totalizacao": f"{doc.get('dt', '')} {doc.get('ht', '')}".strip() or None,
        "andamento": doc.get("and"), "totalizacao_final": doc.get("tf"),
        "secoes": to_int(s.get("ts")), "secoes_totalizadas": to_int(s.get("st")),
        "eleitorado": to_int(e.get("te")), "comparecimento": to_int(e.get("c")),
        "abstencao": to_int(e.get("a")),
        "votos": {"total": to_int(v.get("tv")), "validos": to_int(v.get("vv")),
                  "nominais": to_int(v.get("vnom")), "legenda": to_int(v.get("vl")),
                  "brancos": to_int(v.get("vb")), "nulos": to_int(v.get("tvn"))},
        "cargo": f"{int(carg['cd']):04d}", "cargo_nome": carg.get("nmn"),
        "vagas": to_int(carg.get("nv")), "quociente_eleitoral": to_int(carg.get("qe")),
        "federacoes": [{"numero": f["n"], "sigla": f["sg"], "nome": f["nm"],
                        "partidos": f.get("npar", [])} for f in carg.get("fed", [])],
        "partidos": [{"numero": p["n"], "sigla": p["sg"], "agremiacao": a["com"],
                      "votos_nominais": to_int(p.get("tvtn")),
                      "votos_legenda": to_int(p.get("tvtl"))}
                     for a in carg.get("agr", []) for p in a.get("par", [])],
        "candidatos": candidatos,
    }


def get_candidate_from_ea20(parsed: dict, sqcand: str | None = None,
                            numero_candidato: str | None = None) -> dict | None:
    """Localiza por `sqcand`; `numero_candidato` e apenas fallback tecnico.

    Nome nunca e usado como identificador. Numero ambiguo levanta erro em vez
    de escolher um candidato arbitrariamente.
    """
    if sqcand:
        for cand in parsed["candidatos"]:
            if cand["sqcand"] == str(sqcand):
                return cand
        return None
    if numero_candidato:
        found = [c for c in parsed["candidatos"] if c["numero"] == str(numero_candidato)]
        if len(found) > 1:
            raise LookupError(f"Numero {numero_candidato} ambiguo no EA20 {parsed['cdabr']}")
        return found[0] if found else None
    raise ValueError("Informe sqcand ou numero_candidato")


# ---------------------------------------------------------------------- EA16
def parse_ea16(doc: dict, uf: str) -> dict:
    """UF -> Municipio -> Zona -> Secoes, preservando principal/agregada.

    `nsa` (na principal) lista as secoes agregadas a ela; `nsp` (na agregada)
    aponta a principal. Secao agregada nao tem urna propria: seus votos estao
    no BU da principal e ela nao deve ser contada como urna independente.
    """
    abr = next(a for a in doc["abr"] if a["cd"] == uf)
    municipios = {}
    for mu in abr["mu"]:
        zonas = {}
        for zon in mu["zon"]:
            zonas[zon["cd"]] = [{
                "secao": sec["ns"],
                "tipo": "agregada" if sec.get("nsp") else "principal",
                "principal": sec.get("nsp"),
                "agregadas": list(sec.get("nsa", [])),
            } for sec in zon["sec"]]
        municipios[mu["cd"]] = {"nome": mu["nm"], "zonas": zonas}
    return {"pleito": doc.get("cdp"), "gerado_em": f"{doc.get('dg')} {doc.get('hg')}",
            "idg": doc.get("idg"), "municipios": municipios}


def ea16_stats(parsed: dict) -> dict:
    secoes = [s for m in parsed["municipios"].values() for z in m["zonas"].values() for s in z]
    return {
        "municipios": len(parsed["municipios"]),
        "zonas_por_municipio": {c: sorted(m["zonas"]) for c, m in parsed["municipios"].items()},
        "secoes": len(secoes),
        "principais": sum(1 for s in secoes if s["tipo"] == "principal"),
        "principais_com_agregadas": sum(1 for s in secoes if s["agregadas"]),
        "agregadas": sum(1 for s in secoes if s["tipo"] == "agregada"),
    }


# ---------------------------------------------------------------------- EA18
def parse_ea18(doc: dict) -> dict:
    return {
        "gerado_em": f"{doc.get('dg')} {doc.get('hg')}", "idg": doc.get("idg"),
        "situacao_secao": doc.get("st"),
        "hashes": [{
            "hash": h["hash"], "situacao": h.get("st"),
            "recebido_em": f"{h.get('dr', '')} {h.get('hr', '')}".strip() or None,
            "arquivos": [{"nome": a["nm"], "tipo": a["tp"]} for a in h.get("arq", [])],
        } for h in doc.get("hashes", [])],
    }


def current_bu(parsed_ea18: dict) -> dict | None:
    """BU do hash com situacao 'Totalizado'; hashes em outra situacao sao ignorados."""
    for h in parsed_ea18["hashes"]:
        if (h["situacao"] or "").casefold() == "totalizado":
            for arq in h["arquivos"]:
                if arq["tipo"] == "bu":
                    return {"hash": h["hash"], "nome": arq["nome"], "recebido_em": h["recebido_em"]}
    return None


# ------------------------------------------------------------------------ BU
def sniff_bu(data: bytes) -> dict:
    """Identifica o formato pelo cabecalho TLV (X.690), sem interpretar o conteudo."""
    info = {"bytes": len(data), "primeiros_bytes_hex": data[:16].hex(" "), "json": False,
            "asn1_ber_sequence": False}
    if data[:1] in (b"{", b"["):
        info["json"] = True
        return info
    if len(data) > 2 and data[0] == 0x30:
        first = data[1]
        if first < 0x80:
            header, length = 2, first
        else:
            n = first & 0x7F
            header, length = 2 + n, int.from_bytes(data[2:2 + n], "big")
        info["asn1_ber_sequence"] = header + length == len(data)
        info["tlv_comprimento_declarado"] = length
    return info


def decode_bu_with_official_spec(data: bytes, spec_path: Path) -> dict:
    """Decodifica o BU com a especificacao ASN.1 oficial do TSE (`bu.asn1`).

    Segue o procedimento do exemplo Python que o TSE distribui junto da
    especificacao: envelope generico e, dentro dele, o boletim de urna. So
    roda com a especificacao oficial em maos; a POC nao embute estrutura
    alguma do BU.
    """
    import asn1tools  # dependencia opcional, exclusiva desta etapa

    conv = asn1tools.compile_files(str(spec_path), codec="ber")
    envelope = conv.decode("EntidadeEnvelopeGenerico", data)
    return conv.decode("EntidadeBoletimUrna", envelope["conteudo"])


# -------------------------------------------------------------- reconciliacao
def reconcile_candidate_votes(candidate: dict, scope: str, official: dict,
                              derived_parts: list[dict]) -> dict:
    """Compara o voto oficial (EA20) com a soma de partes (zonas ou secoes).

    `official` e cada item de `derived_parts`: {"votos", "idg", "gerado_em",
    "ultima_totalizacao", "ref"}. Divergencia so e classificada como
    inconsistencia quando oficial e partes vem da mesma janela de totalizacao;
    caso contrario fica registrada como possivel defasagem temporal.
    """
    missing = [p["ref"] for p in derived_parts if p.get("votos") is None]
    derived = None if missing or not derived_parts else sum(p["votos"] for p in derived_parts)
    official_votes = official.get("votos")
    comparable = derived is not None and official_votes is not None
    difference = (official_votes - derived) if comparable else None

    janelas = {official.get("ultima_totalizacao")} | {p.get("ultima_totalizacao") for p in derived_parts}
    mesma_janela = len(janelas) == 1

    if not comparable:
        diagnostico = "nao_comparavel: ha parte sem voto do candidato"
    elif difference == 0:
        diagnostico = "consistente"
    elif not mesma_janela:
        diagnostico = "divergente_com_defasagem_temporal: arquivos de janelas de totalizacao distintas"
    else:
        diagnostico = "divergente_na_mesma_janela: investigar"

    return {
        "candidate": {k: candidate.get(k) for k in ("sqcand", "numero", "nome_urna")},
        "scope": scope,
        "official_votes": official_votes,
        "derived_votes": derived,
        "difference": difference,
        "consistent": comparable and difference == 0,
        "official_idg": official.get("idg"),
        "official_generated_at": official.get("gerado_em"),
        "official_last_totalization": official.get("ultima_totalizacao"),
        "same_totalization_window": mesma_janela,
        "diagnosis": diagnostico,
        "parts": [{k: p.get(k) for k in ("ref", "votos", "idg", "gerado_em", "ultima_totalizacao")}
                  for p in derived_parts],
        "parts_missing_candidate": missing,
    }
