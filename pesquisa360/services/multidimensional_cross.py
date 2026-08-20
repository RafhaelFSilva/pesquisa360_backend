import json
from collections import defaultdict
from itertools import product

from fastapi import HTTPException

from pesquisa360 import crud, schemas
from pesquisa360.core.analytics_config import (
    MAX_CROSS_COMBINATIONS,
    MAX_CROSS_DIMENSIONS,
    MAX_CROSS_NODES,
)
from pesquisa360.db import models
from pesquisa360.question_types import (
    is_categorical_question_type,
    is_multiple_response_question_type,
)


SEM_RESPOSTA = "__SEM_RESPOSTA__"
ROTULO_SEM_RESPOSTA = "Sem resposta"
NAO_X = "__NAO_X__"
ROTULO_NAO_X = "Nao-X"
SEM_SETOR = "__SEM_SETOR__"
ROTULO_SEM_SETOR = "Sem setor"
SETOR_AMBIGUO = "__SETOR_AMBIGUO__"
ROTULO_SETOR_AMBIGUO = "Territorio sobreposto"


def _apply_target_treatments(values, target_value, preserved_set):
    """Aplica os tratamentos da pergunta-alvo preservando a deduplicacao de multipla resposta.

    Ordem da regra: excluido (ja removido antes) -> X -> preservado -> Nao-X.
    """
    if target_value in values:
        return [target_value]
    resultado = []
    for value in values:
        substituto = value if value in preserved_set else NAO_X
        if substituto not in resultado:
            resultado.append(substituto)
    return resultado


def _rotulo_caminho(value):
    """Rotulo neutro. Nao-X agrupa as demais respostas consideradas; nao significa rejeicao."""
    if value == SEM_RESPOSTA:
        return ROTULO_SEM_RESPOSTA
    if value == NAO_X:
        return ROTULO_NAO_X
    if value == SEM_SETOR:
        return ROTULO_SEM_SETOR
    if value == SETOR_AMBIGUO:
        return ROTULO_SETOR_AMBIGUO
    return value


def _classificacao_territorial(db, pesquisa_id, current_user, setor_ids=None):
    """Reutiliza o classificador territorial ja usado pelos Mapas Estrategicos.

    Fonte da verdade geoespacial: PostGIS (`ST_Covers`) sobre o ponto de referencia
    da coleta (`localizacao_inicio`, com fallback para `localizacao_fim`). Nao existe
    `setor_id` gravado na coleta; a associacao e resolvida por consulta.
    """
    classificacao = crud.classificar_coletas_por_setor(
        db=db,
        pesquisa_id=pesquisa_id,
        current_user=current_user,
        setor_ids=setor_ids,
    )
    nome_por_setor = {setor.id: setor.nome for setor in classificacao["setores"]}
    valor_por_coleta = {}
    for coleta_id, setor_id in classificacao["classificados"].items():
        valor_por_coleta[coleta_id] = nome_por_setor.get(setor_id, SEM_SETOR)
    for coleta_id in classificacao["conflito_setor"]:
        # Sobreposicao real: nao duplica a entrevista nem escolhe um setor arbitrario.
        valor_por_coleta[coleta_id] = SETOR_AMBIGUO
    return {
        "valor_por_coleta": valor_por_coleta,
        "setores": classificacao["setores"],
        "nome_por_setor": nome_por_setor,
        "classificados": classificacao["classificados"],
        "sem_setor": set(classificacao["sem_setor"]) | set(classificacao["sem_coordenada"]),
        "conflito": set(classificacao["conflito_setor"]),
    }


def _ordem_territorial(setores):
    """Setores por nome (ID apenas como desempate), tecnicas ao final."""
    ordem = {}
    for posicao, setor in enumerate(
        sorted(setores, key=lambda item: ((item.nome or "").casefold(), item.id)), 1
    ):
        ordem[setor.nome] = posicao
    ordem[SEM_SETOR] = len(ordem) + 1
    ordem[SETOR_AMBIGUO] = len(ordem) + 1
    return ordem


def _limit_error(kind: str, limit: int, found: int) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={
            "erro": f"Limite de {kind} excedido.",
            "limite": limit,
            "quantidade_encontrada": found,
            "sugestao": "Reduza a quantidade de perguntas ou a profundidade maxima.",
        },
    )


