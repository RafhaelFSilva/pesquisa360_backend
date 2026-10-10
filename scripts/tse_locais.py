"""Metadados oficiais dos locais de votacao (nome e endereco) — importacao manual.

Fonte: CSV "Eleitorado por local de votacao" do Portal de Dados Abertos do TSE
(um arquivo por UF, dentro do ZIP `eleitorado_local_votacao_<ano>.zip`).

    python scripts/tse_locais.py conciliar --arquivo docs/tse2026/eleitorado_local_votacao_2026_AP.csv --uf ap --pleito 3220 --turno 1
    python scripts/tse_locais.py importar  --arquivo ... --uf ap --pleito 3220 --turno 1 --fonte-url <url>
    python scripts/tse_locais.py cobertura --uf ap --pleito 3220

O arquivo traz uma linha por secao E por turno. `--turno` e obrigatorio e deve
ser o da eleicao (pleito) que esta sendo enriquecida: as linhas dos outros
turnos sao ignoradas antes de qualquer contagem.

Importa SO metadado: nao toca votos, BU nem EA20. O vinculo secao -> local
continua sendo o codigo do Boletim de Urna; a conciliacao e pela chave oficial
(municipio + zona + codigo), sem comparar nomes. Se algum local dos BUs ficar
sem correspondencia ou ambiguo, a importacao e recusada e nada e gravado.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pesquisa360.db.session import SessionLocal  # noqa: E402
from pesquisa360.services.tse import locais_import  # noqa: E402
from pesquisa360.services.tse.normalization import OFICIAL, SIMULADO  # noqa: E402

EXIT_DIVERGENTE = 4


def _turno(valor: str) -> int:
    if not valor.isdigit() or int(valor) <= 0:
        raise argparse.ArgumentTypeError("turno deve ser um inteiro positivo")
    return int(valor)


def _origem(args) -> str:
    return SIMULADO if args.simulado else OFICIAL


def cmd_conciliar(args) -> int:
    conteudo = Path(args.arquivo).read_bytes()
    fonte = locais_import.ler_fonte(conteudo, args.uf, args.turno)
    with SessionLocal() as session:
        resultado = locais_import.conciliar(session, fonte, _origem(args), args.pleito)
    print(json.dumps({
        "arquivo": str(args.arquivo), "source_hash": fonte.sha256, "turno": fonte.turno,
        "linhas": fonte.linhas, "linhas_arquivo": fonte.linhas_arquivo,
        "turnos_no_arquivo": fonte.turnos,
        "data_eleicao": fonte.data_eleicao, "gerada_em": str(fonte.gerada_em),
        "locais_na_fonte": len(fonte.locais), "ambiguos_na_fonte": len(fonte.ambiguos),
        "invalidos": fonte.invalidas, **resultado}, ensure_ascii=False, indent=1))
    return EXIT_DIVERGENTE if resultado["bu_only"] or resultado["ambiguous"] else 0


def cmd_importar(args) -> int:
    conteudo = Path(args.arquivo).read_bytes()
    with SessionLocal() as session:
        try:
            relatorio = locais_import.importar(
                session, conteudo, origem=_origem(args), pleito=args.pleito, uf=args.uf,
                turno=args.turno, fonte_url=args.fonte_url, dry_run=args.dry_run)
        except locais_import.ConciliacaoDivergente as exc:
            session.rollback()
            print(str(exc), file=sys.stderr)
            return EXIT_DIVERGENTE
        if args.dry_run:
            session.rollback()
        else:
            session.commit()
        relatorio["cobertura"] = locais_import.cobertura(session, _origem(args), args.pleito, args.uf)
    print(json.dumps(relatorio, ensure_ascii=False, indent=1))
    return 0


def cmd_cobertura(args) -> int:
    with SessionLocal() as session:
        print(json.dumps(locais_import.cobertura(session, _origem(args), args.pleito, args.uf),
                         ensure_ascii=False, indent=1))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="comando", required=True)
    for nome, func in (("conciliar", cmd_conciliar), ("importar", cmd_importar),
                       ("cobertura", cmd_cobertura)):
        p = sub.add_parser(nome)
        p.add_argument("--uf", required=True)
        p.add_argument("--pleito", required=True)
        p.add_argument("--simulado", action="store_true")
        if nome != "cobertura":
            p.add_argument("--arquivo", required=True, help="CSV oficial da UF (Latin 1, ';')")
            p.add_argument("--turno", required=True, type=_turno,
                           help="turno da eleicao (NR_TURNO); so as linhas dele sao lidas")
        if nome == "importar":
            p.add_argument("--fonte-url", default=None, help="URL de onde o arquivo foi baixado")
            p.add_argument("--dry-run", action="store_true", help="concilia e relata, sem gravar")
        p.set_defaults(func=func)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
