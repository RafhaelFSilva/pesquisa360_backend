"""Sonda o ambiente oficial de SIMULACAO do TSE usando a camada de servico.

Somente leitura, sem banco. Salva os arquivos brutos em
artifacts/tse_2026/simulado/ (ignorado pelo Git) e imprime um resumo JSON.

    python scripts/tse_2026/sim_probe.py [--uf ap] [--cargo 0006]
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pesquisa360.services.tse import acompanhamento, bu, discovery, ea20, sections  # noqa: E402
from pesquisa360.services.tse import reconciliation as rec  # noqa: E402
from pesquisa360.services.tse.client import TseClient  # noqa: E402
from pesquisa360.services.tse.config import TseSettings  # noqa: E402

OUT = ROOT / "artifacts" / "tse_2026" / "simulado"
SETTINGS = TseSettings(base_url="https://resultados-sim.tse.jus.br/simulado",
                       ambiente="simulado2026")
PLEITO = "17801"


def fetch(client, kind, url, binary=False):
    resp = client.get(url)
    print(f"  HTTP {resp.status} {resp.elapsed_ms:6.0f}ms {kind:6} {url}", file=sys.stderr)
    if resp.content is None:
        return None
    sha = hashlib.sha256(resp.content).hexdigest()
    name = Path(url).name
    stem, _, suffix = name.rpartition(".")
    target = OUT / kind / f"{stem}__{sha[:12]}.{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(resp.content)
    return resp.content if binary else json.loads(resp.content.decode("utf-8"))


def ponto(resultado, sqcand, ref):
    cand = ea20.get_candidate(resultado, sqcand=sqcand)
    return rec.VotePoint(ref=ref, votes=cand.votos if cand else None, idg=resultado.idg,
                         generated_at=resultado.gerado_em,
                         last_totalization=resultado.ultima_totalizacao,
                         sections_counted=resultado.secoes_totalizadas)


def valores(doc_raw, out):
    """Distribuicao dos campos de situacao, para achar cenarios especiais."""
    for chave in ("and", "tf", "sup", "dv", "esae", "mnae", "md"):
        if chave in doc_raw:
            out[f"doc.{chave}"][json.dumps(doc_raw[chave], ensure_ascii=False)[:60]] += 1
    for carg in doc_raw.get("carg", []):
        for agr in carg.get("agr", []):
            for par in agr.get("par", []):
                for k, v in par.items():
                    if k not in ("cand", "n", "sg", "nm", "nfed") and not k.startswith(("tv", "ptv")):
                        out[f"par.{k}"][str(v)] += 1
                for cand in par.get("cand", []):
                    for k in cand:
                        if k not in ("n", "sqcand", "nm", "nmu", "dt", "seq", "vap", "pvap",
                                     "pvapn", "vs"):
                            out[f"cand.{k}"][json.dumps(cand[k], ensure_ascii=False)[:40]] += 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--uf", default="ap")
    p.add_argument("--cargo", default="0006")
    p.add_argument("--max-ea18", type=int, default=3)
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    r: dict = {"ambiente": "SIMULADO", "base_url": SETTINGS.base_url}

    with TseClient(settings=SETTINGS) as client:
        ea11 = fetch(client, "ea11", discovery.ea11_url(SETTINGS.base_url, SETTINGS.ambiente))
        config = discovery.parse_ea11(ea11, base_url=SETTINGS.base_url,
                                      ambiente=SETTINGS.ambiente, pleito=PLEITO)
        eleicao = discovery.election_for_cargo(config, args.cargo).codigo
        r["config"] = {"fase": config.fase, "ciclo": config.ciclo, "pleito": config.pleito,
                       "data": config.data, "eleicao_do_cargo": eleicao,
                       "eleicoes": {e.codigo: [c.codigo for c in e.cargos] for e in config.eleicoes}}

        ea14_raw = fetch(client, "ea14", discovery.ea14_url(config, eleicao))
        ea14 = acompanhamento.parse_acompanhamento(ea14_raw)
        r["ea14"] = {"idg": ea14.idg, "gerado_em": str(ea14.gerado_em), "ufs": {
            l.codigo: f"{l.andamento} {l.secoes_totalizadas}/{l.secoes} {l.ultima_totalizacao}"
            for l in ea14.linhas}}
        r["ea14_chaves_brutas"] = {"linha": sorted(ea14_raw["abr"][0]),
                                   "s": sorted(ea14_raw["abr"][0]["s"]),
                                   "e": sorted(ea14_raw["abr"][0]["e"])}
        uf_linha = next(l for l in ea14.linhas if l.codigo == args.uf)
        if not uf_linha.secoes_totalizadas:
            r["erro"] = f"UF {args.uf} sem secoes totalizadas no simulado"
            print(json.dumps(r, ensure_ascii=False, indent=1))
            return 2
        r["ea14_uf_bruto"] = next(a for a in ea14_raw["abr"] if a["cdabr"] == args.uf)

        ea15_raw = fetch(client, "ea15", discovery.ea15_url(config, eleicao, args.uf))
        ea15 = acompanhamento.parse_acompanhamento(ea15_raw)
        r["ea15"] = {"idg": ea15.idg, "gerado_em": str(ea15.gerado_em), "linhas": {
            l.chave: f"{l.andamento} {l.secoes_totalizadas}/{l.secoes} {l.ultima_totalizacao}"
            for l in ea15.linhas}}

        municipios = discovery.parse_ea12(
            fetch(client, "ea12", discovery.ea12_url(config, eleicao)), args.uf)
        capital = next(m for m in municipios if m.capital)
        r["ea12"] = {"municipios": len(municipios), "capital": [capital.codigo, capital.nome,
                                                               list(capital.zonas)]}

        situacoes = collections.defaultdict(collections.Counter)
        uf_raw = fetch(client, "ea20", discovery.ea20_url(config, eleicao, args.uf, args.cargo))
        valores(uf_raw, situacoes)
        uf = ea20.parse_ea20(uf_raw)
        alvo = max(uf.candidatos, key=lambda c: c.votos or 0)
        partido = next(pt for pt in uf.partidos if pt.numero == alvo.partido_numero)
        r["ea20_uf"] = {
            "tpabr": uf.tipo_abrangencia, "cdabr": uf.codigo_abrangencia, "idg": uf.idg,
            "gerado_em": str(uf.gerado_em), "ultima_totalizacao": str(uf.ultima_totalizacao),
            "andamento": uf.andamento, "tf": uf.totalizacao_final,
            "secoes": f"{uf.secoes_totalizadas}/{uf.secoes}", "eleitorado": uf.eleitorado,
            "comparecimento": uf.comparecimento, "abstencao": uf.abstencao,
            "votos": {"total": uf.votos_total, "validos": uf.votos_validos,
                      "nominais": uf.votos_nominais, "legenda": uf.votos_legenda,
                      "brancos": uf.votos_brancos, "nulos": uf.votos_nulos},
            "vagas": uf.vagas, "qe": uf.quociente_eleitoral, "candidatos": len(uf.candidatos),
            "com_votos": sum(1 for c in uf.candidatos if c.votos),
            "federacoes": len(uf.federacoes), "partidos": len(uf.partidos),
            "soma_candidatos": sum(c.votos or 0 for c in uf.candidatos),
            "chaves_topo": sorted(uf_raw), "chaves_carg": sorted(uf_raw["carg"][0]),
            "chaves_v": sorted(uf_raw.get("v", {})),
        }
        r["candidato"] = {**{k: getattr(alvo, k) for k in (
            "sqcand", "numero", "nome", "nome_urna", "situacao", "eleito", "votos", "percentual")},
            "partido": partido.sigla, "federacao": partido.federacao_numero, "cargo": uf.cargo}

        mun_raw = fetch(client, "ea20", discovery.ea20_url(
            config, eleicao, args.uf, args.cargo, capital.codigo))
        valores(mun_raw, situacoes)
        mun = ea20.parse_ea20(mun_raw)
        zonas = []
        for zona in capital.zonas:
            z_raw = fetch(client, "ea20", discovery.ea20_url(
                config, eleicao, args.uf, args.cargo, capital.codigo, zona))
            if z_raw:
                valores(z_raw, situacoes)
                zonas.append((zona, ea20.parse_ea20(z_raw)))
        r["votos_por_abrangencia"] = {
            "uf": [uf.idg, str(uf.gerado_em), alvo.votos],
            "municipio": [mun.tipo_abrangencia, mun.codigo_abrangencia, mun.idg,
                          str(mun.gerado_em), f"{mun.secoes_totalizadas}/{mun.secoes}",
                          ea20.get_candidate(mun, sqcand=alvo.sqcand).votos],
            "zonas": [[z.tipo_abrangencia, z.codigo_abrangencia, z.idg, str(z.gerado_em),
                       f"{z.secoes_totalizadas}/{z.secoes}",
                       ea20.get_candidate(z, sqcand=alvo.sqcand).votos] for _n, z in zonas],
        }
        r["reconciliacao_zonas_x_municipio"] = rec.reconcile_candidate_votes(
            alvo.sqcand, "municipio", ponto(mun, alvo.sqcand, capital.codigo),
            [ponto(z, alvo.sqcand, n) for n, z in zonas], expected_parts=len(capital.zonas))
        r["situacoes"] = {k: dict(v) for k, v in sorted(situacoes.items())}

        ea16_raw = fetch(client, "ea16", discovery.ea16_url(config, args.uf))
        cfg = sections.parse_ea16(ea16_raw, args.uf)
        todas = [s for m in cfg.municipios for s in m.secoes]
        chaves = collections.Counter(
            tuple(sorted(sec)) for mu in ea16_raw["abr"][0]["mu"] for zon in mu["zon"]
            for sec in zon["sec"])
        r["ea16"] = {"idg": cfg.idg, "pleito": cfg.pleito, "municipios": len(cfg.municipios),
                     "secoes": len(todas), "principais": sum(s.eh_principal for s in todas),
                     "agregadas": sum(s.eh_agregada for s in todas),
                     "chaves_secao": {",".join(k): v for k, v in chaves.items()}}

        mun_secoes = next(m for m in cfg.municipios if m.codigo == capital.codigo)
        r["ea18"] = {"tentativas": []}
        for secao in [s for s in mun_secoes.secoes if s.eh_principal][: args.max_ea18]:
            url = discovery.ea18_url(config, args.uf, secao.municipio, secao.zona, secao.secao)
            aux_raw = fetch(client, "ea18", url)
            r["ea18"]["tentativas"].append([secao.zona, secao.secao, aux_raw is not None])
            if aux_raw is None:
                continue
            aux = sections.parse_ea18(aux_raw)
            r["ea18"].update({"url": url, "bruto": aux_raw})
            atual = sections.current_bu(aux)
            if atual:
                hash_, arquivo = atual
                bu_url = discovery.urna_file_url(config, args.uf, secao.municipio, secao.zona,
                                                 secao.secao, hash_.hash, arquivo.nome)
                data = fetch(client, "bu", bu_url, binary=True)
                r["bu"] = {"url": bu_url, "nome": arquivo.nome,
                           "disponivel": data is not None}
                if data is not None:
                    r["bu"].update({"sha256": hashlib.sha256(data).hexdigest(),
                                    "primeiros_bytes": data[:8].hex(" "), **bu.sniff(data)})
            break

        r["http"] = {"requisicoes": len(client.requests),
                     "pico_por_segundo": client.peak_requests_per_second(),
                     "status": dict(collections.Counter(s for _t, _u, s in client.requests))}

    print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
