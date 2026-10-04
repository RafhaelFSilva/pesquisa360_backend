"""Reconciliacao entre o voto oficial (EA20) e a soma de suas partes.

Niveis: ZONAS -> MUNICIPIO, MUNICIPIOS -> UF, SECOES -> ZONA. O EA20 da
abrangencia maior e sempre a referencia oficial; a soma propria nunca o
substitui. Cada arquivo do TSE e gerado em instante proprio: diferenca entre
arquivos de janelas de totalizacao distintas e defasagem, nao inconsistencia.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

CONSISTENT = "CONSISTENT"
TEMPORAL_LAG = "TEMPORAL_LAG"
INCONSISTENT = "INCONSISTENT"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class VotePoint:
    """Voto de um candidato numa abrangencia, com a janela do arquivo de origem."""

    ref: str
    votes: int | None
    idg: str | None = None
    generated_at: datetime | None = None
    last_totalization: datetime | None = None
    sections_counted: int | None = None


def reconcile_candidate_votes(candidate_id, scope: str, official: VotePoint,
                              parts: list[VotePoint],
                              expected_parts: int | None = None) -> dict:
    """Compara `official` com a soma de `parts`.

    `expected_parts` e quantas partes compoem a abrangencia (ex.: zonas do
    municipio); com partes faltando o resultado e INSUFFICIENT_DATA.

    Mesma janela = soma das secoes totalizadas das partes igual a do oficial
    (sem essa contagem, mesma ultima totalizacao em todos os arquivos). So ha
    INCONSISTENT com diferenca de votos dentro da mesma janela.
    """
    missing = [p.ref for p in parts if p.votes is None]
    incomplete = (
        not parts or bool(missing) or official.votes is None
        or (expected_parts is not None and len(parts) != expected_parts)
    )
    generated = [p.generated_at for p in parts if p.generated_at]
    result = {
        "scope": scope,
        "candidate_id": candidate_id,
        "official": {
            "ref": official.ref, "votes": official.votes, "idg": official.idg,
            "generated_at": _iso(official.generated_at),
            "last_totalization": _iso(official.last_totalization),
            "sections_counted": official.sections_counted,
        },
        "derived": {
            "votes": None if incomplete else sum(p.votes for p in parts),
            "parts": len(parts), "expected_parts": expected_parts,
            "generated_at_min": _iso(min(generated)) if generated else None,
            "generated_at_max": _iso(max(generated)) if generated else None,
            "sections_counted": _sum_or_none([p.sections_counted for p in parts]),
        },
        "difference": None,
        "same_window": None,
        "status": INSUFFICIENT_DATA,
        "missing_parts": missing,
    }
    if incomplete:
        return result

    result["difference"] = official.votes - result["derived"]["votes"]
    result["same_window"] = _same_window(official, parts)
    if result["difference"] == 0:
        result["status"] = CONSISTENT
    elif result["same_window"]:
        result["status"] = INCONSISTENT
    else:
        result["status"] = TEMPORAL_LAG
    return result


def _same_window(official: VotePoint, parts: list[VotePoint]) -> bool:
    # A contagem de secoes e o indicador mais forte: os municipios de uma UF
    # tem horarios de totalizacao proprios mesmo quando tudo ja fechou.
    derived_sections = _sum_or_none([p.sections_counted for p in parts])
    if official.sections_counted is not None and derived_sections is not None:
        return official.sections_counted == derived_sections
    return {p.last_totalization for p in parts} <= {official.last_totalization}


def _sum_or_none(values):
    return None if any(v is None for v in values) or not values else sum(values)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def reconcile_abrangencia(repo, candidato_id: int, abrangencia) -> dict:
    """Reconcilia, a partir do banco, uma abrangencia contra suas filhas diretas.

    Municipio x zonas, UF x municipios. As filhas esperadas sao TODAS as
    cadastradas (do EA12): se alguma nao foi ingerida, o resultado e
    INSUFFICIENT_DATA em vez de uma soma parcial.
    """
    def ponto(linha):
        return VotePoint(
            ref=linha["chave"], votes=linha["votos"], idg=linha["idg"],
            generated_at=linha["gerado_em"], last_totalization=linha["ultima_totalizacao"],
            sections_counted=linha["secoes_totalizadas"])

    oficial = [l for l in repo.candidate_votes(candidato_id) if l["abrangencia_id"] == abrangencia.id]
    filhas = repo.children(abrangencia.id)
    partes = repo.candidate_votes(candidato_id, parent_id=abrangencia.id)
    escopo = {"UF": "uf<-municipios", "MUNICIPIO": "municipio<-zonas"}.get(
        abrangencia.tipo, abrangencia.tipo)
    return reconcile_candidate_votes(
        candidato_id, f"{escopo} {abrangencia.chave}",
        ponto(oficial[0]) if oficial else VotePoint(ref=abrangencia.chave, votes=None),
        [ponto(l) for l in partes], expected_parts=len(filhas))
