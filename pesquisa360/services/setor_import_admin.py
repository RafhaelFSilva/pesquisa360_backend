"""Importacao administrativa de Setores: preparacao, preview e persistencia.

Este modulo NAO reimplementa nada de geoespacial. O motor oficial continua
sendo `setor_shapefile_import`; aqui existe apenas o que o CLI nao precisava:
receber upload, abrir pacote com seguranca, sugerir composicao territorial a
partir da geometria e devolver um preview renderizavel.

A persistencia tambem reaproveita o caminho oficial -- `crud.create_setor` para
o Setor (com agentes N:N) e `setor_territorio.definir_territorios` para a
composicao. Nenhum INSERT ad hoc em `setor_territorio_eleitoral`.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from pesquisa360.db import models
from pesquisa360.services import base_eleitoral as base_service
from pesquisa360.services import setor_territorio
from pesquisa360.services.setor_shapefile_import import (
    REQUIRED_EXTENSIONS,
    ImportedSectorGeometry,
    MultipleFeaturesError,
    ShapefileImportError,
    load_sector_geometry,
)

# --- Limites -----------------------------------------------------------------
# Centralizados e configuraveis: upload sem teto e superficie de ataque, e
# numero magico espalhado pelo codigo vira teto que ninguem sabe que existe.
MAX_ITENS_POR_LOTE = int(os.getenv("P360_IMPORT_SETOR_MAX_ITENS", "20"))
MAX_TAMANHO_POR_ITEM = int(os.getenv("P360_IMPORT_SETOR_MAX_ITEM_BYTES", str(20 * 1024 * 1024)))
MAX_TAMANHO_TOTAL = int(os.getenv("P360_IMPORT_SETOR_MAX_TOTAL_BYTES", str(80 * 1024 * 1024)))
# Um ZIP de setor tem 4 a 8 arquivos; centenas indicam pacote errado ou ataque.
MAX_ENTRADAS_ZIP = int(os.getenv("P360_IMPORT_SETOR_MAX_ZIP_ENTRIES", "50"))
MAX_TAMANHO_DESCOMPACTADO = int(
    os.getenv("P360_IMPORT_SETOR_MAX_UNZIPPED_BYTES", str(120 * 1024 * 1024))
)

# Extensoes aceitas dentro do pacote. `.cpg` e `.qmd` acompanham shapefiles
# reais e sao inofensivas; qualquer outra coisa e recusada.
EXTENSOES_PERMITIDAS = frozenset({*REQUIRED_EXTENSIONS, ".cpg", ".qmd", ".qpj", ".sbn", ".sbx"})

# --- Status da sugestao territorial -----------------------------------------
SUGESTAO_DISPONIVEL = "DISPONIVEL"
SUGESTAO_SEM_BASE = "SEM_BASE_ELEITORAL"
SUGESTAO_SEM_GEOMETRIA = "GEOMETRIA_TERRITORIAL_INDISPONIVEL"

# --- Criterio geometrico -----------------------------------------------------
CRITERIO_COBERTO = "COBERTO"
CRITERIO_PARCIAL = "PARCIAL"

# --- Municipio informativo ---------------------------------------------------
MUNICIPIO_UNICO = "UNICO"
MUNICIPIO_MULTIPLOS = "MULTIPLOS_MUNICIPIOS"
MUNICIPIO_NAO_RESOLVIDO = "NAO_RESOLVIDO"


class PacoteInvalidoError(ShapefileImportError):
    """Problema no pacote enviado, antes mesmo de chegar ao motor GIS."""


@dataclass
class TerritorioCandidato:
    id: int
    nome: str
    tipo: str
    criterio: str
    municipio_id: Optional[int]
    municipio_nome: Optional[str]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "nome": self.nome,
            "tipo": self.tipo,
            "criterio": self.criterio,
            "municipio_id": self.municipio_id,
            "municipio_nome": self.municipio_nome,
        }


@dataclass
class AnaliseItem:
    client_id: str
    arquivo: str
    valido: bool = False
    nome_sugerido: str = ""
    quantidade_features: int = 0
    features_unidas: bool = False
    crs_origem: Optional[str] = None
    crs_destino: str = "EPSG:4326"
    geometria: Optional[dict] = None
    bounds: Optional[dict] = None
    territorios_sugeridos: list = field(default_factory=list)
    territorios_parciais: list = field(default_factory=list)
    territorio_status: str = SUGESTAO_SEM_BASE
    municipios_sugeridos: list = field(default_factory=list)
    municipio_status: str = MUNICIPIO_NAO_RESOLVIDO
    exige_uniao: bool = False
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "client_id": self.client_id,
            "arquivo": self.arquivo,
            "valido": self.valido,
            "nome_sugerido": self.nome_sugerido,
            "quantidade_features": self.quantidade_features,
            "features_unidas": self.features_unidas,
            "crs_origem": self.crs_origem,
            "crs_destino": self.crs_destino,
            "geometria": self.geometria,
            "bounds": self.bounds,
            "territorios_sugeridos": [t.to_dict() for t in self.territorios_sugeridos],
            "territorios_parciais": [t.to_dict() for t in self.territorios_parciais],
            "territorio_status": self.territorio_status,
            "municipios_sugeridos": self.municipios_sugeridos,
            "municipio_status": self.municipio_status,
            "exige_uniao": self.exige_uniao,
            "warnings": self.warnings,
            "errors": self.errors,
        }


# --- Abertura segura do pacote ----------------------------------------------


def _erro(mensagem: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=mensagem)


def validar_limites_do_lote(tamanhos: Sequence[int]) -> None:
    """Teto de quantidade e de bytes, antes de gravar qualquer coisa em disco."""
    if not tamanhos:
        raise _erro("Envie ao menos um arquivo.")
    if len(tamanhos) > MAX_ITENS_POR_LOTE:
        raise _erro(
            f"Limite de {MAX_ITENS_POR_LOTE} arquivos por lote. Enviados: {len(tamanhos)}."
        )
    for tamanho in tamanhos:
        if tamanho > MAX_TAMANHO_POR_ITEM:
            raise _erro(
                f"Cada arquivo deve ter no maximo {MAX_TAMANHO_POR_ITEM // (1024 * 1024)} MB."
            )
    if sum(tamanhos) > MAX_TAMANHO_TOTAL:
        raise _erro(
            f"O lote deve somar no maximo {MAX_TAMANHO_TOTAL // (1024 * 1024)} MB."
        )


def _nome_seguro(nome: str) -> str:
    """So o nome-base, sem diretorio.

    `Path(...).name` descarta `../`, caminho absoluto e separador de qualquer
    plataforma. O nome que veio no pacote nunca e usado para montar caminho.
    """
    return Path(nome.replace("\\", "/")).name


def extrair_pacote_zip(origem: Path, destino: Path) -> None:
    """Extrai um ZIP de shapefile com as protecoes minimas.

    Nunca usa o caminho declarado dentro do ZIP: cada entrada e regravada pelo
    nome-base dentro de `destino`. Isso encerra path traversal por construcao,
    em vez de tentar detectar `../` caso a caso.
    """
    try:
        with zipfile.ZipFile(origem) as pacote:
            entradas = [item for item in pacote.infolist() if not item.is_dir()]
            if len(entradas) > MAX_ENTRADAS_ZIP:
                raise PacoteInvalidoError(
                    f"O pacote possui {len(entradas)} arquivos; o limite e {MAX_ENTRADAS_ZIP}."
                )

            total = sum(item.file_size for item in entradas)
            if total > MAX_TAMANHO_DESCOMPACTADO:
                # Zip bomb: o tamanho declarado ja denuncia antes de extrair.
                raise PacoteInvalidoError("O conteudo descompactado excede o limite permitido.")

            escritos = 0
            for item in entradas:
                nome = _nome_seguro(item.filename)
                if not nome or nome.startswith("."):
                    continue
                if Path(nome).suffix.casefold() not in EXTENSOES_PERMITIDAS:
                    continue
                alvo = destino / nome
                with pacote.open(item) as entrada, open(alvo, "wb") as saida:
                    # Copia limitada: protege contra `file_size` mentiroso.
                    while bloco := entrada.read(1024 * 1024):
                        escritos += len(bloco)
                        if escritos > MAX_TAMANHO_DESCOMPACTADO:
                            raise PacoteInvalidoError(
                                "O conteudo descompactado excede o limite permitido."
                            )
                        saida.write(bloco)
    except PacoteInvalidoError:
        raise
    except zipfile.BadZipFile as exc:
        raise PacoteInvalidoError("O arquivo nao e um ZIP valido.") from exc


def localizar_shp(diretorio: Path) -> Path:
    shps = sorted(p for p in diretorio.iterdir() if p.suffix.casefold() == ".shp")
    if not shps:
        raise PacoteInvalidoError(
            "O pacote nao contem arquivo .shp. Envie o conjunto completo "
            "(.shp, .shx, .dbf, .prj) ou um ZIP com ele."
        )
    if len(shps) > 1:
        raise PacoteInvalidoError(
            "O pacote contem mais de um .shp. Envie um Setor por pacote."
        )
    return shps[0]


def materializar_item(
    nome_arquivo: str, conteudo: bytes, raiz: Path
) -> Path:
    """Grava o upload em diretorio proprio e devolve o caminho do `.shp`.

    O nome do arquivo do usuario nunca vira caminho: e sempre reduzido ao
    nome-base dentro de um diretorio temporario controlado.
    """
    seguro = _nome_seguro(nome_arquivo)
    if not seguro:
        raise PacoteInvalidoError("Nome de arquivo invalido.")

    destino = raiz / f"item_{abs(hash(nome_arquivo)) % (10**8)}"
    destino.mkdir(parents=True, exist_ok=True)

    sufixo = Path(seguro).suffix.casefold()
    if sufixo == ".zip":
        caminho_zip = destino / "pacote.zip"
        caminho_zip.write_bytes(conteudo)
        extraido = destino / "conteudo"
        extraido.mkdir(exist_ok=True)
        extrair_pacote_zip(caminho_zip, extraido)
        return localizar_shp(extraido)

    if sufixo not in EXTENSOES_PERMITIDAS:
        raise PacoteInvalidoError(
            f"Extensao {sufixo or '(sem extensao)'} nao suportada. "
            "Envie um ZIP com o conjunto Shapefile."
        )

    # Arquivo solto: so faz sentido acompanhado dos irmaos, o que o motor
    # confere. Aqui apenas grava com o nome-base.
    (destino / seguro).write_bytes(conteudo)
    return destino / seguro


# --- Sugestao territorial ----------------------------------------------------


def _poligono_wkt(geometry: ImportedSectorGeometry) -> str:
    return geometry.polygon.wkt


def sugerir_territorios(
    db: Session, base_id: int, geometry: ImportedSectorGeometry
) -> tuple[list[TerritorioCandidato], list[TerritorioCandidato], str]:
    """Candidatos territoriais do Setor desenhado, por geometria.

    Devolve (cobertos, parciais, status).

    COBERTO significa que o territorio esta INTEIRAMENTE dentro do poligono do
    Setor. Interseccao parcial nunca vira sugestao confirmada: um bairro que
    encosta na borda do Setor por alguns metros seria associado por engano, e
    isso contamina a resolucao de Municipio la na frente.
    """
    tipo = setor_territorio.TIPO_TERRITORIO_ACEITO
    candidatos = (
        db.query(models.TerritorioEleitoral)
        .filter(
            models.TerritorioEleitoral.base_eleitoral_id == base_id,
            models.TerritorioEleitoral.tipo == tipo,
            models.TerritorioEleitoral.geometria.isnot(None),
        )
        .count()
    )
    if candidatos == 0:
        # A base existe, mas os territorios nao tem poligono. Nao ha o que
        # sugerir -- e isso precisa ficar EXPLICITO para o operador, em vez de
        # aparecer como "nenhum bairro encontrado".
        return [], [], SUGESTAO_SEM_GEOMETRIA

    wkt = _poligono_wkt(geometry)
    setor_geom = func.ST_SetSRID(func.ST_GeomFromText(wkt), 4326)

    municipio = models.TerritorioEleitoral.__table__.alias("municipio")
    linhas = (
        db.query(
            models.TerritorioEleitoral.id,
            models.TerritorioEleitoral.nome,
            models.TerritorioEleitoral.tipo,
            models.TerritorioEleitoral.municipio_id,
            municipio.c.nome.label("municipio_nome"),
            func.ST_Covers(setor_geom, models.TerritorioEleitoral.geometria).label("coberto"),
        )
        .outerjoin(municipio, municipio.c.id == models.TerritorioEleitoral.municipio_id)
        .filter(
            models.TerritorioEleitoral.base_eleitoral_id == base_id,
            models.TerritorioEleitoral.tipo == tipo,
            models.TerritorioEleitoral.geometria.isnot(None),
            func.ST_Intersects(setor_geom, models.TerritorioEleitoral.geometria),
        )
        .order_by(models.TerritorioEleitoral.nome)
        .all()
    )

    cobertos: list[TerritorioCandidato] = []
    parciais: list[TerritorioCandidato] = []
    for linha in linhas:
        candidato = TerritorioCandidato(
            id=linha.id,
            nome=linha.nome,
            tipo=linha.tipo,
            criterio=CRITERIO_COBERTO if linha.coberto else CRITERIO_PARCIAL,
            municipio_id=linha.municipio_id,
            municipio_nome=linha.municipio_nome,
        )
        (cobertos if linha.coberto else parciais).append(candidato)

    return cobertos, parciais, SUGESTAO_DISPONIVEL


def resumir_municipios(territorios: Iterable[TerritorioCandidato]) -> tuple[list[dict], str]:
    """Municipio APENAS informativo, para o operador conferir no preview.

    Nao e persistido em lugar nenhum e nao decide nada: a fonte continua sendo
    a composicao territorial do Setor. Multiplos municipios nao viram escolha
    automatica -- viram aviso.
    """
    vistos: dict[int, str] = {}
    for territorio in territorios:
        if territorio.municipio_id is not None:
            vistos.setdefault(territorio.municipio_id, territorio.municipio_nome or "")

    municipios = [{"id": mid, "nome": nome} for mid, nome in vistos.items()]
    if len(municipios) == 1:
        return municipios, MUNICIPIO_UNICO
    if len(municipios) > 1:
        return municipios, MUNICIPIO_MULTIPLOS
    return municipios, MUNICIPIO_NAO_RESOLVIDO


def municipios_de_territorios(
    db: Session, territorio_ids: Sequence[int]
) -> tuple[list[dict], str]:
    """Mesmo resumo, a partir de ids ja confirmados pelo operador."""
    if not territorio_ids:
        return [], MUNICIPIO_NAO_RESOLVIDO

    municipio = models.TerritorioEleitoral.__table__.alias("municipio")
    linhas = (
        db.query(
            models.TerritorioEleitoral.municipio_id,
            municipio.c.nome.label("municipio_nome"),
        )
        .outerjoin(municipio, municipio.c.id == models.TerritorioEleitoral.municipio_id)
        .filter(models.TerritorioEleitoral.id.in_(territorio_ids))
        .all()
    )
    candidatos = [
        TerritorioCandidato(
            id=0,
            nome="",
            tipo="",
            criterio="",
            municipio_id=linha.municipio_id,
            municipio_nome=linha.municipio_nome,
        )
        for linha in linhas
    ]
    return resumir_municipios(candidatos)


# --- Analise de um item ------------------------------------------------------


def _bounds(geometry: ImportedSectorGeometry) -> dict:
    min_x, min_y, max_x, max_y = geometry.polygon.bounds
    return {"min_lat": min_y, "min_lng": min_x, "max_lat": max_y, "max_lng": max_x}


def _geojson(geometry: ImportedSectorGeometry) -> dict:
    # GeoJSON puro (lng, lat), que o Leaflet do Web ja consome nos setores.
    return {
        "type": "Polygon",
        "coordinates": [[[float(x), float(y)] for x, y in geometry.polygon.exterior.coords]],
    }


def analisar_item(
    db: Session,
    *,
    client_id: str,
    nome_arquivo: str,
    conteudo: bytes,
    raiz: Path,
    unir_features: bool,
    base,
) -> AnaliseItem:
    """Le um pacote e devolve o preview. NAO grava nada no banco."""
    item = AnaliseItem(client_id=client_id, arquivo=nome_arquivo)
    item.nome_sugerido = Path(_nome_seguro(nome_arquivo)).stem.replace("_", " ").strip()

    try:
        shp = materializar_item(nome_arquivo, conteudo, raiz)
        geometry = load_sector_geometry(shp, merge_features=unir_features)
    except MultipleFeaturesError as exc:
        # Nao unir por conta propria: o CLI exige decisao explicita e a UI
        # tambem deve exigir.
        item.exige_uniao = True
        item.quantidade_features = exc.feature_count
        item.errors.append(str(exc))
        return item
    except ShapefileImportError as exc:
        item.errors.append(str(exc))
        return item
    except Exception:  # noqa: BLE001 - nunca vazar stack para a UI
        item.errors.append("Nao foi possivel processar o arquivo enviado.")
        return item

    item.valido = True
    item.quantidade_features = geometry.feature_count
    item.features_unidas = geometry.features_merged
    item.crs_origem = geometry.source_crs
    item.geometria = _geojson(geometry)
    item.bounds = _bounds(geometry)
    if geometry.features_merged:
        item.warnings.append(
            f"As {geometry.feature_count} feicoes do arquivo foram unidas em um unico poligono."
        )

    if base is None:
        item.territorio_status = SUGESTAO_SEM_BASE
        item.warnings.append(
            "O projeto nao possui Base Eleitoral principal: a composicao territorial "
            "nao pode ser sugerida nem informada nesta importacao."
        )
        return item

    cobertos, parciais, status_sugestao = sugerir_territorios(db, base.id, geometry)
    item.territorios_sugeridos = cobertos
    item.territorios_parciais = parciais
    item.territorio_status = status_sugestao
    if status_sugestao == SUGESTAO_SEM_GEOMETRIA:
        item.warnings.append(
            "Os territorios da Base Eleitoral nao possuem geometria; a composicao "
            "precisa ser selecionada manualmente."
        )
    elif not cobertos and not parciais:
        item.warnings.append(
            "Nenhum territorio da Base Eleitoral esta contido neste poligono."
        )
    if parciais:
        item.warnings.append(
            f"{len(parciais)} territorio(s) apenas parcialmente contido(s) exigem revisao."
        )

    item.municipios_sugeridos, item.municipio_status = resumir_municipios(cobertos)
    if item.municipio_status == MUNICIPIO_MULTIPLOS:
        item.warnings.append(
            "Este Setor contem territorios de mais de um Municipio."
        )
    return item


# --- Diretorio temporario ----------------------------------------------------


class RaizTemporaria:
    """Diretorio temporario com limpeza garantida.

    Existe como contexto proprio para que o `finally` seja impossivel de
    esquecer em qualquer caminho de erro do endpoint.
    """

    def __enter__(self) -> Path:
        self._caminho = Path(tempfile.mkdtemp(prefix="p360_setor_import_"))
        return self._caminho

    def __exit__(self, *_exc) -> None:
        shutil.rmtree(self._caminho, ignore_errors=True)


def obter_base_opcional(db: Session, projeto_id: int, current_user):
    """Base principal do projeto, ou None. 404 de tenant sobe normalmente."""
    return base_service.obter_base_principal_projeto_opcional(db, projeto_id, current_user)
