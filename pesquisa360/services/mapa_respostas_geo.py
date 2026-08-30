"""Mapa de Respostas Georreferenciadas (Fase 2A) + cruzamento de duas perguntas.

Espacializa CADA coleta individual como um ponto, categorizada pela resposta
dada a uma pergunta principal.

Com `pergunta_secundaria_id`, a MESMA coleta passa a carregar duas categorias
-- a da pergunta A e a da pergunta B. O par nasce do `coleta_id`, nunca do
encontro de dois totais agregados independentes. Sem esse parametro o
comportamento e identico ao anterior.

Este modulo NAO e um segundo motor analitico. Ele apenas compoe pecas que ja
existem:

  crud._coleta_ponto_referencia        ponto canonico da coleta
  crud.classificar_coletas_por_setor   pertencimento territorial (ST_Covers)
  crud.resolve_reportable_response_value  normalizacao de resposta reportavel
  filtros_universo.aplicar_filtros_respostas  AND entre perguntas / OR dentro

Cor NAO pertence a este contrato: o cliente decide `valor -> cor`.
"""

from __future__ import annotations

from collections import Counter
from typing import Optional, Sequence

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from pesquisa360 import crud
from pesquisa360.db import models
from pesquisa360.services import acessos
from pesquisa360.question_types import (
    is_categorical_question_type,
    is_multiple_response_question_type,
    normalize_question_type,
)
from pesquisa360.services.filtros_universo import (
    aplicar_filtros_respostas,
    carregar_valores_reportaveis,
)