def _normalize_scalar(value):
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    normalized = str(value).strip()
    return normalized or None


def _response_values(raw_value, multiple: bool):
    if not multiple:
        value = _normalize_scalar(raw_value)
        return ([value] if value is not None else []), False

    parsed = raw_value
    if isinstance(raw_value, str):
        stripped = raw_value.strip()
        if not stripped:
            return [], False
        try:
            candidate = json.loads(stripped)
            parsed = candidate if isinstance(candidate, list) else stripped
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = stripped

    values = parsed if isinstance(parsed, list) else [parsed]
    result = []
    seen = set()
    duplicate_found = False
    for item in values:
        value = _normalize_scalar(item)
        if value is None:
            continue
        if value in seen:
            duplicate_found = True
            continue
        seen.add(value)
        result.append(value)
    return result, duplicate_found


def _question_order_key(path, option_orders):
    key = []
    for position, value in enumerate(path):
        order = option_orders[position].get(value)
        key.append((0, order, "") if order is not None else (1, 0, value.casefold()))
    return tuple(key)


def _eligible_questions(db, pesquisa_id, current_user):
    pesquisa = db.query(models.Pesquisa).join(models.Projeto).filter(
        models.Pesquisa.id == pesquisa_id,
        models.Projeto.company_id == current_user.company_id,
    ).first()
    if pesquisa is None:
        raise HTTPException(status_code=404, detail="Pesquisa nao encontrada.")
    questions = db.query(models.Pergunta).filter(
        models.Pergunta.pesquisa_id == pesquisa_id,
        models.Pergunta.ativo.is_(True),
    ).order_by(models.Pergunta.ordem, models.Pergunta.id).all()
    return pesquisa, [
        question for question in questions
        if question.eh_resposta_espontanea
        or is_categorical_question_type(question.tipo_pergunta)
    ]


