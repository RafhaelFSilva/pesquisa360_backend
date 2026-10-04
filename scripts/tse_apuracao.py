"""CLI da Apuracao TSE: ingestao manual e consulta de validacao.

Usa o banco de DATABASE_URL. Cada execucao faz uma passada. A ingestao
automatica e o worker (`python -m pesquisa360.services.tse.worker`); a CLI e
o fallback manual e respeita a MESMA trava: com o worker ativo para a origem,
`ingest` recusa a execucao (codigo de saida 3) em vez de gravar em paralelo.

    python scripts/tse_apuracao.py ingest --uf ap --cargo 0006 \\
        --municipios todos --zonas-de 06050
    python scripts/tse_apuracao.py consulta --uf ap --cargo 0006 --sqcand <sq>

Ambiente de SIMULACAO do TSE (nunca se mistura com o oficial):

    python scripts/tse_apuracao.py ingest --simulado --uf ap --cargo 0006 ...
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pesquisa360.db.models_tse import TseCargo, TseEleicao  # noqa: E402
from pesquisa360.db.session import SessionLocal, engine  # noqa: E402
from pesquisa360.services.tse import reconciliation  # noqa: E402
from pesquisa360.services.tse.client import TseClient  # noqa: E402
from pesquisa360.services.tse.config import settings_for_origem  # noqa: E402
from pesquisa360.services.tse.ingestion import TODOS, IngestScope, TseIngestion  # noqa: E402
from pesquisa360.services.tse.locking import IngestionLock  # noqa: E402
from pesquisa360.services.tse.normalization import OFICIAL, SIMULADO  # noqa: E402
from pesquisa360.services.tse.repository import TseRepository, abrangencia_key  # noqa: E402

EXIT_LOCK_OCUPADO = 3


def _contexto(args):
    """(origem, settings do ambiente TSE, pleito). Sem `--pleito` ele e descoberto."""
    origem = SIMULADO if args.simulado else OFICIAL
    return origem, settings_for_origem(origem), args.pleito or None


def cmd_ingest(args) -> int:
    origem, settings, pleito = _contexto(args)
    municipios = (TODOS,) if args.municipios == ["todos"] else tuple(args.municipios)
    scope = IngestScope(
        pleito=pleito, uf=args.uf.lower(), cargos=tuple(args.cargo), municipios=municipios,
        zonas_de=tuple(args.zonas_de), secoes_ea18=args.secoes_ea18, origem=origem,
        force=args.force,
    )
    with IngestionLock(engine, origem) as lock:
        if not lock.try_acquire():
            print(f"Ingestao {origem} em andamento por outro processo (worker automatico ou "
                  "outra CLI). Nada foi executado. Pare o worker para ingerir manualmente.",
                  file=sys.stderr)
            return EXIT_LOCK_OCUPADO
        with SessionLocal() as session, TseClient(settings=settings) as client:
            report = TseIngestion(session, client, settings).run(scope)
            pico = client.peak_requests_per_second()
    print(json.dumps({"origem": origem, **asdict(report),
                      "pico_requisicoes_por_segundo": pico}, ensure_ascii=False, indent=1))
    return 0


def cmd_consulta(args) -> int:
    origem, _settings, pleito = _contexto(args)
    uf = args.uf.lower()
    with SessionLocal() as session:
        repo = TseRepository(session)
        cargo = session.query(TseCargo).filter_by(codigo=args.cargo).first()
        consulta = session.query(TseEleicao).filter_by(origem=origem)
        if pleito:
            consulta = consulta.filter_by(pleito=pleito)
        # Sem `--pleito`: a eleicao mais recente da origem que disputa o cargo.
        eleicao = next((e for e in consulta.order_by(TseEleicao.data_eleicao.desc(),
                                                     TseEleicao.id.desc())
                        if args.cargo in (e.metadata_json or {}).get("cargos", [])), None)
        if eleicao is None or cargo is None:
            print("Eleicao/cargo nao ingeridos para esta origem.", file=sys.stderr)
            return 1
        abr_uf = repo.get_abrangencia(eleicao.id, abrangencia_key(uf))
        if args.sqcand or args.numero:
            candidato = repo.find_candidato(eleicao.id, sqcand=args.sqcand, numero=args.numero,
                                            cargo=args.cargo, uf=uf)
        else:
            topo = repo.ranking(abr_uf.id, cargo.id, limit=1)
            candidato = repo.find_candidato(eleicao.id, sqcand=topo[0]["sqcand"]) if topo else None
        if candidato is None:
            print("Candidato nao encontrado.", file=sys.stderr)
            return 1

        votos = repo.candidate_votes(candidato.id)
        linha_uf = next(v for v in votos if v["tipo"] == "UF")
        saida = {
            "origem": origem, "eleicao": eleicao.codigo_eleicao, "cargo": cargo.nome,
            "candidato": {"nome": candidato.nome, "nome_urna": candidato.nome_urna,
                          "numero": candidato.numero, "sqcand": candidato.sqcand,
                          "partido": candidato.partido.sigla,
                          "federacao": (candidato.partido.federacao.sigla
                                        if candidato.partido.federacao else None),
                          "situacao": linha_uf["situacao"],
                          "destinacao_voto": linha_uf["destinacao_voto"]},
            "uf": _linha(linha_uf),
            "municipios": [{"nome": v["municipio_nome"], **_linha(v)}
                           for v in votos if v["tipo"] == "MUNICIPIO"],
            "zonas": [{"municipio": v["municipio_codigo"], "zona": v["zona"], **_linha(v)}
                      for v in votos if v["tipo"] == "ZONA"],
            "secoes": "PENDENTE: depende da decodificacao do BU com o bu.asn1 oficial",
            "historico_uf": [{"totalizacao_id": h["totalizacao_id"], "idg": h["idg"],
                              "gerado_em": str(h["gerado_em"]), "votos": h["votos"],
                              "secoes_totalizadas": h["secoes_totalizadas"]}
                             for h in repo.candidate_history(candidato.id, abr_uf.id)],
            "reconciliacao": [reconciliation.reconcile_abrangencia(repo, candidato.id, abr_uf)],
        }
        for municipio in args.reconciliar_municipio:
            abr = repo.get_abrangencia(eleicao.id, abrangencia_key(uf, municipio))
            saida["reconciliacao"].append(
                reconciliation.reconcile_abrangencia(repo, candidato.id, abr))
    print(json.dumps(saida, ensure_ascii=False, indent=1, default=str))
    return 0


def _linha(v: dict) -> dict:
    total, feitas = v["secoes_total"], v["secoes_totalizadas"]
    return {"votos": v["votos"], "percentual": v["percentual"], "idg": v["idg"],
            "gerado_em": str(v["gerado_em"]), "andamento": v["andamento"],
            "secoes": f"{feitas}/{total}",
            "pct_secoes": round(100 * feitas / total, 2) if total else None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="comando", required=True)
    for nome in ("ingest", "consulta"):
        p = sub.add_parser(nome)
        p.add_argument("--simulado", action="store_true",
                       help="usa o ambiente de simulacao do TSE (origem SIMULADO)")
        p.add_argument("--pleito")
        p.add_argument("--uf", required=True)
    p = sub.choices["ingest"]
    p.add_argument("--cargo", action="append", required=True, help="repetivel; ex.: 0006")
    p.add_argument("--municipios", nargs="*", default=[],
                   help="codigos TSE, ou 'todos' para todos os municipios da UF")
    p.add_argument("--zonas-de", nargs="*", default=[],
                   help="municipios cujas zonas tambem sao ingeridas")
    p.add_argument("--secoes-ea18", type=int, default=0)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_ingest)
    p = sub.choices["consulta"]
    p.add_argument("--cargo", required=True)
    p.add_argument("--sqcand")
    p.add_argument("--numero")
    p.add_argument("--reconciliar-municipio", nargs="*", default=[])
    p.set_defaults(func=cmd_consulta)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
