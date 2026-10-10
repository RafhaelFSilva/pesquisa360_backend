"""Rotas da Apuracao Eleitoral: leituras analiticas do TSE e paineis do tenant.

Dois grupos com regras de escopo diferentes:

* `/apuracao/tse/...`  -- dados oficiais do TSE, GLOBAIS (ADR-076). Exigem
  usuario autenticado com INTELIGENCIA_VER, mas nao filtram por empresa.
* `/apuracao/paineis`  -- configuracao do TENANT (ADR-085). O `company_id`
  vem do usuario; painel de outra empresa responde 404.

Somente leitura da base ja ingerida: nenhuma rota consulta o TSE.
"""

from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from pesquisa360 import schemas_apuracao as contratos
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.core.rbac import Permissao, require_permissao
from pesquisa360.db import models
from pesquisa360.services import apuracao_paineis as paineis
from pesquisa360.services.tse.analytics import TseAnalytics, TseNotFound

router = APIRouter(prefix="/apuracao", tags=["Apuracao Eleitoral"])

LER = [Depends(require_permissao(Permissao.INTELIGENCIA_VER))]
ESCREVER = [Depends(require_permissao(Permissao.RELATORIO_CONFIGURAR))]

OrigemQuery = Optional[Literal["OFICIAL", "SIMULADO"]]


def _consultar(funcao, *args, **kwargs):
    try:
        return funcao(*args, **kwargs)
    except TseNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=str(exc)) from exc


# ------------------------------------------------------------ dados do TSE
@router.get("/tse/eleicoes", dependencies=LER)
def listar_eleicoes(origem: OrigemQuery = None, db: Session = Depends(get_db)):
    """Eleicoes com resultado ingerido, com as UFs e os cargos disponiveis."""
    return TseAnalytics(db).listar_eleicoes(origem)


@router.get("/tse/eleicoes/{eleicao_id}/resumo", dependencies=LER)
def resumo_da_eleicao(
    eleicao_id: int, uf: str = Query(min_length=2, max_length=2),
    municipio: Optional[str] = Query(default=None, pattern=r"^\d{5}$"),
    zona: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    secao: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    local_votacao: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    origem: OrigemQuery = None, db: Session = Depends(get_db),
):
    """Totalizacao por cargo na UF, no municipio ou na zona (EA20) ou na secao (BU)."""
    _hierarquia(municipio, zona, secao, local_votacao)
    return _consultar(TseAnalytics(db).resumo, eleicao_id, uf, origem, municipio, zona, secao, local_votacao)


def _hierarquia(municipio: Optional[str], zona: Optional[str], secao: Optional[str],
                local_votacao: Optional[str] = None) -> None:
    """Secao exige zona; zona exige municipio. Recusa explicita: nunca outro recorte."""
    if local_votacao and not zona:
        raise HTTPException(status_code=422, detail="Local de votação exige zona.")
    if secao and not zona:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Seção exige zona.")
    if zona and not municipio:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Zona exige município.")


@router.get("/tse/eleicoes/{eleicao_id}/territorio", dependencies=LER)
def opcoes_territoriais(
    eleicao_id: int, uf: str = Query(min_length=2, max_length=2),
    municipio: Optional[str] = Query(default=None, pattern=r"^\d{5}$"),
    zona: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    local_votacao: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    origem: OrigemQuery = None, db: Session = Depends(get_db),
):
    """Municipios da UF; com `municipio`, suas zonas; com `zona`, suas secoes e a situacao do BU."""
    _hierarquia(municipio, zona, None, local_votacao)
    return _consultar(TseAnalytics(db).opcoes_territoriais, eleicao_id, uf, municipio, zona,
                      origem, local_votacao)