def get_multidimensional_cross_options(db, pesquisa_id, current_user):
    _pesquisa, questions = _eligible_questions(db, pesquisa_id, current_user)
    question_ids = [question.id for question in questions]
    collection_ids = [row[0] for row in db.query(models.Coleta.id).filter(
        models.Coleta.pesquisa_id == pesquisa_id,
        models.Coleta.company_id == current_user.company_id,
    ).all()]
    rows = db.query(
        models.Resposta.coleta_id,
        models.Resposta.pergunta_id,
        models.Resposta.valor_resposta,
    ).join(models.Coleta, models.Coleta.id == models.Resposta.coleta_id).filter(
        models.Coleta.pesquisa_id == pesquisa_id,
        models.Coleta.company_id == current_user.company_id,
        models.Resposta.pergunta_id.in_(question_ids),
    ).all() if question_ids else []
    option_rows = db.query(
        models.Opcao.pergunta_id, models.Opcao.texto, models.Opcao.ordem, models.Opcao.id,
    ).filter(models.Opcao.pergunta_id.in_(question_ids)).order_by(
        models.Opcao.pergunta_id, models.Opcao.ordem, models.Opcao.id,
    ).all() if question_ids else []

    options_by_question = defaultdict(list)
    for question_id, text, order, option_id in option_rows:
        value = _normalize_scalar(text)
        if value is not None:
            options_by_question[question_id].append((value, order or 0, option_id))

    mapping = crud.get_active_spontaneous_mapping_for_report(
        db=db, pesquisa_id=pesquisa_id, current_user=current_user,
    ) if any(question.eh_resposta_espontanea for question in questions) else {}
    active_category_names = [row[0] for row in db.query(
        models.CategoriaRespostaEspontanea.nome,
    ).filter(
        models.CategoriaRespostaEspontanea.pesquisa_id == pesquisa_id,
        models.CategoriaRespostaEspontanea.ativo.is_(True),
    ).order_by(
        models.CategoriaRespostaEspontanea.nome_normalizado,
        models.CategoriaRespostaEspontanea.id,
    ).all()] if any(question.eh_resposta_espontanea for question in questions) else []
    question_by_id = {question.id: question for question in questions}
    observed = defaultdict(lambda: defaultdict(set))
    answered = defaultdict(set)
    for collection_id, question_id, raw_value in rows:
        question = question_by_id[question_id]
        values, _duplicated = _response_values(
            raw_value, is_multiple_response_question_type(question.tipo_pergunta)
        )
        reportable_values = {
            crud.resolve_reportable_response_value(
                pergunta=question,
                valor_resposta=value,
                spontaneous_mapping=mapping,
            )
            for value in values
        }
        if reportable_values:
            answered[question_id].add(collection_id)
        for value in reportable_values:
            observed[question_id][value].add(collection_id)

    dimensions = []
    for question in questions:
        ordered_values = []
        seen = set()
        if not question.eh_resposta_espontanea:
            for value, order, _option_id in options_by_question[question.id]:
                if value not in seen:
                    seen.add(value)
                    ordered_values.append((value, order))
        else:
            for position, value in enumerate(active_category_names, 1):
                if value not in seen:
                    seen.add(value)
                    ordered_values.append((value, position))
        for value in sorted(observed[question.id], key=str.casefold):
            if value not in seen:
                seen.add(value)
                ordered_values.append((value, len(ordered_values) + 1))
        values = [{
            "valor_chave": value,
            "rotulo": value,
            "ordem": position,
            "contagem_entrevistas": len(observed[question.id][value]),
            "origem": "CATEGORIA_ESPONTANEA" if question.eh_resposta_espontanea else "OPCAO",
        } for position, (value, _source_order) in enumerate(ordered_values, 1)]
        values.append({
            "valor_chave": SEM_RESPOSTA,
            "rotulo": ROTULO_SEM_RESPOSTA,
            "ordem": None,
            "contagem_entrevistas": len(collection_ids) - len(answered[question.id]),
            "origem": "SEM_RESPOSTA",
        })
        dimensions.append({
            "pergunta_id": question.id,
            "ordem": question.ordem,
            "texto_pergunta": question.texto_pergunta,
            "tipo_pergunta": question.tipo_pergunta,
            "eh_resposta_espontanea": question.eh_resposta_espontanea,
            "papel_analitico": question.papel_analitico,
            "metadados_analiticos": question.metadados_analiticos or {},
            "cardinalidade_observada": len(observed[question.id]),
            "valores": values,
        })

    territorio = _classificacao_territorial(db, pesquisa_id, current_user)
    contagem_territorial = defaultdict(set)
    for coleta_id in collection_ids:
        contagem_territorial[territorio["valor_por_coleta"].get(coleta_id, SEM_SETOR)].add(coleta_id)
    valores_territorio = [{
        "setor_id": setor.id,
        "valor_chave": setor.nome,
        "rotulo": setor.nome,
        "contagem_entrevistas": len(contagem_territorial.get(setor.nome, ())),
        "origem": "SETOR",
    } for setor in sorted(territorio["setores"], key=lambda item: ((item.nome or "").casefold(), item.id))]
    valores_territorio.append({
        "setor_id": None,
        "valor_chave": SEM_SETOR,
        "rotulo": ROTULO_SEM_SETOR,
        "contagem_entrevistas": len(contagem_territorial.get(SEM_SETOR, ())),
        "origem": "SEM_SETOR",
    })
    if contagem_territorial.get(SETOR_AMBIGUO):
        valores_territorio.append({
            "setor_id": None,
            "valor_chave": SETOR_AMBIGUO,
            "rotulo": ROTULO_SETOR_AMBIGUO,
            "contagem_entrevistas": len(contagem_territorial[SETOR_AMBIGUO]),
            "origem": "AMBIGUO",
        })

    return {
        "pesquisa_id": pesquisa_id,
        "dimensoes": dimensions,
        "territorios": [{"nivel": schemas.NivelTerritorial.SETOR, "valores": valores_territorio}],
    }


