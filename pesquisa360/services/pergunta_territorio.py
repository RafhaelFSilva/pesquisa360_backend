"""Aplicabilidade territorial das Perguntas (FASE F).

Aqui vive a UNICA implementacao de tres regras que varios endpoints precisam e
que nao podem divergir entre si:

1. que municipio um Setor representa (a partir da composicao territorial);
2. quais perguntas se aplicam a um Setor;
3. validacao das associacoes Pergunta -> Municipio.

O municipio de um Setor nao e um campo: e derivado, sempre, de
Setor -> SetorTerritorioEleitoral -> TerritorioEleitoral(BAIRRO).municipio_id.
Duplicar isso num campo do Setor criaria duas fontes que um dia discordariam.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from pesquisa360.db import models
from pesquisa360.services import base_eleitoral as base_service

APLICABILIDADE_GLOBAL = "GLOBAL"
APLICABILIDADE_TERRITORIAL = "TERRITORIAL"
APLICABILIDADES = (APLICABILIDADE_GLOBAL, APLICABILIDADE_TERRITORIAL)

TIPO_MUNICIPIO = "MUNICIPIO"

# Estados da resolucao municipal de um Setor. Nenhum deles "escolhe" nada: a
# regra devolve o municipio apenas quando ele e inequivoco.
STATUS_RESOLVIDO = "RESOLVIDO"
STATUS_SEM_MUNICIPIO = "SEM_MUNICIPIO"
STATUS_AMBIGUO = "AMBIGUO"


@dataclass(frozen=True)
class MunicipioResolvido:
    id: int
    nome: str

    def to_dict(self) -> dict:
        return {"id": self.id, "nome": self.nome}


@dataclass(frozen=True)
class ResolucaoMunicipal:
    status: str
    municipio: Optional[MunicipioResolvido] = None
    # Para o operador entender um AMBIGUO sem abrir o banco.
    municipios_encontrados: tuple = field(default_factory=tuple)

    @property
    def resolvido(self) -> bool:
        return self.status == STATUS_RESOLVIDO and self.municipio is not None


# --- Resolucao do municipio de Setores ---------------------------------------


def resolver_municipios_setores(
    db: Session, setor_ids: Sequence[int]
) -> dict[int, ResolucaoMunicipal]:
    """Municipio de cada Setor (ADR-035): a referencia PERSISTIDA
    (`setores.municipio_territorio_id`) vale primeiro; sem ela, a composicao
    eleitoral (regra historica) continua respondendo."""
    ids = list(dict.fromkeys(setor_ids))
    if not ids:
        return {}
    municipio = models.TerritorioEleitoral
    persistidos = {
        sid: MunicipioResolvido(id=mid, nome=nome)
        for sid, mid, nome in (
            db.query(models.Setor.id, municipio.id, municipio.nome)
            .join(municipio, municipio.id == models.Setor.municipio_territorio_id)
            .filter(models.Setor.id.in_(ids), models.Setor.municipio_territorio_id.isnot(None))
            .all()
        )
    }
    restantes = [sid for sid in ids if sid not in persistidos]
    resultado = resolver_por_composicao(db, restantes) if restantes else {}
    for sid, m in persistidos.items():
        resultado[sid] = ResolucaoMunicipal(status=STATUS_RESOLVIDO, municipio=m, municipios_encontrados=(m,))
    return resultado


def resolver_por_composicao(
    db: Session, setor_ids: Sequence[int]
) -> dict[int, ResolucaoMunicipal]:
    """Municipio de cada Setor pela composicao, em UMA consulta para o lote.

    Cadeia real: SetorTerritorioEleitoral -> TerritorioEleitoral (bairro) ->
    TerritorioEleitoral (municipio, via municipio_id). O join do municipio e
    OUTER de proposito: bairro sem `municipio_id` resolvivel continua contando
    como composicao -- e leva o Setor a SEM_MUNICIPIO, nunca a um chute.
    """
    ids = list(dict.fromkeys(setor_ids))
    if not ids:
        return {}

    bairro = models.TerritorioEleitoral
    municipio = models.TerritorioEleitoral.__table__.alias("municipio")

    linhas = (
        db.query(
            models.SetorTerritorioEleitoral.setor_id,
            bairro.id.label("bairro_id"),
            municipio.c.id.label("municipio_id"),
            municipio.c.nome.label("municipio_nome"),
        )
        .join(bairro, bairro.id == models.SetorTerritorioEleitoral.territorio_eleitoral_id)
        .outerjoin(
            municipio,
            (municipio.c.id == bairro.municipio_id)
            & (municipio.c.base_eleitoral_id == bairro.base_eleitoral_id)
            & (municipio.c.tipo == TIPO_MUNICIPIO),
        )
        .filter(models.SetorTerritorioEleitoral.setor_id.in_(ids))
        .all()
    )

    por_setor: dict[int, dict[int, str]] = {}
    com_composicao: set[int] = set()
    for linha in linhas:
        com_composicao.add(linha.setor_id)
        if linha.municipio_id is not None:
            por_setor.setdefault(linha.setor_id, {})[linha.municipio_id] = linha.municipio_nome

    resultado: dict[int, ResolucaoMunicipal] = {}
    for setor_id in ids:
        encontrados = por_setor.get(setor_id, {})
        if len(encontrados) == 1:
            (mid, nome), = encontrados.items()
            resultado[setor_id] = ResolucaoMunicipal(
                status=STATUS_RESOLVIDO,
                municipio=MunicipioResolvido(id=mid, nome=nome),
                municipios_encontrados=(MunicipioResolvido(id=mid, nome=nome),),
            )
        elif len(encontrados) > 1:
            resultado[setor_id] = ResolucaoMunicipal(
                status=STATUS_AMBIGUO,
                municipios_encontrados=tuple(
                    MunicipioResolvido(id=mid, nome=nome)
                    for mid, nome in sorted(encontrados.items())
                ),
            )
        else:
            # Sem composicao, OU composicao cujos bairros nao resolvem
            # municipio: os dois casos sao "nao sei", nunca "o primeiro".
            resultado[setor_id] = ResolucaoMunicipal(status=STATUS_SEM_MUNICIPIO)
    return resultado


def resolver_municipio_setor(db: Session, setor_id: int) -> ResolucaoMunicipal:
    """Versao unitaria do helper em lote; mesma regra, sem duplicacao."""
    return resolver_municipios_setores(db, [setor_id])[setor_id]


# --- Perguntas aplicaveis ----------------------------------------------------


def _mapa_municipios_por_pergunta(db: Session, pergunta_ids: Iterable[int]) -> dict[int, set[int]]:
    ids = list(pergunta_ids)
    if not ids:
        return {}
    mapa: dict[int, set[int]] = {}
    for pergunta_id, territorio_id in (
        db.query(
            models.PerguntaTerritorioEleitoral.pergunta_id,
            models.PerguntaTerritorioEleitoral.territorio_eleitoral_id,
        )
        .filter(models.PerguntaTerritorioEleitoral.pergunta_id.in_(ids))
        .all()
    ):
        mapa.setdefault(pergunta_id, set()).add(territorio_id)
    return mapa


def perguntas_aplicaveis_por_setor(
    db: Session, pesquisa_id: int, setor_ids: Sequence[int]
) -> dict[int, list[int]]:
    """IDs de perguntas aplicaveis a cada Setor, na ordem do questionario.

    GLOBAL entra sempre. TERRITORIAL entra somente quando o Setor esta
    RESOLVIDO e o municipio dele pertence ao conjunto da pergunta. Setor
    SEM_MUNICIPIO ou AMBIGUO fica so com as GLOBAL -- e o status vai junto no
    contrato, para a ausencia das territoriais nunca passar despercebida.

    Uma pergunta aparece no maximo uma vez por Setor, independentemente de
    quantos bairros ou municipios a alcancaram.
    """
    perguntas = (
        db.query(
            models.Pergunta.id,
            models.Pergunta.aplicabilidade,
        )
        .filter(
            models.Pergunta.pesquisa_id == pesquisa_id,
            models.Pergunta.ativo.is_(True),
        )
        .order_by(models.Pergunta.ordem, models.Pergunta.id)
        .all()
    )
    territoriais = [p.id for p in perguntas if p.aplicabilidade == APLICABILIDADE_TERRITORIAL]
    municipios = _mapa_municipios_por_pergunta(db, territoriais)
    resolucoes = resolver_municipios_setores(db, setor_ids)

    resultado: dict[int, list[int]] = {}
    for setor_id in dict.fromkeys(setor_ids):
        resolucao = resolucoes.get(setor_id, ResolucaoMunicipal(status=STATUS_SEM_MUNICIPIO))
        municipio_id = resolucao.municipio.id if resolucao.resolvido else None
        aplicaveis: list[int] = []
        for pergunta in perguntas:
            if pergunta.aplicabilidade != APLICABILIDADE_TERRITORIAL:
                aplicaveis.append(pergunta.id)
            elif municipio_id is not None and municipio_id in municipios.get(pergunta.id, set()):
                aplicaveis.append(pergunta.id)
        resultado[setor_id] = aplicaveis
    return resultado


def obter_perguntas_aplicaveis_setor(db: Session, pesquisa_id: int, setor_id: int) -> list[int]:
    return perguntas_aplicaveis_por_setor(db, pesquisa_id, [setor_id])[setor_id]


def pesquisa_possui_perguntas_territoriais(db: Session, pesquisa_id: int) -> bool:
    return (
        db.query(models.Pergunta.id)
        .filter(
            models.Pergunta.pesquisa_id == pesquisa_id,
            models.Pergunta.ativo.is_(True),
            models.Pergunta.aplicabilidade == APLICABILIDADE_TERRITORIAL,
        )
        .first()
        is not None
    )


def perguntas_globais_ids(db: Session, pesquisa_id: int) -> list[int]:
    """Conjunto para coleta SEM setor: nada territorial sem territorio."""
    return [
        pergunta_id
        for pergunta_id, in db.query(models.Pergunta.id)
        .filter(
            models.Pergunta.pesquisa_id == pesquisa_id,
            models.Pergunta.ativo.is_(True),
            models.Pergunta.aplicabilidade == APLICABILIDADE_GLOBAL,
        )
        .order_by(models.Pergunta.ordem, models.Pergunta.id)
        .all()
    ]


# --- Validacao de configuracao -----------------------------------------------


def _nao_encontrado(entidade: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{entidade} nao encontrado.")


def _invalido(detalhe: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detalhe)


def validar_configuracao(aplicabilidade: str, municipio_ids: Optional[Sequence[int]]) -> list[int]:
    """Invariantes do dominio, antes de olhar o banco.

    GLOBAL + municipios e TERRITORIAL + [] sao rejeitados em vez de
    normalizados: uma configuracao incoerente e um erro do operador, e
    "consertar" em silencio esconderia esse erro.
    """
    if aplicabilidade not in APLICABILIDADES:
        raise _invalido("Aplicabilidade invalida.")
    ids = list(dict.fromkeys(municipio_ids or []))
    if aplicabilidade == APLICABILIDADE_GLOBAL and ids:
        raise _invalido("Pergunta GLOBAL nao pode ter municipios associados.")
    if aplicabilidade == APLICABILIDADE_TERRITORIAL and not ids:
        raise _invalido("Pergunta TERRITORIAL exige ao menos um municipio.")
    if any(item <= 0 for item in ids):
        raise _invalido("municipio_ids deve conter apenas IDs positivos.")
    return ids


def validar_municipios(
    db: Session, projeto_id: int, municipio_ids: Sequence[int], current_user
) -> list[models.TerritorioEleitoral]:
    """Municipios existentes, de tipo MUNICIPIO, da base principal do projeto.

    Base errada, tipo errado (BAIRRO, por exemplo), tenant errado e inexistente
    sao indistinguiveis de proposito: 404 nao revela o que existe fora do
    contexto do usuario.
    """
    if not municipio_ids:
        return []
    base = base_service.obter_base_principal_projeto_opcional(db, projeto_id, current_user)
    if base is None:
        raise _invalido("O projeto nao possui Base Eleitoral principal vinculada.")

    encontrados = (
        db.query(models.TerritorioEleitoral)
        .filter(
            models.TerritorioEleitoral.id.in_(municipio_ids),
            models.TerritorioEleitoral.base_eleitoral_id == base.id,
            models.TerritorioEleitoral.tipo == TIPO_MUNICIPIO,
        )
        .all()
    )
    if len(encontrados) != len(set(municipio_ids)):
        raise _nao_encontrado("Municipio")
    return encontrados


def definir_municipios(db: Session, pergunta: models.Pergunta, municipio_ids: Sequence[int]) -> None:
    """Substitui o conjunto de municipios da pergunta. NAO faz commit."""
    db.query(models.PerguntaTerritorioEleitoral).filter(
        models.PerguntaTerritorioEleitoral.pergunta_id == pergunta.id
    ).delete(synchronize_session=False)
    for municipio_id in dict.fromkeys(municipio_ids):
        db.add(
            models.PerguntaTerritorioEleitoral(
                pergunta_id=pergunta.id, territorio_eleitoral_id=municipio_id
            )
        )


def municipios_da_pergunta(db: Session, pergunta_id: int) -> list[dict]:
    linhas = (
        db.query(models.TerritorioEleitoral.id, models.TerritorioEleitoral.nome)
        .join(
            models.PerguntaTerritorioEleitoral,
            models.PerguntaTerritorioEleitoral.territorio_eleitoral_id == models.TerritorioEleitoral.id,
        )
        .filter(models.PerguntaTerritorioEleitoral.pergunta_id == pergunta_id)
        .order_by(models.TerritorioEleitoral.nome)
        .all()
    )
    return [{"id": linha.id, "nome": linha.nome} for linha in linhas]


def municipios_por_pergunta(db: Session, pergunta_ids: Iterable[int]) -> dict[int, list[dict]]:
    ids = list(pergunta_ids)
    if not ids:
        return {}
    mapa: dict[int, list[dict]] = {}
    for pergunta_id, mid, nome in (
        db.query(
            models.PerguntaTerritorioEleitoral.pergunta_id,
            models.TerritorioEleitoral.id,
            models.TerritorioEleitoral.nome,
        )
        .join(
            models.TerritorioEleitoral,
            models.TerritorioEleitoral.id == models.PerguntaTerritorioEleitoral.territorio_eleitoral_id,
        )
        .filter(models.PerguntaTerritorioEleitoral.pergunta_id.in_(ids))
        .order_by(models.TerritorioEleitoral.nome)
        .all()
    ):
        mapa.setdefault(pergunta_id, []).append({"id": mid, "nome": nome})
    return mapa


# --- Validacao de respostas (cliente F) ---------------------------------------


def validar_respostas_territoriais(
    db: Session,
    pesquisa_id: int,
    setor_id: Optional[int],
    pergunta_ids: Iterable[int],
) -> None:
    """Toda resposta precisa ser de uma pergunta aplicavel ao contexto.

    Com setor: conjunto aplicavel daquele setor. Sem setor: somente GLOBAL.
    Uma unica resposta fora do conjunto derruba a coleta inteira (a chamada
    acontece antes do flush), e a mensagem nomeia a pergunta -- nunca se
    descarta a resposta e responde 201 como se nada tivesse acontecido.
    """
    enviados = list(pergunta_ids)
    if not enviados:
        return
    if setor_id is None:
        aplicaveis = set(perguntas_globais_ids(db, pesquisa_id))
    else:
        aplicaveis = set(obter_perguntas_aplicaveis_setor(db, pesquisa_id, setor_id))
    indevidas = sorted(set(enviados) - aplicaveis)
    if indevidas:
        raise _invalido(
            "Resposta para pergunta nao aplicavel a este setor: "
            + ", ".join(str(item) for item in indevidas)
        )