def _nao_encontrada(detalhe: str) -> HTTPException:
    """Cross-tenant e inexistente respondem igual: 404, nunca 403."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detalhe)


def _coordenada_valida(lat, lng) -> bool:
    """Mesma faixa exigida pelos demais mapas; (0,0) nao e inventado em lugar algum."""
    if lat is None or lng is None:
        return False
    try:
        latitude = float(lat)
        longitude = float(lng)
    except (TypeError, ValueError):
        return False
    if latitude != latitude or longitude != longitude:  # NaN
        return False
    if latitude in (float("inf"), float("-inf")) or longitude in (float("inf"), float("-inf")):
        return False
    return -90 <= latitude <= 90 and -180 <= longitude <= 180


def _perguntas_da_pesquisa(db: Session, pesquisa_id: int) -> dict:
    return {
        pergunta.id: pergunta
        for pergunta in db.query(models.Pergunta)
        .filter(
            models.Pergunta.pesquisa_id == pesquisa_id,
            models.Pergunta.ativo.is_(True),
        )
        .all()
    }


def _exigir_pergunta_da_pesquisa(perguntas: dict, pergunta_id: int, rotulo: str):
    pergunta = perguntas.get(pergunta_id)
    if pergunta is None:
        # Pergunta de outra pesquisa e indistinguivel de inexistente.
        raise _nao_encontrada(f"{rotulo} nao encontrada nesta pesquisa.")
    return pergunta


def _exigir_categoria_suportada(pergunta) -> None:
    """MVP: apenas resposta unica.

    Multipla escolha verdadeira criaria ambiguidade cartografica -- uma coleta,
    um ponto, duas categorias. Recusamos explicitamente em vez de escolher uma
    das respostas em silencio.
    """
    if pergunta.eh_resposta_espontanea:
        return
    if is_multiple_response_question_type(pergunta.tipo_pergunta):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Pergunta de multipla escolha ainda nao e suportada neste mapa: "
                "uma coleta produziria um unico ponto com mais de uma categoria."
            ),
        )
    if not is_categorical_question_type(pergunta.tipo_pergunta):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Apenas perguntas categoricas de resposta unica sao suportadas "
                f"neste mapa; a pergunta informada e do tipo "
                f"{normalize_question_type(pergunta.tipo_pergunta)}."
            ),
        )


def _resolver_categoria_unica(respostas: set, permitidos: Optional[set]) -> Optional[str]:
    """Valor visual DETERMINISTICO da coleta para uma pergunta.

    Uma coleta so vira ponto quando produz exatamente um valor reportavel. Zero
    (nao respondeu) e mais de um (multipla escolha) sao ambiguidades
    cartograficas: a coleta sai do mapa em vez de o servidor escolher uma das
    respostas em silencio. Mesma regra ja vigente para a pergunta principal.
    """
    escolhidas = respostas & permitidos if permitidos is not None else set(respostas)
    if len(escolhidas) == 1:
        return next(iter(escolhidas))
    return None


# Rotulo do balde de nao selecionadas. NAO e um identificador: a identidade da
# categoria e o par (valor, agrupado). Uma pergunta pode ter uma opcao real
# chamada "Outros" e as duas convivem na mesma resposta sem ambiguidade.
ROTULO_OUTROS = "Outros"


def _rotular(
    respostas: set,
    permitidos: Optional[set],
    preservados: set,
    agrupar: bool,
) -> Optional[tuple[str, bool]]:
    """Categoria exibida da coleta: `(valor, agrupado)` ou None para sair do mapa.

    Sem agrupamento o caminho e EXATAMENTE o legado -- a intersecao com os
    permitidos acontece antes da exigencia de valor unico, o que importa para
    espontaneas, onde uma coleta pode ter mais de um valor reportavel.

    Com agrupamento a decisao passa a ser sobre o universo inteiro da pergunta:
    quem nao esta destacado nao sai do mapa, vai para o balde.
    """
    if not agrupar:
        valor = _resolver_categoria_unica(respostas, permitidos)
        return (valor, False) if valor is not None else None

    # Destacado = selecionado OU preservado. Preservada e uma categoria que
    # continua com identidade propria mesmo desmarcada (Branco/Nulo, NS/NR).
    destacados = set(permitidos) | preservados if permitidos is not None else None
    escolhida = _resolver_categoria_unica(respostas, destacados)
    if escolhida is not None:
        return (escolhida, False)

    # Nao destacada: precisa de valor unico para ir ao balde. Ambiguidade
    # continua saindo do mapa -- agrupar nao afrouxa a regra cartografica.
    valor = _resolver_categoria_unica(respostas, None)
    if valor is None:
        return None
    return (ROTULO_OUTROS, True)


def gerar_mapa_respostas_geo(
    db: Session,
    *,
    pesquisa_id: int,
    pergunta_id: int,
    valores: Sequence[str],
    filtros_respostas: Sequence[dict],
    setor_ids: Optional[Sequence[int]],
    agente_ids: Optional[Sequence[int]],
    current_user: models.Usuario,
    pergunta_secundaria_id: Optional[int] = None,
    valores_secundarios: Optional[Sequence[str]] = None,
    agrupar_nao_selecionadas: bool = False,
    agrupar_nao_selecionadas_secundaria: bool = False,
    valores_preservados: Optional[Sequence[str]] = None,
    valores_preservados_secundarios: Optional[Sequence[str]] = None,
) -> dict:
    pesquisa = crud._validar_pesquisa_relatorio(db, pesquisa_id, current_user)

    perguntas = _perguntas_da_pesquisa(db, pesquisa_id)
    pergunta_alvo = _exigir_pergunta_da_pesquisa(perguntas, pergunta_id, "Pergunta")
    _exigir_categoria_suportada(pergunta_alvo)

    # --- Segunda pergunta do cruzamento --------------------------------------
    # Mesmas exigencias da principal: da MESMA pesquisa (e portanto do mesmo
    # tenant) e com valor unico por coleta. Pergunta de outra pesquisa e
    # indistinguivel de inexistente -> 404.
    if pergunta_secundaria_id is not None:
        if pergunta_secundaria_id == pergunta_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A pergunta de cruzamento nao pode ser a pergunta principal.",
            )
        pergunta_secundaria = _exigir_pergunta_da_pesquisa(
            perguntas, pergunta_secundaria_id, "Pergunta de cruzamento"
        )
        _exigir_categoria_suportada(pergunta_secundaria)

    filtros_normalizados: list[tuple[int, set]] = []
    for filtro in filtros_respostas or []:
        filtro_pergunta_id = filtro["pergunta_id"]
        if filtro_pergunta_id == pergunta_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A pergunta principal nao pode ser usada como filtro adicional.",
            )
        if filtro_pergunta_id == pergunta_secundaria_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A pergunta de cruzamento nao pode ser usada como filtro adicional.",
            )
        _exigir_pergunta_da_pesquisa(perguntas, filtro_pergunta_id, "Pergunta de filtro")
        filtros_normalizados.append((filtro_pergunta_id, set(filtro["valores"])))

    # --- Universo autorizado -------------------------------------------------
    # Tenant pelo company_id da coleta; o cliente nunca informa empresa.
    universo_query = db.query(models.Coleta.id).filter(
        models.Coleta.pesquisa_id == pesquisa_id,
        acessos.filtro_company_acessivel(models.Coleta.company_id, current_user),
    )
    total_universo = universo_query.count()

    # --- Recorte por agente --------------------------------------------------
    coletas_query = universo_query
    if agente_ids:
        coletas_query = coletas_query.filter(models.Coleta.agente_id.in_(list(agente_ids)))
    coleta_ids = [row[0] for row in coletas_query.order_by(models.Coleta.id).all()]

    # --- Recorte territorial -------------------------------------------------
    # Pertencimento pela regra oficial (ST_Covers), em lote. Nunca por um
    # setor_id vindo do cliente ou inferido no front.
    classificacao = crud.classificar_coletas_por_setor(
        db, pesquisa_id, current_user, setor_ids=list(setor_ids) if setor_ids else None
    )
    setor_por_coleta = classificacao["classificados"]
    if setor_ids:
        permitidas = set(setor_por_coleta)
        coleta_ids = [coleta_id for coleta_id in coleta_ids if coleta_id in permitidas]

    # --- Dimensoes de filtro + pergunta principal ----------------------------
    # A secundaria entra no MESMO carregamento em lote: uma consulta cobre
    # principal, secundaria e dimensoes. Nada de "para cada coleta, buscar A e B".
    perguntas_necessarias = {pergunta_id, *(item[0] for item in filtros_normalizados)}
    if pergunta_secundaria_id is not None:
        perguntas_necessarias.add(pergunta_secundaria_id)
    valores_reportaveis = carregar_valores_reportaveis(
        db,
        pesquisa_id=pesquisa_id,
        coleta_ids=coleta_ids,
        perguntas_por_id={pid: perguntas[pid] for pid in perguntas_necessarias},
        current_user=current_user,
    )
    coleta_ids = aplicar_filtros_respostas(coleta_ids, valores_reportaveis, filtros_normalizados)

    # --- Universo ANALITICO --------------------------------------------------
    # Aqui, e so aqui, os filtros ESTRUTURAIS (setor, agente, dimensoes) ja
    # foram aplicados e a selecao de categorias ainda nao. Este e o denominador
    # do "% do universo": marcar ou desmarcar uma resposta destaca ou agrupa,
    # mas nao pode mexer no denominador -- senao o percentual muda de
    # significado a cada clique. Custo zero: nenhuma consulta extra.
    total_universo_analitico = len(coleta_ids)

    # A pergunta principal DESTACA (com agrupamento) ou RESTRINGE (sem ele).
    alvo_permitido = set(valores)
    preservados_a = set(valores_preservados or ())
    categoria_por_coleta: dict[int, str] = {}
    agrupada_por_coleta: dict[int, bool] = {}
    total_sem_categoria = 0
    for coleta_id in coleta_ids:
        respostas = valores_reportaveis.get(coleta_id, {}).get(pergunta_id, set())
        rotulo = _rotular(
            respostas, alvo_permitido, preservados_a, agrupar_nao_selecionadas
        )
        if rotulo is None:
            # Sem agrupamento isto mistura "nao selecionada" com "sem valor
            # unico" -- e o legado. Com agrupamento sobra so a ambiguidade
            # real, e o numero passa a explicar a diferenca entre o universo
            # analitico e o que foi representado.
            total_sem_categoria += 1
            continue
        categoria_por_coleta[coleta_id], agrupada_por_coleta[coleta_id] = rotulo

    # --- Par do cruzamento, POR COLETA ---------------------------------------
    # Duas exclusoes DIFERENTES acontecem aqui, e elas nao se confundem:
    #
    #   1. sem valor unico em B  -> ausencia de resposta, contada em total_sem_par
    #   2. valor fora do recorte -> escolha do usuario, nao e buraco de dado
    #
    # `valores_secundarios` ausente significa TODAS as categorias elegiveis de
    # B, que e exatamente o comportamento do contrato anterior.
    secundaria_permitida = set(valores_secundarios) if valores_secundarios else None
    preservados_b = set(valores_preservados_secundarios or ())
    secundaria_por_coleta: dict[int, str] = {}
    agrupada_secundaria_por_coleta: dict[int, bool] = {}
    total_sem_par = 0
    if pergunta_secundaria_id is not None:
        for coleta_id in list(categoria_por_coleta):
            respostas = valores_reportaveis.get(coleta_id, {}).get(
                pergunta_secundaria_id, set()
            )
            # A resolucao do valor unico vem ANTES do recorte: so assim
            # "nao respondeu" continua distinguivel de "respondeu outra coisa".
            valor_b = _resolver_categoria_unica(respostas, None)
            if valor_b is None:
                # Sem par determinavel a coleta sai do cruzamento -- a mesma
                # semantica ja aplicada a principal. Declarada em `total_sem_par`.
                # Vale tambem com agrupamento: "Outros" reune quem RESPONDEU
                # outra coisa, nunca quem nao respondeu.
                total_sem_par += 1
                del categoria_por_coleta[coleta_id]
                agrupada_por_coleta.pop(coleta_id, None)
                continue
            if secundaria_permitida is not None and valor_b not in secundaria_permitida:
                if not agrupar_nao_selecionadas_secundaria:
                    del categoria_por_coleta[coleta_id]
                    agrupada_por_coleta.pop(coleta_id, None)
                    continue
                if valor_b not in preservados_b:
                    valor_b = ROTULO_OUTROS
                    agrupada_secundaria_por_coleta[coleta_id] = True
            secundaria_por_coleta[coleta_id] = valor_b
            agrupada_secundaria_por_coleta.setdefault(coleta_id, False)

    coletas_filtradas = [coleta_id for coleta_id in coleta_ids if coleta_id in categoria_por_coleta]

    # --- Ponto canonico ------------------------------------------------------
    # Mesma regra dos demais Mapas Estrategicos: a mesma coleta cai no mesmo
    # lugar em todos eles.
    pontos: list[dict] = []
    sem_coordenada = 0
    if coletas_filtradas:
        ponto = crud._coleta_ponto_referencia()
        linhas = (
            db.query(
                models.Coleta.id.label("coleta_id"),
                func.ST_Y(ponto).label("lat"),
                func.ST_X(ponto).label("lng"),
            )
            .filter(
                models.Coleta.id.in_(coletas_filtradas),
                acessos.filtro_company_acessivel(models.Coleta.company_id, current_user),
            )
            .order_by(models.Coleta.id)
            .all()
        )
        for linha in linhas:
            if not _coordenada_valida(linha.lat, linha.lng):
                # Contabilizada, nunca descartada em silencio nem colocada em (0,0).
                sem_coordenada += 1
                continue
            pontos.append(
                {
                    "coleta_id": linha.coleta_id,
                    "lat": float(linha.lat),
                    "lng": float(linha.lng),
                    "valor": categoria_por_coleta[linha.coleta_id],
                    # None no modo simples; no cruzado e a resposta da MESMA coleta.
                    "valor_secundario": secundaria_por_coleta.get(linha.coleta_id),
                    "setor_id": setor_por_coleta.get(linha.coleta_id),
                    # O cliente pinta o balde de neutro pela FLAG, nunca pelo
                    # texto: uma opcao real chamada "Outros" nao pode virar cinza.
                    "agrupado": agrupada_por_coleta.get(linha.coleta_id, False),
                    "agrupado_secundario": agrupada_secundaria_por_coleta.get(
                        linha.coleta_id, False
                    ),
                }
            )

    # Contagem por categoria sobre o universo filtrado, incluindo quem nao tem
    # ponto: o total da categoria nao pode depender de haver GPS.
    # A chave e o PAR (valor, agrupado): o balde nunca se soma a uma opcao real
    # de mesmo nome, e vice-versa.
    contagem = Counter(
        (categoria_por_coleta[coleta_id], agrupada_por_coleta[coleta_id])
        for coleta_id in coletas_filtradas
    )
    # Ordem de leitura: maior primeiro. Empate pela ordem ORIGINAL das opcoes
    # enviadas em `valores`, que e estavel entre requisicoes iguais. O balde e
    # as preservadas nao estao em `valores`, entao caem no fim do desempate --
    # nunca ganham de uma selecionada com a mesma contagem.
    posicao = {valor: indice for indice, valor in enumerate(valores)}
    categorias = sorted(
        (
            {"valor": valor, "total": total, "agrupado": agrupado}
            for (valor, agrupado), total in contagem.items()
            if total > 0
        ),
        key=lambda item: (
            -item["total"],
            posicao.get(item["valor"], len(posicao)),
            item["agrupado"],
            item["valor"],
        ),
    )

    # --- Combinacoes: agregadas A PARTIR dos pares por coleta ----------------
    combinacoes: list[dict] = []
    if pergunta_secundaria_id is not None:
        contagem_par = Counter(
            (
                categoria_por_coleta[coleta_id],
                agrupada_por_coleta[coleta_id],
                secundaria_por_coleta[coleta_id],
                agrupada_secundaria_por_coleta[coleta_id],
            )
            for coleta_id in coletas_filtradas
        )
        combinacoes = sorted(
            (
                {
                    "valor": valor_a,
                    "valor_secundario": valor_b,
                    "total": total,
                    "agrupado": agrupado_a,
                    "agrupado_secundario": agrupado_b,
                }
                for (
                    valor_a,
                    agrupado_a,
                    valor_b,
                    agrupado_b,
                ), total in contagem_par.items()
            ),
            # Maior primeiro; empate deterministico pela ordem original de A e
            # depois pelo rotulo de B.
            key=lambda item: (
                -item["total"],
                posicao.get(item["valor"], len(posicao)),
                item["agrupado"],
                item["valor"],
                item["agrupado_secundario"],
                item["valor_secundario"],
            ),
        )

    return {
        "pesquisa_id": pesquisa.id,
        "pergunta_id": pergunta_id,
        "pergunta_secundaria_id": pergunta_secundaria_id,
        "resumo": {
            "total_universo": total_universo,
            "total_universo_analitico": total_universo_analitico,
            "total_filtrado": len(coletas_filtradas),
            "total_com_coordenada": len(pontos),
            "total_sem_coordenada": sem_coordenada,
            "total_sem_par": total_sem_par,
            "total_sem_categoria": total_sem_categoria,
        },
        "categorias": categorias,
        "combinacoes": combinacoes,
        "pontos": pontos,
    }
