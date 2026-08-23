from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from pesquisa360 import crud, schemas
from pesquisa360.db import models
from pesquisa360.core.dependencies import get_db, get_current_user
from pesquisa360.services.multidimensional_cross import (
    build_multidimensional_cross,
    get_multidimensional_cross_options,
)
from pesquisa360.services import mapa_respostas_geo

router = APIRouter()

def check_access(db: Session, pesquisa_id: int, current_user: models.Usuario):
    """
    Helper para validar se a pesquisa pertence à empresa do usuário.
    """
    pesquisa = db.query(models.Pesquisa).join(models.Projeto).filter(
        models.Pesquisa.id == pesquisa_id,
        models.Projeto.company_id == current_user.company_id
    ).first()
    
    if not pesquisa:
        raise HTTPException(
            status_code=404,
            detail="Pesquisa não encontrada ou você não tem permissão para visualizar este relatório."
        )
    return pesquisa

def parse_csv_ids(value: str | None) -> list[int] | None:
    if value is None or value.strip() == "":
        return None
    try:
        ids = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise ValueError("IDs devem ser inteiros separados por vírgula.") from exc
    return ids or None

@router.get("/relatorios/pesquisas/{pesquisa_id}/filtros/")
def read_relatorio_filtros(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    current_user: models.Usuario = Depends(get_current_user)
):
    """
    Retorna opcoes de filtros disponiveis para a pesquisa do tenant.
    """
    return crud.get_relatorio_filtros(
        db=db,
        pesquisa_id=pesquisa_id,
        current_user=current_user
    )

@router.get("/relatorios/pesquisas/{pesquisa_id}/mapas/territorio/diagnostico/")
def read_mapa_territorio_diagnostico(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    current_user: models.Usuario = Depends(get_current_user)
):
    """
    Diagnostica a base territorial analitica da pesquisa.
    """
    check_access(db, pesquisa_id, current_user)
    return crud.get_mapa_territorio_diagnostico(
        db=db,
        pesquisa_id=pesquisa_id,
        current_user=current_user,
    )

@router.post("/relatorios/pesquisas/{pesquisa_id}/mapas/preview/")
def read_mapa_preview(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    payload: schemas.MapaPreviewRequest,
    current_user: models.Usuario = Depends(get_current_user)
):
    """
    Retorna previa agregada para Mapas Estrategicos.
    """
    check_access(db, pesquisa_id, current_user)
    return crud.get_mapa_preview(
        db=db,
        pesquisa_id=pesquisa_id,
        current_user=current_user,
        payload=payload,
    )


@router.post(
    "/relatorios/pesquisas/{pesquisa_id}/mapas/respostas-georreferenciadas/",
    response_model=schemas.MapaRespostasGeoResponse,
)
def read_mapa_respostas_georreferenciadas(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    payload: schemas.MapaRespostasGeoRequest,
    current_user: models.Usuario = Depends(get_current_user)
):
    """Coletas individuais como pontos, categorizadas pela pergunta principal.

    POST porque o recorte aceita N dimensoes de filtro. O tenant vem do JWT;
    pesquisa de outra empresa responde 404, nunca 403.

    `pergunta_secundaria_id` opcional liga o modo cruzado: a mesma coleta passa
    a carregar duas categorias. Ausente, a resposta e a de sempre.
    """
    return mapa_respostas_geo.gerar_mapa_respostas_geo(
        db,
        pesquisa_id=pesquisa_id,
        pergunta_id=payload.pergunta_id,
        pergunta_secundaria_id=payload.pergunta_secundaria_id,
        valores_secundarios=payload.valores_secundarios,
        valores=payload.valores,
        filtros_respostas=[
            {"pergunta_id": item.pergunta_id, "valores": item.valores}
            for item in payload.filtros_respostas
        ],
        setor_ids=payload.setor_ids,
        agente_ids=payload.agente_ids,
        agrupar_nao_selecionadas=payload.agrupar_nao_selecionadas,
        agrupar_nao_selecionadas_secundaria=payload.agrupar_nao_selecionadas_secundaria,
        valores_preservados=payload.valores_preservados,
        valores_preservados_secundarios=payload.valores_preservados_secundarios,
        current_user=current_user,
    )


