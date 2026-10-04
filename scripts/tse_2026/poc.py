"""POC de ingestao da apuracao TSE 2026 (somente leitura, sem banco).

Uso (a partir da raiz do backend):

    py -3.13 scripts/tse_2026/poc.py
    py -3.13 scripts/tse_2026/poc.py --sqcand 30002538086
    py -3.13 scripts/tse_2026/poc.py --historic-pleito 452 --bu-spec caminho/bu.asn1

Os JSON oficiais consultados ficam em artifacts/tse_2026/ (ignorado pelo Git).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tse_ea as ea  # noqa: E402
from tse_http import TseHttpClient, result_summary  # noqa: E402

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = BACKEND_ROOT / "artifacts" / "tse_2026"


class Poc:
    def __init__(self, args):
        self.args = args
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        gitignore = ARTIFACTS / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n", encoding="utf-8")
        self.http = TseHttpClient(ARTIFACTS, max_rps=args.max_rps)
        self.report: dict = {"run_id": self.http.run_id, "urls": {}}
        self.tests: dict[str, dict] = {}

    # ------------------------------------------------------------ utilidades
    def fetch_json(self, name: str, url: str, kind: str):
        res = self.http.get(url, kind)
        self.report["urls"][name] = result_summary(res)
        if not res.ok:
            return None
        doc = res.json()
        if isinstance(doc, dict) and doc.get("idg"):
            self.http.annotate_last(idg=doc["idg"])
            self.report["urls"][name]["idg"] = doc["idg"]
        return doc

    def test(self, number: int, title: str, status: str, detail: str = "", suffix: str = ""):
        key = f"T{number:02d}{suffix}"
        self.tests[key] = {"titulo": title, "status": status, "detalhe": detail}
        print(f"  [{key:4}] {status:8} {title}" + (f" — {detail}" if detail else ""))

    # ----------------------------------------------------------------- fluxo
    def run(self) -> int:
        a = self.args
        print(f"== POC TSE 2026 | run {self.http.run_id} | limite {a.max_rps} req/s")

        ea11 = self.fetch_json("ea11", ea.ea11_url(a.ambiente), "ea11")
        if ea11 is None:
            self.test(1, "EA11 acessivel e parseavel", "FALHOU", "sem resposta 200")
            return self.finish()
        config = ea.normalize_ea11(ea11, a.ambiente, a.pleito)
        self.report["config"] = config
        self.test(1, "EA11 acessivel e parseavel", "OK",
                  f"ciclo {config['ciclo']}, pleito {config['pleito']}, "
                  f"eleicoes {[e['codigo'] for e in config['eleicoes']]}")

        eleicao = ea.election_for_cargo(config, a.cargo)["codigo"]
        self.report["alvo"] = {"uf": a.uf, "cargo": a.cargo, "eleicao": eleicao}

        ea12 = self.fetch_json("ea12", ea.ea12_url(config, eleicao), "ea12")
        municipios = ea.municipios_da_uf(ea12, a.uf)
        municipio = ea.find_municipio(municipios, a.municipio)
        self.report["ea12"] = {"municipios": municipios, "municipio_escolhido": municipio}

        self.step_acompanhamento(config, eleicao)
        candidate = self.step_ea20(config, eleicao, municipio)
        ea16 = self.step_ea16(config, municipio)
        if ea16:
            self.step_secao(config, ea16, municipio, label="2026")
            if a.historic_pleito and not self.report["secao_2026"].get("bu"):
                self.step_historico(ea11, municipio)
        self.report["consolidacao"] = self.consolidacao(candidate)
        return self.finish()

    def step_acompanhamento(self, config, eleicao):
        a = self.args
        for number, name, url, label in (
            (2, "ea14", ea.ea14_url(config, eleicao), "EA14 parseavel"),
            (3, "ea15", ea.ea15_url(config, eleicao, a.uf), f"EA15/{a.uf.upper()} parseavel"),
        ):
            doc = self.fetch_json(name, url, name)
            if doc is None:
                self.test(number, label, "FALHOU", f"HTTP {self.report['urls'][name]['status']}")
                continue
            parsed = ea.parse_acompanhamento(doc)
            prints = ea.acompanhamento_fingerprints(parsed)
            state_path = ARTIFACTS / "_state" / f"{name}-{eleicao}-{a.uf}.json"
            state_path.parent.mkdir(exist_ok=True)
            previous = json.loads(state_path.read_text("utf-8")) if state_path.exists() else None
            changed = ea.changed_abrangencias(previous, prints)
            state_path.write_text(json.dumps(prints, indent=1), encoding="utf-8")
            self.report[name] = {**parsed, "alteradas_desde_execucao_anterior": changed,
                                 "havia_estado_anterior": previous is not None}
            self.test(number, label, "OK",
                      f"idg {parsed['idg']}, {len(parsed['abrangencias'])} abrangencias, "
                      f"{len(changed)} alteradas desde a execucao anterior")

    def step_ea20(self, config, eleicao, municipio):
        a = self.args
        uf_doc = self.fetch_json("ea20_uf", ea.ea20_url(config, eleicao, a.uf, a.cargo), "ea20")
        if uf_doc is None:
            self.test(4, "EA20 UF parseavel", "FALHOU")
            return None
        uf = ea.parse_ea20(uf_doc)
        self.report["ea20_uf"] = {k: v for k, v in uf.items() if k != "candidatos"}
        self.report["ea20_uf"]["total_candidatos"] = len(uf["candidatos"])
        self.test(4, f"EA20/{a.uf.upper()} cargo {a.cargo} parseavel", "OK",
                  f"idg {uf['idg']}, {len(uf['candidatos'])} candidatos, "
                  f"secoes {uf['secoes_totalizadas']}/{uf['secoes']}")

        if a.sqcand or a.numero:
            candidate = ea.get_candidate_from_ea20(uf, a.sqcand, a.numero)
            criterio = "informado por parametro"
        else:
            # Sem candidato informado: o mais votado; antes da totalizacao, o primeiro do arquivo.
            candidate = max(uf["candidatos"], key=lambda c: c["votos"] or 0)
            criterio = "mais votado no EA20 UF (primeiro do arquivo enquanto zerado)"
        if candidate is None:
            self.test(5, "Candidato encontrado no EA20", "FALHOU", "sqcand/numero inexistente")
            return None
        self.report["candidato"] = {**candidate, "criterio_de_escolha": criterio}
        self.test(5, "Candidato encontrado no EA20", "OK",
                  f"sqcand {candidate['sqcand']} n {candidate['numero']} {candidate['nome_urna']}")

        def linha(parsed, ref):
            cand = ea.get_candidate_from_ea20(parsed, sqcand=candidate["sqcand"])
            return {"ref": ref, "votos": cand["votos"] if cand else None,
                    "percentual": cand["percentual"] if cand else None, "idg": parsed["idg"],
                    "gerado_em": parsed["gerado_em"],
                    "ultima_totalizacao": parsed["ultima_totalizacao"],
                    "secoes": parsed["secoes"], "secoes_totalizadas": parsed["secoes_totalizadas"]}

        tabela = {"uf": linha(uf, a.uf)}

        mun_doc = self.fetch_json(
            "ea20_municipio", ea.ea20_url(config, eleicao, a.uf, a.cargo, municipio["codigo"]), "ea20")
        if mun_doc is None:
            self.test(6, "Candidato consultado em um municipio", "FALHOU")
        else:
            tabela["municipio"] = linha(ea.parse_ea20(mun_doc), municipio["codigo"])
            self.test(6, "Candidato consultado em um municipio", "OK",
                      f"{municipio['nome']} ({municipio['codigo']}): {tabela['municipio']['votos']} votos")

        zonas = []
        for zona in municipio["zonas"]:
            doc = self.fetch_json(
                f"ea20_zona_{zona}",
                ea.ea20_url(config, eleicao, a.uf, a.cargo, municipio["codigo"], zona), "ea20")
            if doc is not None:
                zonas.append(linha(ea.parse_ea20(doc), zona))
        tabela["zonas"] = zonas
        if zonas:
            self.test(7, "Candidato consultado em uma zona", "OK",
                      "; ".join(f"z{z['ref']}={z['votos']}" for z in zonas))
        else:
            self.test(7, "Candidato consultado em uma zona", "FALHOU")
        self.report["candidato_por_abrangencia"] = tabela

        if "municipio" in tabela and len(zonas) == len(municipio["zonas"]):
            self.report["reconciliacao_zonas_x_municipio"] = ea.reconcile_candidate_votes(
                candidate, f"municipio {municipio['codigo']} = soma das zonas",
                tabela["municipio"], zonas)
        return candidate

    def step_ea16(self, config, municipio):
        a = self.args
        doc = self.fetch_json("ea16", ea.ea16_url(config, a.uf), "ea16")
        if doc is None:
            self.test(8, "EA16 lista Municipio -> Zona -> Secao", "FALHOU")
            return None
        parsed = ea.parse_ea16(doc, a.uf)
        stats = ea.ea16_stats(parsed)
        mun = parsed["municipios"][municipio["codigo"]]
        exemplo_agregacao = next(
            ({"zona": z, **s} for z, secs in mun["zonas"].items() for s in secs if s["agregadas"]), None)
        self.report["ea16"] = {
            "gerado_em": parsed["gerado_em"], "idg": parsed["idg"], "estatisticas": stats,
            "zonas_ea12_x_ea16_conferem": sorted(municipio["zonas"]) == sorted(mun["zonas"]),
            "municipio": {"codigo": municipio["codigo"], "nome": mun["nome"],
                          "secoes_por_zona": {z: len(s) for z, s in mun["zonas"].items()},
                          "principais_por_zona": {z: sum(1 for x in s if x["tipo"] == "principal")
                                                  for z, s in mun["zonas"].items()}},
            "exemplo_agregacao": exemplo_agregacao,
        }
        self.test(8, "EA16 lista Municipio -> Zona -> Secao", "OK",
                  f"{stats['municipios']} municipios, {stats['secoes']} secoes "
                  f"({stats['principais']} principais, {stats['agregadas']} agregadas)")
        return parsed

    def step_secao(self, config, ea16, municipio, label):
        """Procura o EA18 de UMA secao principal; no maximo `--max-section-probes` tentativas."""
        a = self.args
        out: dict = {"tentativas": []}
        self.report[f"secao_{label}"] = out
        mun = ea16["municipios"].get(municipio["codigo"])
        if mun is None:
            out["erro"] = "municipio ausente do EA16"
            return out
        zona = a.zona or sorted(mun["zonas"])[0]
        principais = [s for s in mun["zonas"][zona] if s["tipo"] == "principal"]
        found = None
        for sec in principais[: a.max_section_probes]:
            url = ea.ea18_url(config, a.uf, municipio["codigo"], zona, sec["secao"])
            doc = self.fetch_json(f"ea18_{label}_s{sec['secao']}", url, "ea18")
            out["tentativas"].append({"secao": sec["secao"], "url": url,
                                      "status": self.http.request_log[-1]["status"]})
            if doc is not None:
                found = (sec, url, ea.parse_ea18(doc))
                break
        if not found:
            out["diagnostico"] = (f"nenhum EA18 disponivel nas {len(out['tentativas'])} secoes "
                                  "principais sondadas (esperado antes da totalizacao)")
            return out
        sec, url, parsed = found
        out.update({"zona": zona, "secao": sec, "ea18_url": url, "ea18": parsed})
        bu = ea.current_bu(parsed)
        if not bu:
            out["diagnostico"] = "EA18 sem hash em situacao 'Totalizado' com arquivo de BU"
            return out
        bu_url = ea.urna_file_url(config, a.uf, municipio["codigo"], zona, sec["secao"],
                                  bu["hash"], bu["nome"])
        res = self.http.get(bu_url, "bu")
        self.report["urls"][f"bu_{label}"] = result_summary(res)
        out["bu"] = {**bu, "url": bu_url, "status": res.status}
        if res.ok:
            out["bu"].update({"sha256": res.sha256, "formato": ea.sniff_bu(res.body)})
            if a.bu_spec:
                try:
                    decoded = ea.decode_bu_with_official_spec(res.body, Path(a.bu_spec))
                    dump = ARTIFACTS / "bu" / f"{Path(bu['nome']).stem}__decodificado.json"
                    dump.write_text(json.dumps(decoded, default=_jsonable, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
                    out["bu"]["decodificado_em"] = dump.relative_to(ARTIFACTS).as_posix()
                    out["bu"]["chaves_de_topo"] = sorted(decoded)
                except Exception as exc:  # diagnostico da POC: registrar, nao mascarar
                    out["bu"]["erro_decodificacao"] = f"{type(exc).__name__}: {exc}"
        return out

    def step_historico(self, ea11, municipio):
        """Prova da cadeia EA16 -> EA18 -> BU num pleito ja totalizado (NAO e dado de 2026)."""
        a = self.args
        try:
            config = ea.normalize_ea11(ea11, a.ambiente, a.historic_pleito)
        except LookupError as exc:
            self.report["secao_historico"] = {"erro": str(exc)}
            return
        doc = self.fetch_json("ea16_historico", ea.ea16_url(config, a.uf), "ea16")
        if doc is None:
            self.report["secao_historico"] = {"erro": "EA16 historico indisponivel"}
            return
        out = self.step_secao(config, ea.parse_ea16(doc, a.uf), municipio, label="historico")
        out["aviso"] = (f"PROVA HISTORICA: pleito {config['pleito']} ({config['pleito_data']}, "
                        f"ciclo {config['ciclo']}). Valida o mecanismo, nao dados de 2026.")

    def consolidacao(self, candidate):
        return {
            "executada": False,
            "motivo": "votos por secao ainda nao extraidos do BU; estrutura em memoria e "
                      "consultas A-D ficam condicionadas a decodificacao oficial",
            "fontes": {"oficial_consolidada": "EA20", "analitica_por_secao": "BU"},
        }

    def finish(self) -> int:
        r = self.report
        # 2026 e prova historica tem chaves separadas (T09 x T09H): o resultado
        # historico nunca substitui o estado real do pleito de 2026.
        for label, suffix, nota in (("2026", "", ""), ("historico", "H", " [prova historica]")):
            sec = r.get(f"secao_{label}")
            if not sec:
                continue
            if sec.get("ea18"):
                self.test(9, "EA18 de uma secao real localizado" + nota, "OK", sec["ea18_url"], suffix)
            else:
                self.test(9, "EA18 de uma secao real localizado" + nota, "PENDENTE",
                          sec.get("diagnostico") or sec.get("erro", ""), suffix)
            bu = sec.get("bu") or {}
            if not bu.get("formato"):
                self.test(10, "Arquivo BU da secao identificado" + nota, "PENDENTE",
                          "sem EA18/BU disponivel", suffix)
                self.test(11, "Votos do candidato extraidos do BU" + nota, "PENDENTE",
                          "sem BU disponivel", suffix)
                continue
            fmt = bu["formato"]
            tipo = "JSON" if fmt["json"] else "ASN.1 BER" if fmt["asn1_ber_sequence"] else "desconhecido"
            self.test(10, "Arquivo BU da secao identificado" + nota, "OK",
                      f"{bu['nome']} ({fmt['bytes']} B, {tipo})", suffix)
            if bu.get("decodificado_em"):
                self.test(11, "BU decodificado com especificacao oficial" + nota, "PARCIAL",
                          "estrutura decodificada; extracao por candidato na proxima rodada", suffix)
            else:
                self.test(11, "Votos do candidato extraidos do BU" + nota, "PENDENTE",
                          bu.get("erro_decodificacao") or "exige bu.asn1 oficial do TSE (--bu-spec)", suffix)

        rate = self.http.rate_limit_report()
        r["http"] = rate
        ok = rate["pico_observado_em_1s"] <= self.args.max_rps and not rate["404_repetidos"]
        self.test(12, "Rate limit respeitado e sem loop de 404", "OK" if ok else "FALHOU",
                  f"{rate['requisicoes_de_rede']} requisicoes, pico {rate['pico_observado_em_1s']}/s, "
                  f"{rate['urls_404']} URLs 404 (nenhuma repetida)" if ok else json.dumps(rate))
        r["testes"] = self.tests
        r["requisicoes"] = [{k: e.get(k) for k in ("kind", "url", "status", "etag", "last_modified",
                                                    "elapsed_ms", "idg", "nota")}
                            for e in self.http.request_log]
        path = ARTIFACTS / "_reports" / f"poc-{self.http.run_id}.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(r, ensure_ascii=False, indent=1, default=_jsonable), encoding="utf-8")
        print(f"== relatorio: {path}")
        return 1 if any(t["status"] == "FALHOU" for t in self.tests.values()) else 0


def _jsonable(value):
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    if isinstance(value, tuple):
        return list(value)
    return str(value)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ambiente", default="oficial")
    p.add_argument("--pleito", default="3220")
    p.add_argument("--uf", default="ap")
    p.add_argument("--cargo", default="0006")
    p.add_argument("--municipio", default="Macapá", help="nome; o codigo e descoberto no EA12")
    p.add_argument("--zona", help="zona usada na prova de secao (padrao: a primeira do municipio)")
    p.add_argument("--sqcand")
    p.add_argument("--numero", help="fallback tecnico quando nao ha sqcand")
    p.add_argument("--max-rps", type=float, default=2.0)
    p.add_argument("--max-section-probes", type=int, default=3)
    p.add_argument("--historic-pleito", help="pleito ja totalizado para provar EA18/BU (ex.: 452)")
    p.add_argument("--bu-spec", help="caminho do bu.asn1 oficial do TSE")
    args = p.parse_args()
    args.uf = args.uf.lower()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return Poc(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
