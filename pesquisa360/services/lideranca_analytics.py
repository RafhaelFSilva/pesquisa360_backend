"""Analise eleitoral das liderancas: projecao de votos e Gap/Plus.

Duas dimensoes complementares, deliberadamente independentes:

  SETOR   define o universo AMOSTRAL (quais coletas da onda entram na taxa);
  BAIRROS definem o universo ELEITORAL (quantos aptos a lideranca representa).

Nada aqui e persistido: Gap/Plus depende da onda, da pergunta alvo, da cota e
dos parametros da Base Eleitoral, entao gravar produziria dado stale.

Decisao metodologica central: filtros adicionais (sexo, idade...) recortam uma
SUBAMOSTRA cuja populacao no territorio e desconhecida. Por isso o recorte
filtrado devolve apenas taxas — nunca votos absolutos nem Gap/Plus.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Optional, Sequence

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from pesquisa360 import crud
from pesquisa360.db import models
from pesquisa360.services.filtros_universo import aplicar_filtros_respostas
from pesquisa360.services import base_eleitoral as base_service

# --- Motivos de indisponibilidade --------------------------------------------
# Nunca devolver zero para ausencia: zero e um resultado, ausencia nao.
SEM_COTA = "SEM_COTA"
SEM_TERRITORIO_ELEITORAL = "SEM_TERRITORIO_ELEITORAL"
BASE_ELEITORAL_NAO_VALIDADA = "BASE_ELEITORAL_NAO_VALIDADA"
PARAMETROS_ELEITORAIS_AUSENTES = "PARAMETROS_ELEITORAIS_AUSENTES"
SEM_RESPOSTAS_VALIDAS = "SEM_RESPOSTAS_VALIDAS"
PERGUNTA_ALVO_INVALIDA = "PERGUNTA_ALVO_INVALIDA"

ESCOPO_SETOR = "SETOR"
ESCOPO_PESQUISA = "PESQUISA"

STATUS_GAP = "GAP"
STATUS_PLUS = "PLUS"
STATUS_META_ATINGIDA = "META_ATINGIDA"


def _nao_encontrado(entidade: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"{entidade} nao encontrado."
    )


@dataclass(frozen=True)
class ResultadoAmostral:
    total_entrevistas: int
    base_valida: int
    respostas_alvo: int
    taxa_alvo: Optional[Decimal]


def _arredondar_votos(valor: Optional[Decimal]) -> Optional[int]:
    """Decimal em todo o calculo; arredondamento so na apresentacao."""
    if valor is None:
        return None
    return int(valor.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# --- Universo amostral -------------------------------------------------------


def _coletas_da_pesquisa(db: Session, pesquisa_id: int, company_id: int) -> list[int]:
    return [
        row[0]
        for row in db.query(models.Coleta.id)
        .filter(
            models.Coleta.pesquisa_id == pesquisa_id,
            models.Coleta.company_id == company_id,
        )
        .order_by(models.Coleta.id)
        .all()
    ]


def _valores_reportaveis(
    db: Session,
    *,
    pesquisa_id: int,
    coleta_ids: Sequence[int],
    perguntas_por_id: dict,
    spontaneous_mapping: dict,
) -> dict:
    """Valores ja normalizados pelo mesmo motor dos relatorios.

    Reutiliza `resolve_reportable_response_value`, entao espontaneas seguem
    exatamente a semantica do Relatorio Simples; nao existe segunda normalizacao.
    """
    if not coleta_ids or not perguntas_por_id:
        return {}
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


# Semantica compartilhada com o Mapa de Respostas Georreferenciadas:
# OR dentro da pergunta, AND entre perguntas. Uma implementacao so.
_aplicar_filtros = aplicar_filtros_respostas


def _medir(
    coleta_ids: Sequence[int], valores: dict, pergunta_alvo_id: int, alvo_valores: set
) -> ResultadoAmostral:
    """Base valida = entrevistas com resposta reportavel para a pergunta alvo."""
    base_valida = 0
    respostas_alvo = 0
    for coleta_id in coleta_ids:
        respostas = valores.get(coleta_id, {}).get(pergunta_alvo_id, set())
        if not respostas:
            continue
        base_valida += 1
        if respostas & alvo_valores:
            respostas_alvo += 1
    taxa = (
        (Decimal(respostas_alvo) / Decimal(base_valida)) if base_valida else None
    )
    return ResultadoAmostral(
        total_entrevistas=len(coleta_ids),
        base_valida=base_valida,
        respostas_alvo=respostas_alvo,
        taxa_alvo=taxa,
    )


# --- Universo eleitoral ------------------------------------------------------


def _aptos_da_lideranca(territorios: Sequence[models.TerritorioEleitoral]) -> Optional[int]:
    """Soma dos bairros associados; cada territorio entra uma unica vez."""
    if not territorios:
        return None
    vistos = {}
    for territorio in territorios:
        vistos[territorio.id] = territorio.eleitorado_apto or 0
    return sum(vistos.values())


def _votos_validos_projetados(
    aptos: Optional[int], base: models.BaseEleitoral
) -> tuple[Optional[Decimal], Optional[str]]:
    """aptos x comparecimento x validos.

    Sem os parametros declarados na Base nao ha projecao: assumir 100% de
    comparecimento transformaria aptos em votos validos, que e exatamente o
    erro que a modelagem se propos a impedir.
    """
    if aptos is None:
        return None, SEM_TERRITORIO_ELEITORAL
    if base.comparecimento_estimado is None or base.percentual_votos_validos is None:
        return None, PARAMETROS_ELEITORAIS_AUSENTES
    projetados = (
        Decimal(aptos)
        * Decimal(base.comparecimento_estimado)
        * Decimal(base.percentual_votos_validos)
    )
    return projetados, None


# --- Analise em lote ---------------------------------------------------------


def analisar_liderancas(
    db: Session,
    *,
    projeto_id: int,
    pesquisa_id: int,
    pergunta_alvo_id: int,
    alvo_valores: Sequence[str],
    filtros_respostas: Sequence[dict] | None,
    lideranca_ids: Sequence[int] | None,
    current_user: models.Usuario,
) -> dict:
    projeto = (
        db.query(models.Projeto)
        .filter(
            models.Projeto.id == projeto_id,
            models.Projeto.company_id == current_user.company_id,
        )
        .first()
    )
    if projeto is None:
        raise _nao_encontrado("Projeto")

    pesquisa = (
        db.query(models.Pesquisa)
        .filter(
            models.Pesquisa.id == pesquisa_id,
            models.Pesquisa.projeto_id == projeto.id,
        )
        .first()
    )
    if pesquisa is None:
        raise _nao_encontrado("Pesquisa")

    pergunta_alvo = (
        db.query(models.Pergunta)
        .filter(
            models.Pergunta.id == pergunta_alvo_id,
            models.Pergunta.pesquisa_id == pesquisa.id,
            models.Pergunta.ativo.is_(True),
        )
        .first()
    )
    if pergunta_alvo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pergunta alvo nao encontrada."
        )

    filtros_normalizados = []
    perguntas_por_id = {pergunta_alvo.id: pergunta_alvo}
    for item in filtros_respostas or []:
        filtro_id = item["pergunta_id"]
        pergunta = (
            db.query(models.Pergunta)
            .filter(
                models.Pergunta.id == filtro_id,
                models.Pergunta.pesquisa_id == pesquisa.id,
                models.Pergunta.ativo.is_(True),
            )
            .first()
        )
        if pergunta is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pergunta de filtro nao encontrada nesta pesquisa.",
            )
        perguntas_por_id[pergunta.id] = pergunta
        filtros_normalizados.append((pergunta.id, set(item["valores"])))

    # Base principal do projeto. Conferencia (nao calculo) para poder devolver
    # o motivo BASE_ELEITORAL_NAO_VALIDADA em vez de estourar.
    base = base_service.obter_base_principal_projeto_opcional(db, projeto.id, current_user)
    base_indisponivel = None
    if base is None:
        base_indisponivel = SEM_TERRITORIO_ELEITORAL
    elif base.status != base_service.STATUS_VALIDADA:
        base_indisponivel = BASE_ELEITORAL_NAO_VALIDADA

    query = db.query(models.LiderancaPolitica).filter(
        models.LiderancaPolitica.projeto_id == projeto.id,
        models.LiderancaPolitica.ativo.is_(True),
    )
    if lideranca_ids:
        query = query.filter(models.LiderancaPolitica.id.in_(list(lideranca_ids)))
    liderancas = query.order_by(models.LiderancaPolitica.nome, models.LiderancaPolitica.id).all()

    configs = {
        config.lideranca_id: config
        for config in db.query(models.LiderancaPesquisaConfig)
        .filter(
            models.LiderancaPesquisaConfig.pesquisa_id == pesquisa.id,
            models.LiderancaPesquisaConfig.lideranca_id.in_([item.id for item in liderancas] or [0]),
        )
        .all()
    }

    territorios_por_lideranca: dict[int, list] = defaultdict(list)
    if liderancas:
        vinculos = (
            db.query(models.LiderancaTerritorioEleitoral, models.TerritorioEleitoral)
            .join(
                models.TerritorioEleitoral,
                models.TerritorioEleitoral.id
                == models.LiderancaTerritorioEleitoral.territorio_eleitoral_id,
            )
            .filter(
                models.LiderancaTerritorioEleitoral.lideranca_id.in_(
                    [item.id for item in liderancas]
                )
            )
            .all()
        )
        for vinculo, territorio in vinculos:
            territorios_por_lideranca[vinculo.lideranca_id].append(territorio)

    # --- universo amostral compartilhado -------------------------------------
    coletas_pesquisa = _coletas_da_pesquisa(db, pesquisa.id, current_user.company_id)
    spontaneous_mapping = (
        crud.get_active_spontaneous_mapping_for_report(
            db=db, pesquisa_id=pesquisa.id, current_user=current_user
        )
        if any(pergunta.eh_resposta_espontanea for pergunta in perguntas_por_id.values())
        else {}
    )
    valores = _valores_reportaveis(
        db,
        pesquisa_id=pesquisa.id,
        coleta_ids=coletas_pesquisa,
        perguntas_por_id=perguntas_por_id,
        spontaneous_mapping=spontaneous_mapping,
    )

    setores_necessarios = any(config.setor_id for config in configs.values())
    coletas_por_setor: dict[int, list[int]] = defaultdict(list)
    if setores_necessarios:
        # Fonte de verdade unica: mesma classificacao dos Mapas e Cruzamentos,
        # preservando "sem setor" e sobreposicao.
        classificacao = crud.classificar_coletas_por_setor(
            db, pesquisa.id, current_user
        )
        for coleta_id, setor_id in classificacao["classificados"].items():
            coletas_por_setor[setor_id].append(coleta_id)

    alvo_set = set(alvo_valores)
    resultado_liderancas = []
    for lideranca in liderancas:
        config = configs.get(lideranca.id)
        territorios = territorios_por_lideranca.get(lideranca.id, [])

        if config is not None and config.setor_id:
            escopo = ESCOPO_SETOR
            universo = coletas_por_setor.get(config.setor_id, [])
        else:
            escopo = ESCOPO_PESQUISA
            universo = coletas_pesquisa

        principal = _medir(universo, valores, pergunta_alvo.id, alvo_set)
        recorte = _medir(
            _aplicar_filtros(universo, valores, filtros_normalizados),
            valores,
            pergunta_alvo.id,
            alvo_set,
        ) if filtros_normalizados else None

        aptos = _aptos_da_lideranca(territorios)
        votos_validos = None
        indisponibilidade = None
        if base_indisponivel and territorios:
            indisponibilidade = base_indisponivel
        elif base is not None:
            votos_validos, indisponibilidade = _votos_validos_projetados(aptos, base)
        else:
            indisponibilidade = SEM_TERRITORIO_ELEITORAL

        cota = config.cota_votos_validos if config is not None else None
        votos_projetados = None
        gap_plus = None
        status_gap = None
        atingimento = None

        if indisponibilidade is None and principal.taxa_alvo is None:
            indisponibilidade = SEM_RESPOSTAS_VALIDAS
        if indisponibilidade is None and votos_validos is not None:
            votos_projetados = votos_validos * principal.taxa_alvo
            if cota is None:
                indisponibilidade = SEM_COTA
            else:
                gap_plus = votos_projetados - Decimal(cota)
                if gap_plus > 0:
                    status_gap = STATUS_PLUS
                elif gap_plus < 0:
                    status_gap = STATUS_GAP
                else:
                    status_gap = STATUS_META_ATINGIDA
                if cota > 0:
                    atingimento = (votos_projetados / Decimal(cota)) * Decimal(100)

        resultado_liderancas.append(
            {
                "id": lideranca.id,
                "nome": lideranca.nome,
                "posicionamento": lideranca.posicionamento,
                "setor": (
                    {"id": config.setor.id, "nome": config.setor.nome}
                    if config is not None and config.setor is not None
                    else None
                ),
                "territorios": [
                    {
                        "id": territorio.id,
                        "nome": territorio.nome,
                        "eleitorado_apto": territorio.eleitorado_apto,
                    }
                    for territorio in sorted(territorios, key=lambda item: item.nome)
                ],
                "cota_votos_validos": cota,
                "universo_eleitoral": {
                    "eleitorado_apto": aptos,
                    "votos_validos_projetados": _arredondar_votos(votos_validos),
                },
                "resultado_principal": {
                    "escopo_amostral": escopo,
                    "total_entrevistas": principal.total_entrevistas,
                    "base_valida": principal.base_valida,
                    "respostas_alvo": principal.respostas_alvo,
                    "taxa_alvo": float(principal.taxa_alvo) if principal.taxa_alvo is not None else None,
                    "votos_projetados_alvo": _arredondar_votos(votos_projetados),
                    "gap_plus": _arredondar_votos(gap_plus),
                    "status": status_gap,
                    "atingimento_percentual": (
                        float(atingimento.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                        if atingimento is not None
                        else None
                    ),
                },
                # Recorte diagnostico: taxas apenas. Sem calibracao populacional
                # do segmento, votos absolutos seriam projecao falsa.
                "recorte_filtrado": (
                    {
                        "total_entrevistas": recorte.total_entrevistas,
                        "base_valida": recorte.base_valida,
                        "respostas_alvo": recorte.respostas_alvo,
                        "taxa_alvo": float(recorte.taxa_alvo) if recorte.taxa_alvo is not None else None,
                    }
                    if recorte is not None
                    else None
                ),
                "indisponibilidade": indisponibilidade,
            }
        )

    return {
        "projeto_id": projeto.id,
        "pesquisa_id": pesquisa.id,
        "alvo": {"pergunta_id": pergunta_alvo.id, "valores": list(alvo_valores)},
        "filtros_respostas": [
            {"pergunta_id": pergunta_id, "valores": sorted(valores_filtro)}
            for pergunta_id, valores_filtro in filtros_normalizados
        ],
        "liderancas": resultado_liderancas,
    }