@router.get(
    "/relatorios/pesquisas/{pesquisa_id}/configuracoes-executivas/",
    response_model=List[schemas.ConfiguracaoRelatorioExecutivoRead],
)
def list_configuracoes_executivas(
    *, db: Session = Depends(get_db), pesquisa_id: int,
    tipo_relatorio: schemas.TipoRelatorioExecutivo | None = None,
    current_user: models.Usuario = Depends(get_current_user),
):
    return crud.list_configuracoes_relatorio_executivo(
        db, pesquisa_id, current_user, tipo_relatorio
    )


@router.post(
    "/relatorios/pesquisas/{pesquisa_id}/configuracoes-executivas/",
    response_model=schemas.ConfiguracaoRelatorioExecutivoRead,
    status_code=status.HTTP_201_CREATED,
)
def create_configuracao_executiva(
    *, db: Session = Depends(get_db), pesquisa_id: int,
    payload: schemas.ConfiguracaoRelatorioExecutivoCreate,
    current_user: models.Usuario = Depends(get_current_user),
):
    return crud.create_configuracao_relatorio_executivo(db, pesquisa_id, payload, current_user)


@router.get(
    "/relatorios/pesquisas/{pesquisa_id}/configuracoes-executivas/{configuracao_id}/",
    response_model=schemas.ConfiguracaoRelatorioExecutivoRead,
)
def get_configuracao_executiva(
    *, db: Session = Depends(get_db), pesquisa_id: int, configuracao_id: int,
    current_user: models.Usuario = Depends(get_current_user),
):
    return crud.get_configuracao_relatorio_executivo(db, pesquisa_id, configuracao_id, current_user)


@router.patch(
    "/relatorios/pesquisas/{pesquisa_id}/configuracoes-executivas/{configuracao_id}/",
    response_model=schemas.ConfiguracaoRelatorioExecutivoRead,
)
def update_configuracao_executiva(
    *, db: Session = Depends(get_db), pesquisa_id: int, configuracao_id: int,
    payload: schemas.ConfiguracaoRelatorioExecutivoUpdate,
    current_user: models.Usuario = Depends(get_current_user),
):
    return crud.update_configuracao_relatorio_executivo(
        db, pesquisa_id, configuracao_id, payload, current_user
    )


@router.delete("/relatorios/pesquisas/{pesquisa_id}/configuracoes-executivas/{configuracao_id}/")
def delete_configuracao_executiva(
    *, db: Session = Depends(get_db), pesquisa_id: int, configuracao_id: int,
    current_user: models.Usuario = Depends(get_current_user),
):
    return crud.delete_configuracao_relatorio_executivo(db, pesquisa_id, configuracao_id, current_user)


@router.post(
    "/relatorios/pesquisas/{pesquisa_id}/configuracoes-executivas/{configuracao_id}/secoes/",
    response_model=schemas.ConfiguracaoRelatorioExecutivoRead,
    status_code=status.HTTP_201_CREATED,
)
def create_secao_executiva(
    *, db: Session = Depends(get_db), pesquisa_id: int, configuracao_id: int,
    payload: schemas.SecaoRelatorioExecutivoCreate,
    current_user: models.Usuario = Depends(get_current_user),
):
    return crud.create_secao_relatorio_executivo(
        db, pesquisa_id, configuracao_id, payload, current_user
    )


@router.patch(
    "/relatorios/pesquisas/{pesquisa_id}/configuracoes-executivas/{configuracao_id}/secoes/{secao_id}/",
    response_model=schemas.ConfiguracaoRelatorioExecutivoRead,
)
def update_secao_executiva(
    *, db: Session = Depends(get_db), pesquisa_id: int, configuracao_id: int, secao_id: int,
    payload: schemas.SecaoRelatorioExecutivoUpdate,
    current_user: models.Usuario = Depends(get_current_user),
):
    return crud.update_secao_relatorio_executivo(
        db, pesquisa_id, configuracao_id, secao_id, payload, current_user
    )


@router.delete(
    "/relatorios/pesquisas/{pesquisa_id}/configuracoes-executivas/{configuracao_id}/secoes/{secao_id}/"
)
def delete_secao_executiva(
    *, db: Session = Depends(get_db), pesquisa_id: int, configuracao_id: int, secao_id: int,
    current_user: models.Usuario = Depends(get_current_user),
):
    return crud.delete_secao_relatorio_executivo(
        db, pesquisa_id, configuracao_id, secao_id, current_user
    )


