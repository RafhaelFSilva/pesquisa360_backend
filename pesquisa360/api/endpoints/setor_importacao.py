"""Importacao administrativa de Setores: validar e importar em lote.

Duas rotas, um motor. `/validar` nunca escreve; `/importacao` reprocessa os
MESMOS arquivos e persiste pelo caminho oficial de criacao de Setor.

O modulo e separado de `projetos.py` de proposito: aquele arquivo ja passa de
900 linhas, e importacao tem ciclo de vida proprio (upload, temporarios,
limites).
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from pesquisa360 import crud, schemas
from pesquisa360.core.dependencies import get_db, require_manager_or_superadmin
from pesquisa360.db import models
from pesquisa360.services import setor_import_admin as import_service
from pesquisa360.services import setor_territorio

router = APIRouter()

STATUS_IMPORTADO = "IMPORTADO"
STATUS_ERRO = "ERRO"


class ItemImportacao(BaseModel):
    """Parametrizacao de UM Setor do lote.

    Cada item e independente: nome, meta, tolerancia, finalidade, agentes e
    composicao territorial nao tem valor global obrigatorio.
    """

    client_id: str
    arquivo_index: int = Field(ge=0)
    nome: str
    meta: int
    tolerancia_metros: int = 50
    finalidade: schemas.FinalidadeSetor = schemas.FinalidadeSetor.OPERACAO
    agente_ids: list[int] = Field(default_factory=list)
    territorio_eleitoral_ids: list[int] = Field(default_factory=list)
    unir_features: bool = False

    @field_validator("nome")
    @classmethod
    def validar_nome(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Nome do setor e obrigatorio.")
        return value.strip()

    @field_validator("meta")
    @classmethod
    def validar_meta(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Meta deve ser um inteiro positivo.")
        return value

    @field_validator("tolerancia_metros")
    @classmethod
    def validar_tolerancia(cls, value: int) -> int:
        if value < 0:
            raise ValueError("Tolerancia deve ser maior ou igual a zero.")
        return value

    @field_validator("agente_ids", "territorio_eleitoral_ids")
    @classmethod
    def validar_ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("IDs devem ser positivos.")
        if len(value) != len(set(value)):
            raise ValueError("IDs nao podem repetir.")
        return value


def _checar_acesso(db: Session, projeto_id: int, pesquisa_id: int, current_user):
    """Cadeia oficial de tenant. Nenhum company_id vem do cliente."""
    projeto = crud.get_projeto(db=db, projeto_id=projeto_id, current_user=current_user)
    if not projeto:
        raise HTTPException(status_code=404, detail="Projeto nao encontrado.")

    pesquisa = (
        db.query(models.Pesquisa)
        .filter(
            models.Pesquisa.id == pesquisa_id,
            models.Pesquisa.projeto_id == projeto_id,
        )
        .first()
    )
    if not pesquisa:
        raise HTTPException(status_code=404, detail="Pesquisa nao encontrada.")
    return projeto, pesquisa


async def _ler_arquivos(arquivos: list[UploadFile]) -> list[tuple[str, bytes]]:
    lidos: list[tuple[str, bytes]] = []
    for arquivo in arquivos:
        conteudo = await arquivo.read()
        lidos.append((arquivo.filename or "arquivo", conteudo))
    import_service.validar_limites_do_lote([len(c) for _, c in lidos])
    return lidos


@router.post("/projetos/{projeto_id}/pesquisas/{pesquisa_id}/setores/importacao/validar")
async def validar_importacao_setores(
    projeto_id: int,
    pesquisa_id: int,
    arquivos: Annotated[list[UploadFile], File()],
    unir_features: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_manager_or_superadmin),
):
    """Analisa os pacotes e devolve o preview. NAO persiste nada.

    `unir_features` chega como JSON (`{"client-id": true}`) porque a decisao de
    unir feicoes e por item, nao do lote.
    """
    _checar_acesso(db, projeto_id, pesquisa_id, current_user)

    try:
        uniao_por_item: dict[str, bool] = json.loads(unir_features) if unir_features else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Parametro unir_features invalido.")

    lidos = await _ler_arquivos(arquivos)
    base = import_service.obter_base_opcional(db, projeto_id, current_user)

    itens: list[dict[str, Any]] = []
    with import_service.RaizTemporaria() as raiz:
        for indice, (nome, conteudo) in enumerate(lidos):
            client_id = nome or f"arquivo-{indice}"
            analise = import_service.analisar_item(
                db,
                client_id=client_id,
                nome_arquivo=nome,
                conteudo=conteudo,
                raiz=raiz,
                unir_features=bool(uniao_por_item.get(client_id, False)),
                base=base,
            )
            itens.append(analise.to_dict())

    return {
        "base_eleitoral": {"id": base.id, "nome": base.nome} if base else None,
        "limites": {
            "max_itens": import_service.MAX_ITENS_POR_LOTE,
            "max_bytes_item": import_service.MAX_TAMANHO_POR_ITEM,
            "max_bytes_total": import_service.MAX_TAMANHO_TOTAL,
        },
        "itens": itens,
    }


def _validar_agentes(db: Session, agente_ids: list[int], current_user) -> None:
    if agente_ids:
        # Mesma regra do CRUD de Setor: agente ativo, do tenant, com perfil de
        # agente. 404 para id de outro tenant, sem revelar existencia.
        crud.validar_agentes_setor(db, agente_ids, current_user)


def _validar_territorios(
    db: Session, projeto_id: int, territorio_ids: list[int], current_user
) -> None:
    """Territorios validos ANTES de criar qualquer Setor.

    Reaproveita a regra oficial: base principal do projeto e tipo aceito. Sem
    isso, um id adulterado no browser so falharia depois do Setor existir.
    """
    if not territorio_ids:
        return
    base = import_service.obter_base_opcional(db, projeto_id, current_user)
    if base is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="O projeto nao possui Base Eleitoral principal vinculada.",
        )
    encontrados = (
        db.query(models.TerritorioEleitoral.id)
        .filter(
            models.TerritorioEleitoral.id.in_(territorio_ids),
            models.TerritorioEleitoral.base_eleitoral_id == base.id,
            models.TerritorioEleitoral.tipo == setor_territorio.TIPO_TERRITORIO_ACEITO,
        )
        .all()
    )
    if len(encontrados) != len(set(territorio_ids)):
        # Base errada, tipo errado, tenant errado e inexistente sao
        # indistinguiveis de proposito.
        raise HTTPException(status_code=404, detail="Territorio eleitoral nao encontrado.")


@router.post("/projetos/{projeto_id}/pesquisas/{pesquisa_id}/setores/importacao")
async def importar_setores(
    projeto_id: int,
    pesquisa_id: int,
    arquivos: Annotated[list[UploadFile], File()],
    itens: Annotated[str, Form()],
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_manager_or_superadmin),
):
    """Reprocessa os arquivos e cria os Setores parametrizados.

    O arquivo e a fonte: o GeoJSON que o browser recebeu no preview NAO e
    aceito como entrada de geometria. Todo item e revalidado aqui.
    """
    _checar_acesso(db, projeto_id, pesquisa_id, current_user)

    try:
        crus = json.loads(itens)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Parametro itens invalido.")
    if not isinstance(crus, list) or not crus:
        raise HTTPException(status_code=400, detail="Envie ao menos um item.")

    try:
        parametros = [ItemImportacao.model_validate(item) for item in crus]
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Item invalido: {exc}") from exc

    lidos = await _ler_arquivos(arquivos)
    if len(parametros) > len(lidos):
        raise HTTPException(status_code=400, detail="Ha itens sem arquivo correspondente.")
    for item in parametros:
        if item.arquivo_index >= len(lidos):
            raise HTTPException(
                status_code=400,
                detail=f"Item {item.client_id} referencia arquivo inexistente.",
            )

    # --- Validacao PREVIA de tudo o que da para conferir sem escrever --------
    # Reduz o partial success previsivel: agente/territorio/tenant invalido
    # derruba o lote ANTES de o primeiro Setor existir.
    for item in parametros:
        _validar_agentes(db, item.agente_ids, current_user)
        _validar_territorios(db, projeto_id, item.territorio_eleitoral_ids, current_user)

    resultados: list[dict[str, Any]] = []
    importados = 0

    with import_service.RaizTemporaria() as raiz:
        for item in parametros:
            nome_arquivo, conteudo = lidos[item.arquivo_index]
            try:
                shp = import_service.materializar_item(nome_arquivo, conteudo, raiz)
                geometria = import_service.load_sector_geometry(
                    shp, merge_features=item.unir_features
                )
            except import_service.ShapefileImportError as exc:
                resultados.append(
                    {"client_id": item.client_id, "status": STATUS_ERRO, "detail": str(exc)}
                )
                continue
            except Exception:  # noqa: BLE001
                resultados.append(
                    {
                        "client_id": item.client_id,
                        "status": STATUS_ERRO,
                        "detail": "Nao foi possivel processar o arquivo enviado.",
                    }
                )
                continue

            try:
                setor_in = schemas.SetorCreate(
                    nome=item.nome,
                    meta=item.meta,
                    tolerancia=item.tolerancia_metros,
                    finalidade=item.finalidade,
                    agente_ids=item.agente_ids,
                    geometria_coords=geometria.coordinates_lat_lon,
                )
                db_setor = crud.create_setor(
                    db=db,
                    setor_in=setor_in,
                    pesquisa_id=pesquisa_id,
                    current_user=current_user,
                )
            except HTTPException as exc:
                resultados.append(
                    {
                        "client_id": item.client_id,
                        "status": STATUS_ERRO,
                        "detail": str(exc.detail),
                    }
                )
                continue
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                resultados.append(
                    {"client_id": item.client_id, "status": STATUS_ERRO, "detail": str(exc)}
                )
                continue

            territorios_aplicados: list[int] = []
            aviso: Optional[str] = None
            if item.territorio_eleitoral_ids:
                try:
                    # Caminho OFICIAL de composicao, o mesmo do dialogo de
                    # composicao eleitoral. Nenhum INSERT ad hoc.
                    aplicados = setor_territorio.definir_territorios(
                        db=db,
                        projeto_id=projeto_id,
                        pesquisa_id=pesquisa_id,
                        setor_id=db_setor.id,
                        territorio_ids=item.territorio_eleitoral_ids,
                        current_user=current_user,
                    )
                    territorios_aplicados = [t.id for t in aplicados]
                except HTTPException as exc:
                    # O Setor foi criado; a composicao nao. Nao apagar o Setor
                    # nem esconder a falha: o operador precisa saber que este
                    # item ficou sem composicao.
                    aviso = f"Setor criado sem composicao territorial: {exc.detail}"

            importados += 1
            resultado = {
                "client_id": item.client_id,
                "status": STATUS_IMPORTADO,
                "setor_id": db_setor.id,
                "nome": db_setor.nome,
                "territorio_eleitoral_ids": territorios_aplicados,
            }
            if aviso:
                resultado["warning"] = aviso
            resultados.append(resultado)

    return {
        "total": len(parametros),
        "importados": importados,
        "falhas": len(parametros) - importados,
        "itens": resultados,
    }


@router.get("/projetos/{projeto_id}/pesquisas/{pesquisa_id}/setores/importacao/territorios")
def listar_territorios_disponiveis(
    projeto_id: int,
    pesquisa_id: int,
    busca: str = "",
    limite: int = 500,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_manager_or_superadmin),
):
    """Territorios selecionaveis manualmente na preparacao.

    Existe porque a sugestao por geometria depende de os territorios da Base
    terem poligono -- e quando nao tem, a composicao ainda precisa ser possivel
    pela selecao humana.
    """
    _checar_acesso(db, projeto_id, pesquisa_id, current_user)
    base = import_service.obter_base_opcional(db, projeto_id, current_user)
    if base is None:
        return {"base_eleitoral": None, "territorios": []}

    municipio = models.TerritorioEleitoral.__table__.alias("municipio")
    query = (
        db.query(
            models.TerritorioEleitoral.id,
            models.TerritorioEleitoral.nome,
            models.TerritorioEleitoral.municipio_id,
            municipio.c.nome.label("municipio_nome"),
        )
        .outerjoin(municipio, municipio.c.id == models.TerritorioEleitoral.municipio_id)
        .filter(
            models.TerritorioEleitoral.base_eleitoral_id == base.id,
            models.TerritorioEleitoral.tipo == setor_territorio.TIPO_TERRITORIO_ACEITO,
        )
    )
    termo = busca.strip()
    if termo:
        query = query.filter(models.TerritorioEleitoral.nome.ilike(f"%{termo}%"))

    linhas = query.order_by(models.TerritorioEleitoral.nome).limit(max(1, min(limite, 1000))).all()
    return {
        "base_eleitoral": {"id": base.id, "nome": base.nome},
        "territorios": [
            {
                "id": linha.id,
                "nome": linha.nome,
                "municipio_id": linha.municipio_id,
                "municipio_nome": linha.municipio_nome,
            }
            for linha in linhas
        ],
    }
