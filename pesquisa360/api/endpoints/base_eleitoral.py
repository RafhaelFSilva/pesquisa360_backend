"""Rotas de leitura e validacao da Base Eleitoral (Fase 3A).

Nao existe upload da fonte real aqui: sem o arquivo eleitoral em maos, expor um
endpoint de importacao generico seria criar uma API de JSON livre para contornar
a ausencia do adapter. O motor de importacao permanece interno ate a Fase 3B.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from pesquisa360 import schemas
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.db import models
from pesquisa360.services import base_eleitoral as service
from pesquisa360.core.rbac import Permissao, require_permissao

router = APIRouter()


def _eh_oficial(base: models.BaseEleitoral) -> bool:
    return base.company_id is None


def _base_para_item(base: models.BaseEleitoral) -> dict:
    return {
        "id": base.id,
        "nome": base.nome,
        "ano": base.ano,
        "uf": base.uf,
        "fonte": base.fonte,
        "versao": base.versao,
        "data_referencia": base.data_referencia,
        "status": base.status,
        "eh_oficial": _eh_oficial(base),
    }


def _territorio_para_item(territorio: models.TerritorioEleitoral) -> dict:
    # Geometria PostGIS nunca sai crua (ADR-004): a listagem e alfanumerica e
    # apenas sinaliza a presenca de geometria.
    return {
        "id": territorio.id,
        "base_eleitoral_id": territorio.base_eleitoral_id,
        "tipo": territorio.tipo,
        "codigo": territorio.codigo,
        "nome": territorio.nome,
        "nome_normalizado": territorio.nome_normalizado,
        "parent_id": territorio.parent_id,
        "municipio_id": territorio.municipio_id,
        "zona_eleitoral": territorio.zona_eleitoral,
        "numero_secao": territorio.numero_secao,
        "eleitorado_apto": territorio.eleitorado_apto,
        "eleitorado_apto_origem": territorio.eleitorado_apto_origem,
        "eleitorado_apto_divergente": territorio.eleitorado_apto_divergente,
        "status_validacao": territorio.status_validacao,
        "possui_geometria": territorio.geometria is not None,
    }


def _contar_territorios(db: Session, base_id: int) -> int:
    return (
        db.query(models.TerritorioEleitoral)
        .filter(models.TerritorioEleitoral.base_eleitoral_id == base_id)
        .count()
    )


def _auditoria_data_referencia(db: Session, base_id: int) -> Optional[dict]:
    """Traduz o bloco persistido para o schema explicito.

    Nada de devolver `metadados` cru: o cliente recebe apenas os campos
    previstos em AuditoriaDataReferencia.
    """
    bloco = service.obter_auditoria_data_referencia(db, base_id)
    if not bloco:
        return None
    return {
        "origem": bloco.get("data_referencia_origem"),
        "convencional": bool(bloco.get("data_referencia_convencional")),
        "data_fonte_declarada": bloco.get("data_fonte_declarada"),
        "motivo": bloco.get("motivo"),
        "pdf_creation_date": bloco.get("pdf_creation_date"),
    }


def _detalhe_completo(db: Session, base: models.BaseEleitoral) -> dict:
    """Detalhe da base com os agregados que o Workspace precisa ler."""
    detalhe = _base_para_item(base)
    raiz = service.obter_raiz_estado(db, base.id)
    operacional = raiz.eleitorado_apto if raiz is not None else None
    declarado = raiz.eleitorado_apto_origem if raiz is not None else None
    diferenca = (
        declarado - operacional
        if operacional is not None and declarado is not None
        else None
    )
    # A visibilidade da base ja foi validada por quem chama este helper.
    importacoes = (
        db.query(models.ImportacaoBaseEleitoral)
        .filter(models.ImportacaoBaseEleitoral.base_eleitoral_id == base.id)
        .order_by(models.ImportacaoBaseEleitoral.id)
        .all()
    )
    # Sem unicidade garantida: so preenche importacao_origem quando ha
    # exatamente um lote. Com mais de um, a UI consulta /importacoes.
    origem = importacoes[0] if len(importacoes) == 1 else None

    detalhe.update(
        {
            "fonte_referencia": base.fonte_referencia,
            "substituida_por_id": base.substituida_por_id,
            "comparecimento_estimado": (
                float(base.comparecimento_estimado)
                if base.comparecimento_estimado is not None
                else None
            ),
            "percentual_votos_validos": (
                float(base.percentual_votos_validos)
                if base.percentual_votos_validos is not None
                else None
            ),
            "criado_por_id": base.criado_por_id,
            "criado_em": base.criado_em,
            "atualizado_em": base.atualizado_em,
            "total_territorios": _contar_territorios(db, base.id),
            "total_em_conferencia": service.contar_territorios_em_conferencia(db, base.id),
            "totais_por_tipo": service.contar_territorios_por_tipo(db, base.id),
            "eleitorado_operacional": operacional,
            "eleitorado_declarado": declarado,
            "diferenca_eleitorado": diferenca,
            "auditoria_data_referencia": _auditoria_data_referencia(db, base.id),
            "total_importacoes": len(importacoes),
            "importacao_origem": origem,
        }
    )
    return detalhe


# --- Leitura ------------------------------------------------------------------


@router.get("/base-eleitoral/", response_model=List[schemas.BaseEleitoralListItem], dependencies=[Depends(require_permissao(Permissao.BASE_ELEITORAL_VER))])
def listar_bases_eleitorais(
    *,
    db: Session = Depends(get_db),
    status_validacao: Optional[schemas.StatusBaseEleitoral] = None,
    uf: Optional[str] = None,
    ano: Optional[int] = None,
    current_user: models.Usuario = Depends(get_current_user),
):
    """Bases oficiais mais as privadas do tenant do usuario autenticado."""
    bases = service.listar_bases_eleitorais_visiveis(
        db,
        current_user,
        status_filtro=status_validacao.value if status_validacao else None,
        uf=uf,
        ano=ano,
    )
    return [_base_para_item(base) for base in bases]


@router.get(
    "/base-eleitoral/{base_id}", response_model=schemas.BaseEleitoralDetalheCompleto, dependencies=[Depends(require_permissao(Permissao.BASE_ELEITORAL_VER))])
def obter_base_eleitoral(
    *,
    db: Session = Depends(get_db),
    base_id: int,
    current_user: models.Usuario = Depends(get_current_user),
):
    base = service.obter_base_eleitoral_visivel(db, base_id, current_user)
    return _detalhe_completo(db, base)


@router.get(
    "/base-eleitoral/{base_id}/territorios",
    response_model=schemas.TerritorioEleitoralPage, dependencies=[Depends(require_permissao(Permissao.BASE_ELEITORAL_VER))])
def listar_territorios_base_eleitoral(
    *,
    db: Session = Depends(get_db),
    base_id: int,
    tipo: Optional[schemas.TipoTerritorioEleitoral] = None,
    status_validacao: Optional[schemas.StatusBaseEleitoral] = None,
    municipio_id: Optional[int] = None,
    q: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: models.Usuario = Depends(get_current_user),
):
    filtros = {
        "tipo": tipo.value if tipo else None,
        "status_validacao": status_validacao.value if status_validacao else None,
        "municipio_id": municipio_id,
        "q": q,
    }
    territorios = service.listar_territorios(
        db, base_id, current_user, limit=limit, offset=offset, **filtros
    )
    # `total` reflete os mesmos filtros, antes de limit/offset.
    total = service.contar_territorios(db, base_id, current_user, **filtros)
    return {
        "items": [_territorio_para_item(territorio) for territorio in territorios],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/base-eleitoral/{base_id}/importacoes",
    response_model=List[schemas.ImportacaoBaseEleitoralResumo], dependencies=[Depends(require_permissao(Permissao.BASE_ELEITORAL_VER))])
def listar_importacoes_base_eleitoral(
    *,
    db: Session = Depends(get_db),
    base_id: int,
    current_user: models.Usuario = Depends(get_current_user),
):
    """Lotes de importacao da base, em ordem de execucao.

    Nao ha constraint garantindo um unico lote por versao, entao o backend nao
    elege uma origem arbitrariamente. Quando ha exatamente um lote, o detalhe da
    base ja traz `importacao_origem`; com mais de um, a origem e escolhida aqui,
    explicitamente.
    """
    return service.listar_importacoes(db, base_id, current_user)


@router.get(
    "/projetos/{projeto_id}/base-eleitoral",
    response_model=schemas.ProjetoBaseEleitoralAtualResponse, dependencies=[Depends(require_permissao(Permissao.BASE_ELEITORAL_VER))])
def obter_base_eleitoral_do_projeto(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    current_user: models.Usuario = Depends(get_current_user),
):
    """Base eleitoral principal do projeto.

    Projeto sem base vinculada responde 200 com `base=null`: ausencia de vinculo
    e estado funcional normal. 404 continua significando projeto inexistente ou
    invisivel para o tenant.
    """
    base = service.obter_base_principal_projeto_opcional(db, projeto_id, current_user)
    if base is None:
        return {"projeto_id": projeto_id, "principal": False, "base": None}
    return {
        "projeto_id": projeto_id,
        "principal": True,
        "base": _detalhe_completo(db, base),
    }


@router.get(
    "/projetos/{projeto_id}/bases-eleitorais-disponiveis",
    response_model=List[schemas.BaseEleitoralListItem], dependencies=[Depends(require_permissao(Permissao.BASE_ELEITORAL_VER))])
def listar_bases_eleitorais_disponiveis_para_projeto(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    current_user: models.Usuario = Depends(get_current_user),
):
    """Candidatas a vinculo para ESTE Projeto (ADR-034).

    Autoriza o Projeto pela ACL (404 se invisivel) e lista bases oficiais mais
    as privadas do tenant DO PROJETO -- nunca da empresa principal do usuario.
    E o que o modal "Vincular Base Eleitoral" consome; `GET /base-eleitoral/`
    continua respondendo "o que o usuario ve", que e outra pergunta.
    """
    bases = service.listar_bases_disponiveis_para_projeto(db, projeto_id, current_user)
    return [_base_para_item(base) for base in bases]


@router.get(
    "/base-eleitoral/{base_id}/divergencias",
    response_model=List[schemas.DivergenciaBaseEleitoralResponse], dependencies=[Depends(require_permissao(Permissao.BASE_ELEITORAL_VER))])
def listar_divergencias_base_eleitoral(
    *,
    db: Session = Depends(get_db),
    base_id: int,
    current_user: models.Usuario = Depends(get_current_user),
):
    return service.listar_divergencias(db, base_id, current_user)


# --- Acoes --------------------------------------------------------------------


@router.post(
    "/projetos/{projeto_id}/base-eleitoral/{base_id}/vincular",
    response_model=schemas.VinculoProjetoBaseResponse, dependencies=[Depends(require_permissao(Permissao.BASE_ELEITORAL_GERENCIAR))])
def vincular_base_ao_projeto(
    *,
    db: Session = Depends(get_db),
    projeto_id: int,
    base_id: int,
    payload: Optional[schemas.VincularBaseProjetoRequest] = None,
    current_user: models.Usuario = Depends(get_current_user),
):
    """Fixa a versao eleitoral usada pela campanha.

    ACL decide se o usuario pode agir no Projeto; o tenant do PROJETO decide
    se a Base serve a ele (ADR-034). Base privada de outro tenant: 404.
    """
    principal = payload.principal if payload is not None else True
    return service.vincular_base_eleitoral_ao_projeto(
        db, projeto_id, base_id, current_user, principal=principal
    )


@router.post("/base-eleitoral/{base_id}/validar", response_model=schemas.ResultadoValidacaoBase, dependencies=[Depends(require_permissao(Permissao.BASE_ELEITORAL_GERENCIAR))])
def validar_base_eleitoral(
    *,
    db: Session = Depends(get_db),
    base_id: int,
    current_user: models.Usuario = Depends(get_current_user),
):
    """Validacao humana explicita: nao existe promocao automatica."""
    base = service.validar_base_eleitoral(db, base_id, current_user)
    return {
        "id": base.id,
        "status": base.status,
        "total_territorios": _contar_territorios(db, base.id),
        "total_em_conferencia": service.contar_territorios_em_conferencia(db, base.id),
    }


@router.patch(
    "/base-eleitoral/{base_id}/parametros-projecao",
    response_model=schemas.BaseEleitoralDetalheCompleto, dependencies=[Depends(require_permissao(Permissao.BASE_ELEITORAL_GERENCIAR))])
def definir_parametros_projecao(
    *,
    db: Session = Depends(get_db),
    base_id: int,
    payload: schemas.ParametrosProjecaoRequest,
    current_user: models.Usuario = Depends(get_current_user),
):
    """Configura comparecimento estimado e percentual de votos validos.

    Decisao metodologica humana: o sistema nunca preenche estes valores por
    convencao. Campo omitido no corpo mantem o valor atual; campo enviado como
    null limpa a configuracao.
    """
    informados = payload.model_dump(exclude_unset=True)
    base = service.definir_parametros_projecao(
        db,
        base_id,
        current_user,
        **{
            campo: informados[campo]
            for campo in ("comparecimento_estimado", "percentual_votos_validos")
            if campo in informados
        },
    )
    return _detalhe_completo(db, base)


@router.post(
    "/base-eleitoral/territorios/{territorio_id}/resolver-divergencia",
    response_model=schemas.TerritorioEleitoralListItem, dependencies=[Depends(require_permissao(Permissao.BASE_ELEITORAL_GERENCIAR))])
def resolver_divergencia(
    *,
    db: Session = Depends(get_db),
    territorio_id: int,
    payload: schemas.ResolverDivergenciaRequest,
    current_user: models.Usuario = Depends(get_current_user),
):
    territorio = service.resolver_divergencia_territorio(
        db,
        territorio_id,
        payload.valor_final,
        payload.justificativa,
        current_user,
    )
    return _territorio_para_item(territorio)