@router.patch(
    "/relatorios/pesquisas/{pesquisa_id}/configuracoes-executivas/{configuracao_id}/reordenar-secoes/",
    response_model=schemas.ConfiguracaoRelatorioExecutivoRead,
)
def reorder_secoes_executivas(
    *, db: Session = Depends(get_db), pesquisa_id: int, configuracao_id: int,
    payload: schemas.ReordenarRelatorioExecutivoRequest,
    current_user: models.Usuario = Depends(get_current_user),
):
    return crud.reorder_secoes_relatorio_executivo(
        db, pesquisa_id, configuracao_id, payload, current_user
    )


@router.post(
    "/relatorios/pesquisas/{pesquisa_id}/configuracoes-executivas/{configuracao_id}/secoes/{secao_id}/analises/",
    response_model=schemas.ConfiguracaoRelatorioExecutivoRead,
    status_code=status.HTTP_201_CREATED,
)
def create_analise_executiva(
    *, db: Session = Depends(get_db), pesquisa_id: int, configuracao_id: int, secao_id: int,
    payload: schemas.AnaliseRelatorioExecutivoCreate,
    current_user: models.Usuario = Depends(get_current_user),
):
    return crud.create_analise_relatorio_executivo(
        db, pesquisa_id, configuracao_id, secao_id, payload, current_user
    )


@router.put(
    "/relatorios/pesquisas/{pesquisa_id}/configuracoes-executivas/{configuracao_id}/secoes/{secao_id}/analises/{analise_id}/",
    response_model=schemas.ConfiguracaoRelatorioExecutivoRead,
)
def update_analise_executiva(
    *, db: Session = Depends(get_db), pesquisa_id: int, configuracao_id: int,
    secao_id: int, analise_id: int, payload: schemas.AnaliseRelatorioExecutivoUpdate,
    current_user: models.Usuario = Depends(get_current_user),
):
    return crud.update_analise_relatorio_executivo(
        db, pesquisa_id, configuracao_id, secao_id, analise_id, payload, current_user
    )


@router.delete(
    "/relatorios/pesquisas/{pesquisa_id}/configuracoes-executivas/{configuracao_id}/secoes/{secao_id}/analises/{analise_id}/"
)
def delete_analise_executiva(
    *, db: Session = Depends(get_db), pesquisa_id: int, configuracao_id: int,
    secao_id: int, analise_id: int,
    current_user: models.Usuario = Depends(get_current_user),
):
    return crud.delete_analise_relatorio_executivo(
        db, pesquisa_id, configuracao_id, secao_id, analise_id, current_user
    )


@router.patch(
    "/relatorios/pesquisas/{pesquisa_id}/configuracoes-executivas/{configuracao_id}/secoes/{secao_id}/reordenar-analises/",
    response_model=schemas.ConfiguracaoRelatorioExecutivoRead,
)
def reorder_analises_executivas(
    *, db: Session = Depends(get_db), pesquisa_id: int, configuracao_id: int, secao_id: int,
    payload: schemas.ReordenarRelatorioExecutivoRequest,
    current_user: models.Usuario = Depends(get_current_user),
):
    return crud.reorder_analises_relatorio_executivo(
        db, pesquisa_id, configuracao_id, secao_id, payload, current_user
    )

@router.get("/relatorios/pesquisas/{pesquisa_id}/simples/", response_model=schemas.RelatorioPesquisa)
@router.get("/pesquisas/{pesquisa_id}/simples/", response_model=schemas.RelatorioPesquisa)
def read_relatorio_simples(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    agente_ids: str | None = None,
    setor_ids: str | None = None,
    current_user: models.Usuario = Depends(get_current_user)
):
    """
    Retorna um relatório agregado simples.
    FILTRO: Apenas usuários da mesma empresa do projeto podem visualizar.
    """
    # 1. Validação de Segurança (Empresa)
    check_access(db, pesquisa_id, current_user)
    try:
        agente_id_list = parse_csv_ids(agente_ids)
        setor_id_list = parse_csv_ids(setor_ids)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # 2. Geração do Relatório
    # (O crud.get_relatorio_pesquisa lê dados brutos, mas já validamos o acesso acima)
    relatorio = crud.get_relatorio_pesquisa(
        db=db,
        pesquisa_id=pesquisa_id,
        current_user=current_user,
        agente_ids=agente_id_list,
        setor_ids=setor_id_list
    )
    
    if not relatorio:
         raise HTTPException(status_code=404, detail="Não foi possível gerar o relatório (sem dados ou erro interno)")
         
    return relatorio