def build_multidimensional_cross(db, pesquisa_id, payload, current_user):
    pesquisa, _eligible = _eligible_questions(db, pesquisa_id, current_user)

    requested_ids = payload.pergunta_ids

    # Sequencia analitica = [pergunta-alvo] + dimensoes de aprofundamento.
    # O alvo ocupa a raiz da arvore; sua posicao nunca depende da ordem das dimensoes.
    # Sem `alvo`, o comportamento legado (ordem de pergunta_ids) e preservado.
    alvo_pergunta_id = payload.alvo.pergunta_id if payload.alvo is not None else None
    if payload.dimensoes is not None:
        dimensoes = [
            {"tipo": item.tipo.value, "pergunta_id": item.pergunta_id, "nivel": item.nivel}
            for item in payload.dimensoes
        ]
    else:
        dimensoes = [
            {"tipo": schemas.TipoDimensaoCruzamento.PERGUNTA.value, "pergunta_id": question_id, "nivel": None}
            for question_id in requested_ids
            if question_id != alvo_pergunta_id
        ]
    if alvo_pergunta_id is None:
        sequence = dimensoes
    else:
        sequence = [{
            "tipo": schemas.TipoDimensaoCruzamento.PERGUNTA.value,
            "pergunta_id": alvo_pergunta_id,
            "nivel": None,
        }] + dimensoes
    if len(sequence) > MAX_CROSS_DIMENSIONS:
        raise _limit_error("dimensoes", MAX_CROSS_DIMENSIONS, len(sequence))

    depth = payload.profundidade_maxima or len(sequence)
    processed = sequence[:depth]
    processed_ids = [
        item["pergunta_id"] for item in processed
        if item["tipo"] == schemas.TipoDimensaoCruzamento.PERGUNTA.value
    ]
    position_question_ids = [item["pergunta_id"] for item in processed]
    territorio_descriptor = next(
        (item for item in processed if item["tipo"] == schemas.TipoDimensaoCruzamento.TERRITORIO.value),
        None,
    )
    questions = db.query(models.Pergunta).filter(
        models.Pergunta.id.in_(requested_ids),
        models.Pergunta.pesquisa_id == pesquisa_id,
        models.Pergunta.ativo.is_(True),
    ).all()
    by_id = {question.id: question for question in questions}
    if set(by_id) != set(requested_ids):
        raise HTTPException(
            status_code=422,
            detail="Uma ou mais perguntas nao sao elegiveis para esta pesquisa.",
        )
    requested_questions = [by_id[question_id] for question_id in requested_ids]
    if not all(
        question.eh_resposta_espontanea
        or is_categorical_question_type(question.tipo_pergunta)
        for question in requested_questions
    ):
        raise HTTPException(status_code=422, detail="Todas as perguntas devem ser categoricas.")
    ordered_questions = [by_id[question_id] for question_id in processed_ids]

    # --- Pergunta-alvo -------------------------------------------------------
    # O alvo e a raiz da arvore (nivel 1). Ausente mantem o comportamento legado.
    target = payload.alvo
    target_id = None
    target_value = None
    excluded_values = set()
    preserved_values = []
    one_vs_rest = False
    if target is not None:
        raiz = processed[0] if processed else None
        if (
            raiz is None
            or raiz["tipo"] != schemas.TipoDimensaoCruzamento.PERGUNTA.value
            or target.pergunta_id != raiz["pergunta_id"]
        ):
            raise HTTPException(
                status_code=422,
                detail="A pergunta-alvo deve ocupar a raiz da sequencia analitica.",
            )
        target_id = target.pergunta_id
        one_vs_rest = target.modo == schemas.ModoAlvoCruzamento.ONE_VS_REST
        if one_vs_rest:
            target_value = target.valor
            excluded_values = set(target.valores_excluidos)
            preserved_values = list(target.valores_preservados)
            if any(item.pergunta_id == target_id for item in payload.filtros_respostas):
                raise HTTPException(
                    status_code=422,
                    detail="A pergunta-alvo em ONE_VS_REST nao aceita filtros_respostas; use valores_excluidos.",
                )

    preserved_set = set(preserved_values)

    collection_ids = [row[0] for row in db.query(models.Coleta.id).filter(
        models.Coleta.pesquisa_id == pesquisa_id,
        models.Coleta.company_id == current_user.company_id,
    ).order_by(models.Coleta.id).all()]

    # --- Territorio ----------------------------------------------------------
    # O filtro restringe o universo ANTES das dimensoes, do alvo e das bases.
    # A dimensao insere o territorio na arvore. Sao independentes e combinaveis.
    filtro_territorial = payload.filtro_territorial
    filtro_ativo = filtro_territorial is not None and filtro_territorial.ativo
    classificacao_territorial = None
    if filtro_ativo or territorio_descriptor is not None:
        # Classifica sempre contra todos os setores analiticos da pesquisa: restringir
        # a classificacao ao recorte transformaria os demais setores em "Sem setor".
        classificacao_territorial = _classificacao_territorial(db, pesquisa_id, current_user)
    if filtro_ativo:
        desconhecidos = [
            setor_id for setor_id in filtro_territorial.setor_ids
            if setor_id not in classificacao_territorial["nome_por_setor"]
        ]
        if desconhecidos:
            raise HTTPException(
                status_code=404,
                detail="Setor analitico nao encontrado para esta pesquisa.",
            )
        nomes_selecionados = {
            classificacao_territorial["nome_por_setor"][setor_id]
            for setor_id in filtro_territorial.setor_ids
            if setor_id in classificacao_territorial["nome_por_setor"]
        }
        permitidos = {
            coleta_id
            for coleta_id, valor in classificacao_territorial["valor_por_coleta"].items()
            if valor in nomes_selecionados
        }
        if filtro_territorial.incluir_sem_setor:
            permitidos |= classificacao_territorial["sem_setor"]
        collection_ids = [coleta_id for coleta_id in collection_ids if coleta_id in permitidos]

    valor_territorial_por_coleta = (
        classificacao_territorial["valor_por_coleta"] if classificacao_territorial else {}
    )
    ordem_territorial = (
        _ordem_territorial(classificacao_territorial["setores"]) if classificacao_territorial else {}
    )

    total_interviews = len(collection_ids)

    answer_rows = db.query(
        models.Resposta.id,
        models.Resposta.coleta_id,
        models.Resposta.pergunta_id,
        models.Resposta.valor_resposta,
    ).join(models.Coleta, models.Coleta.id == models.Resposta.coleta_id).filter(
        models.Coleta.pesquisa_id == pesquisa_id,
        models.Coleta.company_id == current_user.company_id,
        models.Resposta.pergunta_id.in_(processed_ids),
    ).order_by(models.Resposta.coleta_id, models.Resposta.pergunta_id, models.Resposta.id).all()

    options = db.query(
        models.Opcao.pergunta_id,
        models.Opcao.texto,
        models.Opcao.ordem,
        models.Opcao.id,
    ).filter(models.Opcao.pergunta_id.in_(processed_ids)).order_by(
        models.Opcao.pergunta_id, models.Opcao.ordem, models.Opcao.id
    ).all()
    option_orders_by_question = defaultdict(dict)
    for question_id, text, order, _option_id in options:
        normalized = _normalize_scalar(text)
        if normalized is not None and normalized not in option_orders_by_question[question_id]:
            option_orders_by_question[question_id][normalized] = order if order is not None else 0

    multiple_by_id = {
        question.id: is_multiple_response_question_type(question.tipo_pergunta)
        for question in ordered_questions
    }
    spontaneous_mapping = crud.get_active_spontaneous_mapping_for_report(
        db=db, pesquisa_id=pesquisa_id, current_user=current_user,
    ) if any(question.eh_resposta_espontanea for question in ordered_questions) else {}
    answers = defaultdict(lambda: defaultdict(list))
    answer_record_counts = defaultdict(int)
    duplicate_anomaly = False
    for _answer_id, collection_id, question_id, raw_value in answer_rows:
        answer_record_counts[(collection_id, question_id)] += 1
        values, duplicated_in_value = _response_values(
            raw_value, multiple_by_id[question_id]
        )
        question = by_id[question_id]
        values = [
            crud.resolve_reportable_response_value(
                pergunta=question,
                valor_resposta=value,
                spontaneous_mapping=spontaneous_mapping,
            )
            for value in values
        ]
        duplicate_anomaly = duplicate_anomaly or duplicated_in_value
        existing = answers[collection_id][question_id]
        for value in values:
            if value in existing:
                duplicate_anomaly = True
            else:
                existing.append(value)
    if any(count > 1 for count in answer_record_counts.values()):
        duplicate_anomaly = True

    valid_collection_ids = []
    values_by_collection = {}
    for collection_id in collection_ids:
        dimensions = []
        complete = True
        for descriptor in processed:
            if descriptor["tipo"] == schemas.TipoDimensaoCruzamento.TERRITORIO.value:
                # Coleta sem territorio nao desaparece: vira categoria propria.
                dimensions.append([valor_territorial_por_coleta.get(collection_id, SEM_SETOR)])
                continue
            question_id = descriptor["pergunta_id"]
            values = answers[collection_id].get(question_id, [])
            if not values:
                if payload.incluir_sem_resposta:
                    values = [SEM_RESPOSTA]
                else:
                    complete = False
                    break
            if one_vs_rest and question_id == target_id:
                values = [value for value in values if value not in excluded_values]
                if not values:
                    # Resposta excluida sai do universo: nao entra em X, nem em Nao-X,
                    # nem em categoria preservada, nem no denominador da comparacao.
                    complete = False
                    break
                values = _apply_target_treatments(values, target_value, preserved_set)
            dimensions.append(values)
        if complete:
            valid_collection_ids.append(collection_id)
            values_by_collection[collection_id] = dimensions

    node_collections = defaultdict(set)
    combinations_processed = 0
    for collection_id in valid_collection_ids:
        observed_paths = set(product(*values_by_collection[collection_id]))
        combinations_processed += len(observed_paths)
        if combinations_processed > MAX_CROSS_COMBINATIONS:
            raise _limit_error(
                "combinacoes processadas", MAX_CROSS_COMBINATIONS, combinations_processed
            )
        for path in observed_paths:
            for level in range(1, depth + 1):
                node_collections[path[:level]].add(collection_id)
        if len(node_collections) > MAX_CROSS_NODES:
            raise _limit_error("nodos", MAX_CROSS_NODES, len(node_collections))

    cardinalities = [set() for _ in processed]
    for path in node_collections:
        for position, value in enumerate(path):
            cardinalities[position].add(value)

    if one_vs_rest:
        # Ordem fixa da leitura: X, Nao-X e depois as categorias preservadas,
        # estas na ordem original do questionario.
        ordem_original = option_orders_by_question[target_id]
        ordem_alvo = {target_value: 0, NAO_X: 1}
        for posicao, valor in enumerate(
            sorted(preserved_values, key=lambda item: (ordem_original.get(item, 10**6), item)), 2
        ):
            ordem_alvo[valor] = posicao
        option_orders_by_question[target_id] = ordem_alvo

    available_values = {}
    for position, descriptor in enumerate(processed):
        if descriptor["tipo"] != schemas.TipoDimensaoCruzamento.PERGUNTA.value:
            continue
        question_id = descriptor["pergunta_id"]
        available_values[question_id] = (
            cardinalities[position]
            | {SEM_RESPOSTA}
            | set(option_orders_by_question[question_id])
        )
    filters = {item.pergunta_id: set(item.valores) for item in payload.filtros_respostas}
    if any(question_id not in processed_ids for question_id in filters):
        raise HTTPException(
            status_code=422,
            detail="Filtros devem usar apenas dimensoes processadas pela profundidade maxima.",
        )
    active_category_names = {
        row[0] for row in db.query(models.CategoriaRespostaEspontanea.nome).filter(
            models.CategoriaRespostaEspontanea.pesquisa_id == pesquisa_id,
            models.CategoriaRespostaEspontanea.ativo.is_(True),
        ).all()
    } if any(question.eh_resposta_espontanea for question in ordered_questions) else set()
    for position, question in enumerate(ordered_questions):
        if question.eh_resposta_espontanea:
            available_values[question.id].update(active_category_names)
    for question_id, values in filters.items():
        if question_id in processed_ids and not values.issubset(available_values[question_id]):
            raise HTTPException(
                status_code=422,
                detail=f"Filtro contem valor invalido para a pergunta {question_id}.",
            )

    visible_paths = {
        path for path in node_collections
        if all(
            position_question_ids[position] is None
            or position_question_ids[position] not in filters
            or value in filters[position_question_ids[position]]
            for position, value in enumerate(path)
        )
    }
    option_orders = [
        ordem_territorial
        if descriptor["tipo"] == schemas.TipoDimensaoCruzamento.TERRITORIO.value
        else option_orders_by_question[descriptor["pergunta_id"]]
        for descriptor in processed
    ]
    sorted_paths = sorted(
        visible_paths,
        key=lambda path: (len(path), _question_order_key(path, option_orders)),
    )
    paths_with_children = {path[:-1] for path in visible_paths if len(path) > 1}
    base_valid = len(valid_collection_ids)
    nodes = []
    for path in sorted_paths:
        level = len(path)
        count = len(node_collections[path])
        parent_base = base_valid if level == 1 else len(node_collections[path[:-1]])
        nodes.append({
            "nivel": level,
            "caminho": [
                {
                    "pergunta_id": processed[position]["pergunta_id"],
                    "tipo": processed[position]["tipo"],
                    "nivel_territorial": processed[position]["nivel"],
                    "valor_chave": value,
                    "rotulo": _rotulo_caminho(value),
                }
                for position, value in enumerate(path)
            ],
            "contagem_entrevistas": count,
            "base_pai": parent_base,
            "percentual_pai": round(count / parent_base * 100, 2) if parent_base else 0.0,
            "percentual_total": round(count / base_valid * 100, 2) if base_valid else 0.0,
            "tem_filhos": path in paths_with_children,
        })

    dimensions = []
    for position, descriptor in enumerate(processed):
        if descriptor["tipo"] == schemas.TipoDimensaoCruzamento.TERRITORIO.value:
            dimensions.append({
                "posicao": position + 1,
                "pergunta_id": None,
                "tipo": schemas.TipoDimensaoCruzamento.TERRITORIO,
                "nivel_territorial": descriptor["nivel"],
                "ordem": 0,
                "texto_pergunta": "Setor",
                "tipo_pergunta": "TERRITORIO",
                "eh_obrigatoria": False,
                "eh_resposta_espontanea": False,
                "papel_analitico": None,
                "metadados_analiticos": {},
                "cardinalidade_observada": len(cardinalities[position]),
                "eh_multipla_resposta": False,
            })
            continue
        question = by_id[descriptor["pergunta_id"]]
        dimensions.append({
            "posicao": position + 1,
            "pergunta_id": question.id,
            "tipo": schemas.TipoDimensaoCruzamento.PERGUNTA,
            "nivel_territorial": None,
            "ordem": question.ordem,
            "texto_pergunta": question.texto_pergunta,
            "tipo_pergunta": question.tipo_pergunta,
            "eh_obrigatoria": question.eh_obrigatoria,
            "eh_resposta_espontanea": question.eh_resposta_espontanea,
            "papel_analitico": question.papel_analitico,
            "metadados_analiticos": question.metadados_analiticos or {},
            "cardinalidade_observada": len(cardinalities[position]),
            "eh_multipla_resposta": multiple_by_id[question.id],
        })

    has_multiple = any(multiple_by_id.values())
    warnings = []
    if duplicate_anomaly:
        warnings.append(
            "Foram detectadas respostas duplicadas na mesma entrevista e pergunta; valores identicos foram deduplicados."
        )
    if has_multiple:
        warnings.append(
            "Dimensoes de multipla resposta contam entrevistas por categoria; percentuais de filhos podem exceder 100%."
        )
    if not valid_collection_ids:
        warnings.append("Nao existem entrevistas validas para as dimensoes selecionadas.")

    return {
        "pesquisa_id": pesquisa_id,
        "total_entrevistas": total_interviews,
        "base_valida": base_valid,
        "dimensoes": dimensions,
        "nodos": nodes,
        "metadados_execucao": {
            "quantidade_dimensoes_solicitadas": len(requested_ids),
            "quantidade_dimensoes_processadas": depth,
            "profundidade_maxima": depth,
            "quantidade_nodos": len(nodes),
            "incluir_sem_resposta": payload.incluir_sem_resposta,
            "possui_multipla_resposta": has_multiple,
            "somatorio_percentuais_pode_exceder_100": has_multiple,
            "alvo": {
                "pergunta_id": target.pergunta_id,
                "modo": target.modo,
                "valor": target.valor,
                "valores_excluidos": target.valores_excluidos,
                "valores_preservados": target.valores_preservados,
            } if target is not None else None,
            "filtro_territorial": filtro_territorial if filtro_ativo else None,
            "dimensao_territorial": territorio_descriptor["nivel"] if territorio_descriptor else None,
        },
        "avisos": warnings,
    }
