"""Restricao de universo de coletas por respostas.

Extraido de `lideranca_analytics` para ser compartilhado, porque a mesma
semantica vale para qualquer analise que precise recortar entrevistas:

    OR  dentro dos valores da mesma pergunta
    AND entre perguntas distintas

Isto NAO gera tabela cruzada: e restricao de universo. O cruzamento em arvore
continua em `multidimensional_cross`.

Os valores usados na comparacao passam por `resolve_reportable_response_value`,
o mesmo normalizador do Relatorio Simples -- espontaneas chegam ja como
categoria, nunca como texto cru, e nao existe uma segunda normalizacao.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Sequence

from sqlalchemy.orm import Session

from pesquisa360 import crud
from pesquisa360.db import models


def carregar_valores_reportaveis(
    db: Session,
    *,
    pesquisa_id: int,
    coleta_ids: Sequence[int],
    perguntas_por_id: dict,
    current_user: models.Usuario,
    spontaneous_mapping: dict | None = None,
) -> dict:
    """Mapa coleta -> pergunta -> conjunto de valores reportaveis.

    Uma unica consulta para todas as coletas e perguntas envolvidas; nada de
    consultar resposta por coleta.
    """
    if not coleta_ids or not perguntas_por_id:
        return {}

    if spontaneous_mapping is None:
        spontaneous_mapping = (
            crud.get_active_spontaneous_mapping_for_report(
                db=db, pesquisa_id=pesquisa_id, current_user=current_user
            )
            if any(
                pergunta.eh_resposta_espontanea for pergunta in perguntas_por_id.values()
            )
            else {}
        )

    linhas = (
        db.query(
            models.Resposta.coleta_id,
            models.Resposta.pergunta_id,
            models.Resposta.valor_resposta,
        )
        .filter(
            models.Resposta.coleta_id.in_(list(coleta_ids)),
            models.Resposta.pergunta_id.in_(list(perguntas_por_id)),
        )
        .all()
    )

    valores: dict = defaultdict(lambda: defaultdict(set))
    for coleta_id, pergunta_id, valor in linhas:
        pergunta = perguntas_por_id[pergunta_id]
        reportavel = crud.resolve_reportable_response_value(
            pergunta=pergunta,
            valor_resposta=valor,
            spontaneous_mapping=spontaneous_mapping,
        )
        if reportavel:
            valores[coleta_id][pergunta_id].add(reportavel)
    return valores


def aplicar_filtros_respostas(
    coleta_ids: Iterable[int],
    valores: dict,
    filtros: Sequence[tuple[int, set]],
) -> list[int]:
    """OR dentro dos valores da mesma pergunta, AND entre perguntas distintas."""
    if not filtros:
        return list(coleta_ids)
    selecionadas = []
    for coleta_id in coleta_ids:
        respostas = valores.get(coleta_id, {})
        if all(
            respostas.get(pergunta_id, set()) & permitidos
            for pergunta_id, permitidos in filtros
        ):
            selecionadas.append(coleta_id)
    return selecionadas