@router.post("/relatorios/pesquisas/{pesquisa_id}/crosstab/", response_model=schemas.CrosstabResponse)
@router.post("/pesquisas/{pesquisa_id}/crosstab/", response_model=schemas.CrosstabResponse)
def read_relatorio_crosstab(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    crosstab_in: schemas.CrosstabRequest,
    agente_ids: str | None = None,
    setor_ids: str | None = None,
    current_user: models.Usuario = Depends(get_current_user)
):
    """
    Retorna tabulação cruzada (Crosstab).
    FILTRO: Apenas usuários da mesma empresa.
    """
    # 1. Validação de Segurança
    check_access(db, pesquisa_id, current_user)
    try:
        agente_id_list = parse_csv_ids(agente_ids)
        setor_id_list = parse_csv_ids(setor_ids)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    perguntas = db.query(models.Pergunta).filter(
        models.Pergunta.pesquisa_id == pesquisa_id,
        models.Pergunta.id.in_([
            crosstab_in.pergunta_linha_id,
            crosstab_in.pergunta_coluna_id
        ]),
        models.Pergunta.ativo.is_(True)
    ).all()
    perguntas_por_id = {pergunta.id: pergunta.texto_pergunta for pergunta in perguntas}
    pergunta_linha = perguntas_por_id.get(crosstab_in.pergunta_linha_id)
    pergunta_coluna = perguntas_por_id.get(crosstab_in.pergunta_coluna_id)

    if pergunta_linha is None or pergunta_coluna is None:
        raise HTTPException(status_code=404, detail="Pergunta nao encontrada.")

    # 2. Geração do Relatório
    # Nota: Atualizamos a chamada do CRUD para passar o current_user se necessário,
    # ou confiamos na validação acima. O CRUD enviado anteriormente recebia current_user.
    try:
        dados_crosstab = crud.get_report_crosstab(
            db=db, 
            pesquisa_id=pesquisa_id,
            pergunta_linha_id=crosstab_in.pergunta_linha_id,
            pergunta_coluna_id=crosstab_in.pergunta_coluna_id,
            current_user=current_user,
            agente_ids=agente_id_list,
            setor_ids=setor_id_list
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    linhas = {}
    totais_linha = {}
    for item in dados_crosstab:
        valor_linha = "" if item.get("linha") is None else str(item.get("linha"))
        valor_coluna = "" if item.get("coluna") is None else str(item.get("coluna"))
        contagem = int(item.get("valor") or 0)

        linhas.setdefault(valor_linha, [])
        linhas[valor_linha].append({
            "valor_coluna": valor_coluna,
            "contagem": contagem,
        })
        totais_linha[valor_linha] = totais_linha.get(valor_linha, 0) + contagem

    dados = []
    for valor_linha, celulas in linhas.items():
        total_linha = totais_linha.get(valor_linha, 0)
        dados.append({
            "valor_linha": valor_linha,
            "celulas": [
                {
                    "valor_coluna": celula["valor_coluna"],
                    "contagem": celula["contagem"],
                    "percentual": round((celula["contagem"] / total_linha) * 100, 2) if total_linha else 0.0,
                }
                for celula in celulas
            ]
        })

    return {
        "pergunta_linha": str(pergunta_linha),
        "pergunta_coluna": str(pergunta_coluna),
        "dados": dados
    }


@router.post(
    "/relatorios/pesquisas/{pesquisa_id}/cruzamentos-multidimensionais/",
    response_model=schemas.CruzamentoMultidimensionalResponse,
)
@router.post(
    "/pesquisas/{pesquisa_id}/cruzamentos-multidimensionais/",
    response_model=schemas.CruzamentoMultidimensionalResponse,
)
def read_cruzamento_multidimensional(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    payload: schemas.CruzamentoMultidimensionalRequest,
    current_user: models.Usuario = Depends(get_current_user),
):
    check_access(db, pesquisa_id, current_user)
    return build_multidimensional_cross(db, pesquisa_id, payload, current_user)


@router.get(
    "/relatorios/pesquisas/{pesquisa_id}/cruzamentos-multidimensionais/opcoes/",
    response_model=schemas.CruzamentoOpcoesResponse,
)
def read_cruzamento_multidimensional_opcoes(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    current_user: models.Usuario = Depends(get_current_user),
):
    check_access(db, pesquisa_id, current_user)
    return get_multidimensional_cross_options(db, pesquisa_id, current_user)
