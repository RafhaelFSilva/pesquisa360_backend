"""Municipio operacional de referencia do Setor (ADR-035).

Duas regras independentes coexistem na Pesquisa:

    COTA TERRITORIAL  -> unidade = SETOR      ("onde e quantas?")
    COTA POR PERFIL   -> unidade = MUNICIPIO  ("quem entrevistar?")

`setores.municipio_territorio_id` e a ponte persistida entre elas: a mesma
coleta incrementa o realizado do Setor e a celula de perfil do Municipio ao
qual o Setor pertence. A referencia NAO substitui a composicao eleitoral (que
continua sendo a inteligencia detalhada do setor); e uma agregacao operacional.

Fontes, em ordem:
  1. MANUAL     -- informado no create/patch, validado contra a Base principal
                   do Projeto e contra a geometria (nunca aceito as cegas);
  2. GEOMETRIA  -- PostGIS: fracao de area do poligono do setor dentro de cada
                   MUNICIPIO com geometria (somente quando a base tem geometrias);
  3. COMPOSICAO -- bairros da composicao -> municipio (regra ja existente).

Setor OPERACIONAL (finalidade != RELATORIO) que atravessa mais de um municipio
e recusado (422): nunca se atribui o maior pedaco silenciosamente. Setor
analitico pode ser multi-municipal e fica com referencia NULL.

Tenant: sempre o do PROJETO (ADR-034) -- nunca `current_user.company_id`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from pesquisa360.db import models
from pesquisa360.services import base_eleitoral as base_service
from pesquisa360.services import pergunta_territorio

STATUS_RESOLVIDO = "RESOLVIDO"
STATUS_AMBIGUO = "AMBIGUO"
STATUS_SEM_MUNICIPIO = "SEM_MUNICIPIO"
STATUS_FORA_DA_BASE = "FORA_DA_BASE"
STATUS_SEM_BASE = "SEM_BASE"

FONTE_MANUAL = "MANUAL"
FONTE_GEOMETRIA = "GEOMETRIA"
FONTE_COMPOSICAO = "COMPOSICAO"

# Fracao de area a partir da qual um segundo municipio conta como "atravessa".
TOLERANCIA_FRACAO = 0.01
TIPO_MUNICIPIO = "MUNICIPIO"

MENSAGEM_AMBIGUO = (
    "Este setor intercepta mais de um município. Ajuste o polígono antes de "
    "utilizá-lo para operação de campo e cotas por perfil."
)


def eh_operacional(finalidade: Optional[str]) -> bool:
    """OPERACAO e AMBOS operam em campo; RELATORIO e so analitico."""
    return (finalidade or "OPERACAO") != "RELATORIO"


@dataclass(frozen=True)
class Candidato:
    id: int
    nome: str
    fracao: Optional[float] = None   # so na deteccao por geometria


@dataclass
class Deteccao:
    status: str
    municipio_id: Optional[int] = None
    municipio_nome: Optional[str] = None
    fonte: Optional[str] = None
    candidatos: list[Candidato] = field(default_factory=list)
    inconsistencias: list[str] = field(default_factory=list)

    @property
    def resolvido(self) -> bool:
        return self.status == STATUS_RESOLVIDO and self.municipio_id is not None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "fonte": self.fonte,
            "municipio": (
                {"id": self.municipio_id, "nome": self.municipio_nome} if self.resolvido else None
            ),
            "candidatos": [{"id": c.id, "nome": c.nome, "fracao": c.fracao} for c in self.candidatos],
            "inconsistencias": list(self.inconsistencias),
        }


def _invalido(detalhe: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detalhe)


# --- classificacao pura (testavel sem PostGIS) --------------------------------


def classificar_candidatos(candidatos: Sequence[Candidato], fonte: str) -> Deteccao:
    """Regra unica: 1 candidato relevante -> RESOLVIDO; 2+ -> AMBIGUO; 0 -> FORA."""
    relevantes = [c for c in candidatos if c.fracao is None or c.fracao >= TOLERANCIA_FRACAO]
    relevantes.sort(key=lambda c: (-(c.fracao or 0), c.id))
    if len(relevantes) == 1:
        c = relevantes[0]
        return Deteccao(STATUS_RESOLVIDO, c.id, c.nome, fonte, relevantes)
    if len(relevantes) > 1:
        return Deteccao(STATUS_AMBIGUO, None, None, fonte, relevantes)
    return Deteccao(STATUS_FORA_DA_BASE if fonte == FONTE_GEOMETRIA else STATUS_SEM_MUNICIPIO, fonte=fonte)


# --- deteccao por geometria (PostGIS) -----------------------------------------


def _postgis(db: Session) -> bool:
    return getattr(getattr(db, "bind", None), "dialect", None) is not None and db.bind.dialect.name == "postgresql"


def base_tem_geometrias_municipais(db: Session, base_id: int) -> bool:
    return (
        db.query(models.TerritorioEleitoral.id)
        .filter(
            models.TerritorioEleitoral.base_eleitoral_id == base_id,
            models.TerritorioEleitoral.tipo == TIPO_MUNICIPIO,
            models.TerritorioEleitoral.geometria.isnot(None),
        )
        .first()
        is not None
    )


def detectar_por_geometria(db: Session, setor_id: int, base_id: int) -> Optional[Deteccao]:
    """Fracao da area do setor dentro de cada MUNICIPIO da base.

    Area em `geography` (metros quadrados) -- nunca graus tratados como
    metros. Devolve None quando nao ha PostGIS ou a base nao tem geometrias
    municipais: "nao sei", que e diferente de "fora da base".
    """
    if not _postgis(db) or not base_tem_geometrias_municipais(db, base_id):
        return None
    T = models.TerritorioEleitoral
    S = models.Setor
    area_setor = func.ST_Area(func.Geography(S.geometria))
    area_intersecao = func.ST_Area(func.Geography(func.ST_Intersection(S.geometria, T.geometria)))
    linhas = (
        db.query(T.id, T.nome, (area_intersecao / func.nullif(area_setor, 0)).label("fracao"))
        .select_from(S)
        .join(T, func.ST_Intersects(S.geometria, T.geometria))
        .filter(
            S.id == setor_id,
            S.geometria.isnot(None),
            T.base_eleitoral_id == base_id,
            T.tipo == TIPO_MUNICIPIO,
            T.geometria.isnot(None),
        )
        .all()
    )
    return classificar_candidatos(
        [Candidato(id=l.id, nome=l.nome, fracao=float(l.fracao or 0)) for l in linhas], FONTE_GEOMETRIA
    )


# --- deteccao por composicao --------------------------------------------------


def detectar_por_composicao(db: Session, setor_id: int) -> Deteccao:
    r = pergunta_territorio.resolver_por_composicao(db, [setor_id])[setor_id]
    candidatos = [Candidato(id=m.id, nome=m.nome) for m in r.municipios_encontrados]
    if r.status == pergunta_territorio.STATUS_SEM_MUNICIPIO:
        return Deteccao(STATUS_SEM_MUNICIPIO, fonte=FONTE_COMPOSICAO)
    return classificar_candidatos(candidatos, FONTE_COMPOSICAO)


# --- deteccao combinada -------------------------------------------------------


def _municipio_da_base(db: Session, base_id: int, municipio_id: int) -> Optional[models.TerritorioEleitoral]:
    return (
        db.query(models.TerritorioEleitoral)
        .filter(
            models.TerritorioEleitoral.id == municipio_id,
            models.TerritorioEleitoral.base_eleitoral_id == base_id,
            models.TerritorioEleitoral.tipo == TIPO_MUNICIPIO,
        )
        .first()
    )


def detectar(
    db: Session,
    setor: models.Setor,
    projeto_id: int,
    current_user,
    municipio_manual: Optional[int] = None,
) -> Deteccao:
    """Municipio de referencia do setor ja persistido (geometria no banco)."""
    base = base_service.obter_base_principal_projeto_opcional(db, projeto_id, current_user)
    if base is None:
        if municipio_manual is not None:
            raise _invalido("O projeto nao possui Base Eleitoral principal vinculada.")
        return Deteccao(STATUS_SEM_BASE)

    por_geometria = detectar_por_geometria(db, setor.id, base.id)
    por_composicao = detectar_por_composicao(db, setor.id)

    if municipio_manual is not None:
        alvo = _municipio_da_base(db, base.id, municipio_manual)
        if alvo is None:
            # 404 padrao: nao revela municipios de outra base/tenant.
            raise HTTPException(status_code=404, detail="Municipio nao encontrado(a).")
        # Manual nunca contradiz uma localizacao geometrica inequivoca.
        if por_geometria is not None and por_geometria.resolvido and por_geometria.municipio_id != alvo.id:
            raise _invalido(
                f"O polígono do setor está em {por_geometria.municipio_nome}; "
                f"não pode ser referenciado a {alvo.nome}."
            )
        d = Deteccao(STATUS_RESOLVIDO, alvo.id, alvo.nome, FONTE_MANUAL, [Candidato(alvo.id, alvo.nome)])
        _anotar_inconsistencias(d, por_geometria, por_composicao)
        return d

    principal = por_geometria if por_geometria is not None else por_composicao
    d = Deteccao(principal.status, principal.municipio_id, principal.municipio_nome, principal.fonte, list(principal.candidatos))
    _anotar_inconsistencias(d, por_geometria, por_composicao)
    return d


def _anotar_inconsistencias(d: Deteccao, geo: Optional[Deteccao], comp: Deteccao) -> None:
    if geo is not None and geo.resolvido and comp.resolvido and geo.municipio_id != comp.municipio_id:
        d.inconsistencias.append(
            f"Polígono em {geo.municipio_nome}, composição eleitoral em {comp.municipio_nome}."
        )
    if d.resolvido and comp.status == STATUS_AMBIGUO:
        d.inconsistencias.append("A composição eleitoral contém unidades de mais de um município.")
    elif d.resolvido and comp.resolvido and comp.municipio_id != d.municipio_id:
        d.inconsistencias.append(f"A composição eleitoral aponta para {comp.municipio_nome}.")


def aplicar(
    db: Session,
    setor: models.Setor,
    projeto_id: int,
    current_user,
    municipio_manual: Optional[int] = None,
) -> Deteccao:
    """Detecta e persiste (sem commit). Operacional ambiguo -> 422."""
    try:
        d = detectar(db, setor, projeto_id, current_user, municipio_manual)
    except OperationalError:
        # Esquema sem Base Eleitoral (fixtures legadas/ambiente parcial): sem
        # como resolver, e sem bloquear a operacao do setor. Manual exige base.
        if municipio_manual is not None:
            raise
        return Deteccao(STATUS_SEM_BASE)
    if eh_operacional(setor.finalidade) and d.status == STATUS_AMBIGUO:
        raise _invalido(MENSAGEM_AMBIGUO)
    if d.status == STATUS_AMBIGUO:
        setor.municipio_territorio_id = None      # analitico multi-municipal
    elif d.resolvido:
        setor.municipio_territorio_id = d.municipio_id
    elif d.status in (STATUS_FORA_DA_BASE,):
        setor.municipio_territorio_id = None
    # SEM_MUNICIPIO / SEM_BASE: "nao sei" -- preserva o que ja havia.
    return d


def situacao(db: Session, setor: models.Setor, projeto_id: int, current_user) -> dict:
    """Leitura para a UI: referencia persistida + coerencia com a composicao."""
    ref = None
    if setor.municipio_territorio_id is not None:
        m = db.get(models.TerritorioEleitoral, setor.municipio_territorio_id)
        if m is not None:
            ref = {"id": m.id, "nome": m.nome}
    comp = detectar_por_composicao(db, setor.id)
    avisos: list[str] = []
    if ref is not None and comp.status == STATUS_AMBIGUO:
        avisos.append("A composição contém unidades de outro Município.")
    elif ref is not None and comp.resolvido and comp.municipio_id != ref["id"]:
        avisos.append(f"A composição contém unidades de outro Município ({comp.municipio_nome}).")
    return {
        "municipio": ref,
        "municipio_status": STATUS_RESOLVIDO if ref else (comp.status if comp.status != STATUS_RESOLVIDO else STATUS_SEM_MUNICIPIO),
        "composicao_compativel": ref is not None and comp.resolvido and comp.municipio_id == ref["id"],
        "avisos": avisos,
    }
