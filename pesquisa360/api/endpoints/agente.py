from typing import List, Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, or_
import json

from pesquisa360 import crud, schemas
from pesquisa360.db import models
from pesquisa360.core.dependencies import get_db, get_current_user
from pesquisa360.core.rbac import Permissao, require_permissao

router = APIRouter()

def ordenar_perguntas_para_sync(projetos):
    for projeto in projetos or []:
        for pesquisa in getattr(projeto, "pesquisas", []) or []:
            perguntas = getattr(pesquisa, "perguntas", None)
            if perguntas:
                perguntas.sort(key=lambda pergunta: (pergunta.ordem, pergunta.id))
    return projetos

@router.get("/pesquisas/", response_model=List[schemas.ProjetoSync], dependencies=[Depends(require_permissao(Permissao.MISSAO_SINCRONIZAR))])
def read_pesquisas_para_sincronizar(
    *,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """Projetos e pesquisas para o App Mobile sincronizar.

    ADR-024: o recorte é a ACL do agente, não a empresa do cadastro dele. Um
    agente que atende dois clientes recebe as missões de ambos em um único sync,
    e cada Projeto/Pesquisa preserva a própria identidade de tenant.
    """
    # Você precisará garantir que esta função no CRUD receba o current_user
    # Se ela não existir no CRUD novo, use get_projetos comum.
    try:
        projetos = crud.get_projetos_em_campo(db=db, current_user=current_user)
    except AttributeError:
        # Fallback caso você ainda não tenha criado 'get_projetos_em_campo' no CRUD novo
        projetos = crud.get_projetos(db=db, current_user=current_user)
        
    return ordenar_perguntas_para_sync(projetos)

@router.get("/missao/{pesquisa_id}", dependencies=[Depends(require_permissao(Permissao.MISSAO_SINCRONIZAR))])
def get_missao_agente(
    *,
    db: Session = Depends(get_db),
    pesquisa_id: int,
    current_user: models.Usuario = Depends(get_current_user)
):
    """
    Retorna a missão (setor/cota) específica para o agente logado.
    """
    # 1. SEGURANÇA: Verifica se a pesquisa pertence à empresa do agente
    pesquisa = crud.get_pesquisa(db=db, pesquisa_id=pesquisa_id, current_user=current_user)
    if not pesquisa:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada ou acesso negado.")

    # 2. Busca o setor do agente
    setores = (
        db.query(models.Setor)
        .outerjoin(
            models.SetorAgente,
            and_(
                models.SetorAgente.setor_id == models.Setor.id,
                models.SetorAgente.agente_id == current_user.id,
                models.SetorAgente.ativo.is_(True),
            ),
        )
        .filter(
            models.Setor.pesquisa_id == pesquisa_id,
            or_(
                models.SetorAgente.id.is_not(None),
                models.Setor.agente_id == current_user.id,
            ),
            models.Setor.finalidade.in_([
                schemas.FinalidadeSetor.OPERACAO.value,
                schemas.FinalidadeSetor.AMBOS.value,
            ]),
        )
        .distinct()
        .order_by(models.Setor.id.asc())
        .all()
    )

    if not setores:
        return {
            "tem_setor": False,
            "setor_id": None,
            "setor_nome": None,
            "meta": 0,
            "realizado": 0,
            "restante": 0,
            "excedente": 0,
            "percentual_atingimento": None,
            "cota_atingida": None,
            "tolerancia_metros": 0,
            "geometria": None,
            "setores": [],
            "possui_perguntas_territoriais": False,
        }

    # 3. Calcula o progresso coletivo por setor em uma unica query agrupada.
    progressos = crud.obter_progressos_setores(
        db,
        setores,
        pesquisa_id=pesquisa_id,
    )

    # FASE F. Municipio e perguntas aplicaveis de TODOS os setores em lote:
    # uma resolucao territorial e uma leitura do questionario, nao N+1.
    from pesquisa360.services import pergunta_territorio

    setor_ids = [setor.id for setor in setores]
    resolucoes = pergunta_territorio.resolver_municipios_setores(db, setor_ids)
    aplicaveis = pergunta_territorio.perguntas_aplicaveis_por_setor(db, pesquisa_id, setor_ids)
    possui_territoriais = pergunta_territorio.pesquisa_possui_perguntas_territoriais(
        db, pesquisa_id
    )

    # 4. Converte a geometria para GeoJSON
    setores_payload = []
    for setor in setores:
        geojson = None
        if setor.geometria is not None:
            cerca_str = db.query(func.ST_AsGeoJSON(setor.geometria)).scalar()
            if cerca_str:
                geojson = json.loads(cerca_str)
        progresso = progressos[setor.id]
        setores_payload.append({
            "id": setor.id,
            "nome": setor.nome,
            "meta": setor.meta,
            "realizado": progresso["realizado"],
            "restante": progresso["restante"],
            "excedente": progresso["excedente"],
            "percentual_atingimento": progresso["percentual_atingimento"],
            "cota_atingida": progresso["cota_atingida"],
            # PROMPT 04. Cota territorial operacional (aditivo). ENCERRADO
            # bloqueia NOVAS abordagens no Mobile; o snapshot leva o corte
            # (MAX id de coleta) para o aparelho somar so o que e novo.
            "status_cota": progresso["status_cota"],
            "limite_atencao_realizado": progresso["limite_atencao_realizado"],
            "snapshot_ate_coleta_id": progresso["snapshot_ate_coleta_id"],
            "snapshot_em": (
                progresso["snapshot_em"].isoformat()
                if progresso["snapshot_em"] is not None
                else None
            ),
            "agentes_atribuidos_total": progresso["agentes_atribuidos_total"],
            "tolerancia_metros": setor.tolerancia,
            "geometria": geojson,
            # Aditivos da FASE F. IDs, nao definicoes: o sync ja entrega as
            # perguntas; aqui vai so o recorte por setor.
            "territorio_status": resolucoes[setor.id].status,
            "municipio": (
                resolucoes[setor.id].municipio.to_dict()
                if resolucoes[setor.id].resolvido
                else None
            ),
            "pergunta_ids_aplicaveis": aplicaveis[setor.id],
        })

    primeiro_setor = setores[0]

    # PROMPT 05. Cota de perfil (ORIENTATIVA): so os municipios dos setores
    # deste agente. Nunca bloqueia; o Mobile mostra apenas sexo/faixa + nivel.
    from pesquisa360.services import cota_perfil

    perfil = cota_perfil.prioridades_para_missao(
        db,
        pesquisa_id,
        [
            resolucoes[setor.id].municipio.id
            for setor in setores
            if resolucoes[setor.id].resolvido
        ],
    )

    return {
        **perfil,
        "tem_setor": True,
        "setor_id": primeiro_setor.id,
        "setor_nome": primeiro_setor.nome,
        "meta": primeiro_setor.meta,
        "realizado": setores_payload[0]["realizado"],
        "restante": setores_payload[0]["restante"],
        "excedente": setores_payload[0]["excedente"],
        "percentual_atingimento": setores_payload[0]["percentual_atingimento"],
        "cota_atingida": setores_payload[0]["cota_atingida"],
        "status_cota": setores_payload[0]["status_cota"],
        "tolerancia_metros": primeiro_setor.tolerancia,
        "geometria": setores_payload[0]["geometria"],
        "setores": setores_payload,
        # Deixa o Mobile avisar o agente quando um setor nao resolve municipio
        # e isso de fato esconde perguntas -- sem precisar de coluna nova no
        # banco local para saber a aplicabilidade de cada pergunta.
        "possui_perguntas_territoriais": possui_territoriais,
    }