@router.get("/tse/eleicoes/{eleicao_id}/cargos/{cargo_codigo}", dependencies=LER)
def resultado_por_cargo(
    eleicao_id: int, cargo_codigo: str, uf: str = Query(min_length=2, max_length=2),
    municipio: Optional[str] = Query(default=None, pattern=r"^\d{5}$"),
    zona: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    secao: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    local_votacao: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    origem: OrigemQuery = None, limite: Optional[int] = Query(default=None, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    """Candidatos ordenados por votos. `situacao`/`eleito` vem do TSE, sem inferencia."""
    _hierarquia(municipio, zona, secao, local_votacao)
    return _consultar(TseAnalytics(db).resultado_cargo, eleicao_id, cargo_codigo, uf,
                      municipio, zona, origem, limite, secao, local_votacao)


@router.get("/tse/eleicoes/{eleicao_id}/cargos/{cargo_codigo}/nominatas", dependencies=LER)
def nominatas_do_cargo(
    eleicao_id: int, cargo_codigo: str, uf: str = Query(min_length=2, max_length=2),
    municipio: Optional[str] = Query(default=None, pattern=r"^\d{5}$"),
    zona: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    secao: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    local_votacao: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    origem: OrigemQuery = None, db: Session = Depends(get_db),
):
    """Partido/federacao -> candidatos. A posicao e ordenacao factual por votos."""
    _hierarquia(municipio, zona, secao, local_votacao)
    return _consultar(TseAnalytics(db).nominatas, eleicao_id, cargo_codigo, uf, municipio,
                      zona, origem, secao, local_votacao)


@router.get("/tse/eleicoes/{eleicao_id}/cargos/{cargo_codigo}/distribuicao", dependencies=LER)
def distribuicao_territorial(
    eleicao_id: int, cargo_codigo: str, uf: str = Query(min_length=2, max_length=2),
    candidato: List[str] = Query(default=[]),
    partido: List[str] = Query(default=[]),
    federacao: List[str] = Query(default=[]),
    municipio: Optional[str] = Query(default=None, pattern=r"^\d{5}$"),
    zona: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    secao: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    local_votacao: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    nivel: Optional[Literal['locais_votacao', 'secoes']] = None,
    origem: OrigemQuery = None, db: Session = Depends(get_db),
):
    """Distribuicao de candidatos/nominatas: por municipio (UF), zona (municipio) ou secao (zona).

    Uma requisicao para todos os itens e todas as partes. `candidato`, `partido` e
    `federacao` sao repetiveis. Com `secao`, o recorte e a propria urna (nivel minimo).
    """
    _hierarquia(municipio, zona, secao, local_votacao)
    return _consultar(TseAnalytics(db).distribuicao, eleicao_id, cargo_codigo, uf, candidato,
                      partido, federacao, municipio, zona, origem, secao, local_votacao, nivel)


@router.get("/tse/eleicoes/{eleicao_id}/cargos/{cargo_codigo}/conferencia-bu", dependencies=LER)
def conferencia_bu(
    eleicao_id: int, cargo_codigo: str, uf: str = Query(min_length=2, max_length=2),
    municipio: str = Query(pattern=r"^\d{5}$"),
    zona: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    origem: OrigemQuery = None, db: Session = Depends(get_db),
):
    """Conferencia BU x EA20 por zona (somente leitura; nenhum valor e alterado)."""
    return _consultar(TseAnalytics(db).conferencia_bu, eleicao_id, cargo_codigo, uf, municipio,
                      zona, origem)


@router.get("/tse/candidatos/{sqcand}", dependencies=LER)
def candidato(
    sqcand: str, eleicao_id: int,
    municipio: Optional[str] = Query(default=None, pattern=r"^\d{5}$"),
    zona: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    secao: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    local_votacao: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    origem: OrigemQuery = None, db: Session = Depends(get_db),
):
    """Candidato e seu resultado oficial na UF ou, com filtros, no municipio/zona/secao."""
    _hierarquia(municipio, zona, secao, local_votacao)
    return _consultar(TseAnalytics(db).candidato, sqcand, eleicao_id, origem, municipio, zona,
                      secao, local_votacao)


@router.get("/tse/candidatos/{sqcand}/territorio", dependencies=LER)
def territorio_do_candidato(
    sqcand: str, eleicao_id: int, group_by: Literal["municipio", "zona"] = "municipio",
    municipio: Optional[str] = Query(default=None, pattern=r"^\d{5}$"),
    origem: OrigemQuery = None, db: Session = Depends(get_db),
):
    """Votos do candidato por municipio ou zona. Secao: indisponivel (depende do BU)."""
    return _consultar(TseAnalytics(db).territorio, sqcand, eleicao_id, group_by, municipio,
                      origem)


@router.get("/tse/candidatos/{sqcand}/evolucao", dependencies=LER)
def evolucao_do_candidato(
    sqcand: str, eleicao_id: int,
    municipio: Optional[str] = Query(default=None, pattern=r"^\d{5}$"),
    zona: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    origem: OrigemQuery = None, db: Session = Depends(get_db),
):
    return _consultar(TseAnalytics(db).evolucao, sqcand, eleicao_id, municipio, zona, origem)


# ------------------------------------------------------- paineis do tenant
@router.get("/paineis", response_model=List[contratos.PainelResumo], dependencies=LER)
def listar_paineis(db: Session = Depends(get_db),
                   current_user: models.Usuario = Depends(get_current_user)):
    return [paineis.resumo(db, p) for p in paineis.listar(db, current_user)]


@router.post("/paineis", response_model=contratos.PainelDetalhe, status_code=201,
             dependencies=ESCREVER)
def criar_painel(dados: contratos.PainelIn, db: Session = Depends(get_db),
                 current_user: models.Usuario = Depends(get_current_user)):
    return paineis.detalhe(db, paineis.criar(db, dados, current_user))


@router.get("/paineis/{painel_id}", response_model=contratos.PainelDetalhe, dependencies=LER)
def obter_painel(painel_id: int, db: Session = Depends(get_db),
                 current_user: models.Usuario = Depends(get_current_user)):
    return paineis.detalhe(db, paineis.obter(db, painel_id, current_user))


@router.put("/paineis/{painel_id}", response_model=contratos.PainelDetalhe,
            dependencies=ESCREVER)
def atualizar_painel(painel_id: int, dados: contratos.PainelIn, db: Session = Depends(get_db),
                     current_user: models.Usuario = Depends(get_current_user)):
    return paineis.detalhe(db, paineis.atualizar(db, painel_id, dados, current_user))


@router.delete("/paineis/{painel_id}", status_code=204, dependencies=ESCREVER)
def excluir_painel(painel_id: int, db: Session = Depends(get_db),
                   current_user: models.Usuario = Depends(get_current_user)):
    paineis.excluir(db, painel_id, current_user)
    return Response(status_code=204)
