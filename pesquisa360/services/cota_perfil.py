"""Cotas de Perfil amostral (PROMPT 05).

Dominio ORIENTATIVO: informa quais perfis (municipio x sexo x faixa etaria)
estao relativamente atrasados. Nunca bloqueia abordagem, entrevista ou
sincronizacao -- a cota territorial (setor) e que e bloqueante.

Fonte da verdade do realizado: Coletas com respostas classificatorias.
Tentativas de campo nunca entram. O Mobile recebe o snapshot pronto.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence

from fastapi import HTTPException
from sqlalchemy.orm import Session

from pesquisa360 import schemas
from pesquisa360.db import models
from pesquisa360.services import acessos
from pesquisa360.services import pergunta_territorio

# --- Limiares centralizados (unicos no sistema) ------------------------------
# desvio_pp = percentual_celula - percentual_territorio (negativo = atrasada).
LIMITE_DEFICIT_BAIXO = -5.0
LIMITE_DEFICIT_MEDIO = -10.0
LIMITE_DEFICIT_ALTO = -20.0
# Antes de 10% da meta do territorio nao ha prioridade (amostra inicial).
FRACAO_MINIMA_PARA_PRIORIDADE = 0.10

STATUS_FASE_INICIAL = "FASE_INICIAL"
STATUS_EQUILIBRADO = "EQUILIBRADO"
STATUS_PRIORIDADES = "PRIORIDADES"

ROTULO_SEXO = {"MASCULINO": "Homem", "FEMININO": "Mulher"}

NAO_CLASSIFICADA_SEM_SETOR = "SEM_SETOR"
NAO_CLASSIFICADA_SEM_MUNICIPIO = "SEM_MUNICIPIO"
NAO_CLASSIFICADA_SEM_SEXO = "SEM_SEXO"
NAO_CLASSIFICADA_SEXO_DESCONHECIDO = "SEXO_DESCONHECIDO"
NAO_CLASSIFICADA_SEM_IDADE = "SEM_IDADE"
NAO_CLASSIFICADA_IDADE_INVALIDA = "IDADE_INVALIDA"
NAO_CLASSIFICADA_SEM_CELULA = "SEM_CELULA"


def classificar_prioridade(desvio_pp: float) -> str:
    """Helper unico: EQUILIBRADO / BAIXO / MEDIO / ALTO a partir do desvio."""
    if desvio_pp < LIMITE_DEFICIT_ALTO:
        return schemas.PrioridadePerfil.ALTO.value
    if desvio_pp < LIMITE_DEFICIT_MEDIO:
        return schemas.PrioridadePerfil.MEDIO.value
    if desvio_pp < LIMITE_DEFICIT_BAIXO:
        return schemas.PrioridadePerfil.BAIXO.value
    return schemas.PrioridadePerfil.EQUILIBRADO.value


def _normalizar(texto: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (texto or "").strip()).casefold()


# --- Configuracao ------------------------------------------------------------

def _nao_encontrado(entidade: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{entidade} nao encontrado(a).")


def _invalido(detalhe: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detalhe)


def _pesquisa_do_tenant(db: Session, projeto_id: int, pesquisa_id: int, current_user) -> models.Pesquisa:
    pesquisa = (
        db.query(models.Pesquisa)
        .join(models.Projeto, models.Projeto.id == models.Pesquisa.projeto_id)
        .filter(
            models.Pesquisa.id == pesquisa_id,
            models.Pesquisa.projeto_id == projeto_id,
            acessos.filtro_projeto_acessivel(current_user),
        )
        .first()
    )
    if pesquisa is None:
        raise _nao_encontrado("Pesquisa")
    return pesquisa


def _pergunta_da_pesquisa(db: Session, pergunta_id: int, pesquisa_id: int, papel: str) -> models.Pergunta:
    pergunta = (
        db.query(models.Pergunta)
        .filter(
            models.Pergunta.id == pergunta_id,
            models.Pergunta.pesquisa_id == pesquisa_id,
            models.Pergunta.ativo.is_(True),
        )
        .first()
    )
    if pergunta is None:
        raise _invalido(f"Pergunta de {papel} nao pertence a esta pesquisa.")
    return pergunta


def _validar_faixas(territorio_id: int, sexo: str, cotas: Sequence[schemas.CotaPerfilItem], modo: str) -> None:
    if modo == schemas.ModoIdadeCota.NUMERICA.value:
        intervalos = sorted(
            ((c.idade_min or 0, c.idade_max if c.idade_max is not None else 10**6) for c in cotas)
        )
        for (a_min, a_max), (b_min, _b_max) in zip(intervalos, intervalos[1:]):
            if b_min <= a_max:
                raise _invalido(
                    f"Faixas etarias sobrepostas para {sexo} no territorio {territorio_id}."
                )
    else:
        vistos: set = set()
        for cota in cotas:
            for valor in cota.idade_valores or []:
                chave = _normalizar(valor)
                if chave in vistos:
                    raise _invalido(
                        f"Valor de faixa '{valor}' repetido para {sexo} no territorio {territorio_id}."
                    )
                vistos.add(chave)


def definir_plano(
    db: Session,
    projeto_id: int,
    pesquisa_id: int,
    payload: schemas.PlanoCotaPerfilRequest,
    current_user,
) -> models.PlanoCotaPerfil:
    """PUT transacional: o payload valido substitui o plano inteiro; qualquer
    recusa deixa o plano anterior intacto."""
    pesquisa = _pesquisa_do_tenant(db, projeto_id, pesquisa_id, current_user)
    # ADR-034: o tenant do plano e o do PROJETO. `current_user.company_id` e a
    # empresa principal de quem configura -- um Superadmin de outra empresa ou
    # um Gerente com ACL cruzada gravaria o plano no tenant errado.
    tenant_id = (
        db.query(models.Projeto.company_id).filter(models.Projeto.id == pesquisa.projeto_id).scalar()
    )
    _pergunta_da_pesquisa(db, payload.pergunta_sexo_id, pesquisa_id, "sexo")
    _pergunta_da_pesquisa(db, payload.pergunta_idade_id, pesquisa_id, "idade")

    territorio_ids = [t.territorio_id for t in payload.territorios]
    # 404 para municipio fora da base principal do projeto / outro tenant.
    municipios = pergunta_territorio.validar_municipios(db, projeto_id, territorio_ids, current_user)
    nomes = {m.id: m.nome for m in municipios}

    for territorio in payload.territorios:
        por_sexo: Dict[str, List[schemas.CotaPerfilItem]] = {}
        for cota in territorio.cotas:
            por_sexo.setdefault(cota.sexo.value, []).append(cota)
        for sexo, cotas in por_sexo.items():
            _validar_faixas(territorio.territorio_id, sexo, cotas, payload.modo_idade.value)

    try:
        plano = (
            db.query(models.PlanoCotaPerfil)
            .filter(models.PlanoCotaPerfil.pesquisa_id == pesquisa_id)
            .first()
        )
        if plano is None:
            plano = models.PlanoCotaPerfil(pesquisa_id=pesquisa_id, company_id=tenant_id)
            db.add(plano)
        else:
            plano.company_id = tenant_id   # corrige plano legado gravado com a empresa do usuario
            db.query(models.CotaPerfil).filter(models.CotaPerfil.plano_id == plano.id).delete(
                synchronize_session=False
            )
        plano.ativo = payload.ativo
        plano.pergunta_sexo_id = payload.pergunta_sexo_id
        plano.pergunta_idade_id = payload.pergunta_idade_id
        plano.modo_idade = payload.modo_idade.value
        plano.sexo_valores = {k.value if hasattr(k, "value") else str(k): v for k, v in payload.sexo_valores.items()}
        db.flush()

        ordem = 0
        for territorio in payload.territorios:
            for cota in territorio.cotas:
                db.add(
                    models.CotaPerfil(
                        plano_id=plano.id,
                        territorio_eleitoral_id=territorio.territorio_id,
                        sexo=cota.sexo.value,
                        faixa_rotulo=cota.faixa_etaria,
                        idade_min=cota.idade_min,
                        idade_max=cota.idade_max,
                        idade_valores=cota.idade_valores,
                        meta=cota.meta,
                        ordem=ordem,
                    )
                )
                ordem += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(plano)
    plano._nomes_territorios = nomes  # cache de apresentacao
    return plano


def obter_plano(db: Session, projeto_id: int, pesquisa_id: int, current_user) -> Optional[models.PlanoCotaPerfil]:
    """Plano da pesquisa ja autorizada pela ACL do Projeto (ADR-034).

    O recorte de tenant e feito em `_pesquisa_do_tenant`; filtrar aqui por
    `current_user.company_id` escondia o plano de quem tem acesso legitimo ao
    Projeto por outra empresa (Superadmin, ACL cruzada).
    """
    _pesquisa_do_tenant(db, projeto_id, pesquisa_id, current_user)
    return (
        db.query(models.PlanoCotaPerfil)
        .filter(models.PlanoCotaPerfil.pesquisa_id == pesquisa_id)
        .first()
    )


def _nomes_territorios(db: Session, ids: Iterable[int]) -> Dict[int, str]:
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}
    linhas = (
        db.query(models.TerritorioEleitoral.id, models.TerritorioEleitoral.nome)
        .filter(models.TerritorioEleitoral.id.in_(ids))
        .all()
    )
    return {tid: nome for tid, nome in linhas}


def plano_para_leitura(db: Session, plano: models.PlanoCotaPerfil) -> dict:
    nomes = _nomes_territorios(db, [c.territorio_eleitoral_id for c in plano.cotas])
    cotas = [
        {
            "id": c.id,
            "territorio_id": c.territorio_eleitoral_id,
            "territorio_nome": nomes.get(c.territorio_eleitoral_id),
            "sexo": c.sexo,
            "faixa_etaria": c.faixa_rotulo,
            "idade_min": c.idade_min,
            "idade_max": c.idade_max,
            "idade_valores": c.idade_valores,
            "meta": c.meta,
        }
        for c in plano.cotas
    ]
    # Diagnostico: soma das celulas por municipio. Nao existe meta municipal
    # formal no dominio (metas sao por setor), entao meta_territorial e null e
    # nada e rejeitado -- apenas informado.
    totais: Dict[int, int] = {}
    for c in plano.cotas:
        totais[c.territorio_eleitoral_id] = totais.get(c.territorio_eleitoral_id, 0) + int(c.meta or 0)
    # ADR-035: meta territorial consolidada = SUM(meta) dos setores OPERACIONAIS
    # referenciados ao municipio. Sem regra de igualdade: so informa a diferenca.
    contexto = {c["territorio_id"]: c for c in contexto_territorial(db, plano.pesquisa_id)}
    diagnostico = []
    for tid, total in sorted(totais.items()):
        ctx = contexto.get(tid)
        meta_territorial = ctx["meta_territorial"] if ctx and ctx["setores_operacionais"] else None
        diagnostico.append({
            "territorio_id": tid,
            "territorio_nome": nomes.get(tid),
            "total_cotas_perfil": total,
            "meta_territorial": meta_territorial,
            "diferenca": (total - meta_territorial) if meta_territorial is not None else None,
            "setores_operacionais": ctx["setores_operacionais"] if ctx else [],
        })
    return {
        "id": plano.id,
        "pesquisa_id": plano.pesquisa_id,
        "ativo": plano.ativo,
        "pergunta_sexo_id": plano.pergunta_sexo_id,
        "pergunta_idade_id": plano.pergunta_idade_id,
        "modo_idade": plano.modo_idade,
        "sexo_valores": plano.sexo_valores,
        "cotas": cotas,
        "diagnostico": diagnostico,
    }


# --- Classificacao das coletas ----------------------------------------------

@dataclass
class ResultadoClassificacao:
    realizado_por_cota: Dict[int, int] = field(default_factory=dict)
    nao_classificadas: int = 0
    motivos: Dict[str, int] = field(default_factory=dict)

    def registrar_nao_classificada(self, motivo: str) -> None:
        self.nao_classificadas += 1
        self.motivos[motivo] = self.motivos.get(motivo, 0) + 1


def _mapa_sexo(plano: models.PlanoCotaPerfil) -> Dict[str, str]:
    mapa: Dict[str, str] = {}
    for sexo, valores in (plano.sexo_valores or {}).items():
        for valor in valores or []:
            mapa[_normalizar(valor)] = str(sexo)
    return mapa


def _idade_numerica(valor: Optional[str]) -> Optional[int]:
    if valor is None:
        return None
    match = re.search(r"\d{1,3}", str(valor))
    if not match:
        return None
    idade = int(match.group())
    return idade if 0 <= idade <= 130 else None


def _localizar_cota(
    cotas: Sequence[models.CotaPerfil],
    modo: str,
    municipio_id: int,
    sexo: str,
    idade_raw: Optional[str],
) -> Optional[models.CotaPerfil]:
    candidatas = [c for c in cotas if c.territorio_eleitoral_id == municipio_id and c.sexo == sexo]
    if modo == schemas.ModoIdadeCota.NUMERICA.value:
        idade = _idade_numerica(idade_raw)
        if idade is None:
            return None
        for c in candidatas:
            minimo = c.idade_min if c.idade_min is not None else 0
            if idade >= minimo and (c.idade_max is None or idade <= c.idade_max):
                return c
        return None
    chave = _normalizar(idade_raw)
    for c in candidatas:
        if any(_normalizar(v) == chave for v in (c.idade_valores or [])):
            return c
    return None


def classificar_coletas(db: Session, plano: models.PlanoCotaPerfil, pesquisa_id: int) -> ResultadoClassificacao:
    """Cada Coleta cai em NO MAXIMO uma celula; sem dados -> NAO_CLASSIFICADA."""
    resultado = ResultadoClassificacao({c.id: 0 for c in plano.cotas})
    coletas = (
        db.query(models.Coleta.id, models.Coleta.setor_id)
        .filter(models.Coleta.pesquisa_id == pesquisa_id)
        .all()
    )
    if not coletas:
        return resultado

    setor_ids = sorted({setor_id for _, setor_id in coletas if setor_id is not None})
    resolucoes = pergunta_territorio.resolver_municipios_setores(db, setor_ids)

    coleta_ids = [cid for cid, _ in coletas]
    respostas: Dict[int, Dict[int, str]] = {}
    for coleta_id, pergunta_id, valor in (
        db.query(models.Resposta.coleta_id, models.Resposta.pergunta_id, models.Resposta.valor_resposta)
        .filter(
            models.Resposta.coleta_id.in_(coleta_ids),
            models.Resposta.pergunta_id.in_([plano.pergunta_sexo_id, plano.pergunta_idade_id]),
        )
        .all()
    ):
        respostas.setdefault(coleta_id, {})[pergunta_id] = valor

    mapa_sexo = _mapa_sexo(plano)
    for coleta_id, setor_id in coletas:
        if setor_id is None:
            resultado.registrar_nao_classificada(NAO_CLASSIFICADA_SEM_SETOR)
            continue
        resolucao = resolucoes.get(setor_id)
        if resolucao is None or not resolucao.resolvido:
            resultado.registrar_nao_classificada(NAO_CLASSIFICADA_SEM_MUNICIPIO)
            continue
        valores = respostas.get(coleta_id, {})
        sexo_raw = valores.get(plano.pergunta_sexo_id)
        if sexo_raw is None or not str(sexo_raw).strip():
            resultado.registrar_nao_classificada(NAO_CLASSIFICADA_SEM_SEXO)
            continue
        sexo = mapa_sexo.get(_normalizar(str(sexo_raw)))
        if sexo is None:
            resultado.registrar_nao_classificada(NAO_CLASSIFICADA_SEXO_DESCONHECIDO)
            continue
        idade_raw = valores.get(plano.pergunta_idade_id)
        if idade_raw is None or not str(idade_raw).strip():
            resultado.registrar_nao_classificada(NAO_CLASSIFICADA_SEM_IDADE)
            continue
        if plano.modo_idade == schemas.ModoIdadeCota.NUMERICA.value and _idade_numerica(str(idade_raw)) is None:
            resultado.registrar_nao_classificada(NAO_CLASSIFICADA_IDADE_INVALIDA)
            continue
        cota = _localizar_cota(plano.cotas, plano.modo_idade, resolucao.municipio.id, sexo, str(idade_raw))
        if cota is None:
            resultado.registrar_nao_classificada(NAO_CLASSIFICADA_SEM_CELULA)
            continue
        resultado.realizado_por_cota[cota.id] = resultado.realizado_por_cota.get(cota.id, 0) + 1
    return resultado


# --- Motor de prioridade ------------------------------------------------------

def _percentual(realizado: int, meta: int) -> float:
    return round((realizado * 100.0) / meta, 2) if meta > 0 else 0.0


def calcular_progresso(db: Session, plano: models.PlanoCotaPerfil, pesquisa_id: int) -> dict:
    """Snapshot completo (numeros incluidos) por municipio. O Mobile recebe
    dele apenas as prioridades; a UI do agente nao exibe quantidades."""
    classificacao = classificar_coletas(db, plano, pesquisa_id)
    nomes = _nomes_territorios(db, [c.territorio_eleitoral_id for c in plano.cotas])
    por_territorio: Dict[int, List[models.CotaPerfil]] = {}
    for cota in plano.cotas:
        por_territorio.setdefault(cota.territorio_eleitoral_id, []).append(cota)

    territorios = []
    for tid in sorted(por_territorio):
        cotas = por_territorio[tid]
        meta_total = sum(int(c.meta or 0) for c in cotas)
        realizado_total = sum(classificacao.realizado_por_cota.get(c.id, 0) for c in cotas)
        percentual_territorio = _percentual(realizado_total, meta_total)
        fase_inicial = meta_total <= 0 or realizado_total < meta_total * FRACAO_MINIMA_PARA_PRIORIDADE

        celulas = []
        for c in cotas:
            realizado = classificacao.realizado_por_cota.get(c.id, 0)
            meta = int(c.meta or 0)
            percentual = _percentual(realizado, meta)
            desvio = round(percentual - percentual_territorio, 2)
            celulas.append(
                {
                    "territorio_id": tid,
                    "territorio_nome": nomes.get(tid),
                    "sexo": c.sexo,
                    "sexo_rotulo": ROTULO_SEXO.get(c.sexo, c.sexo),
                    "faixa_etaria": c.faixa_rotulo,
                    "meta": meta,
                    "realizado": realizado,
                    "restante": max(meta - realizado, 0),
                    "percentual_atingimento": percentual,
                    "percentual_territorio": percentual_territorio,
                    "desvio_pp": desvio,
                    "prioridade": classificar_prioridade(desvio),
                    "_completa": meta <= 0 or realizado >= meta,
                }
            )
        prioridades = [
            c for c in celulas
            if not fase_inicial and not c["_completa"] and c["desvio_pp"] < 0
            and c["prioridade"] != schemas.PrioridadePerfil.EQUILIBRADO.value
        ]
        prioridades.sort(key=lambda c: (c["desvio_pp"], -c["restante"]))
        for c in celulas:
            c.pop("_completa", None)
        status = (
            STATUS_FASE_INICIAL if fase_inicial
            else STATUS_PRIORIDADES if prioridades
            else STATUS_EQUILIBRADO
        )
        territorios.append(
            {
                "territorio_id": tid,
                "territorio_nome": nomes.get(tid),
                "meta_total": meta_total,
                "realizado_total": realizado_total,
                "percentual_territorio": percentual_territorio,
                "fase_inicial": fase_inicial,
                "status": status,
                "celulas": celulas,
                "prioridades": prioridades,
            }
        )

    return {
        "pesquisa_id": pesquisa_id,
        "plano_ativo": bool(plano.ativo),
        "snapshot_em": datetime.now(timezone.utc),
        "nao_classificadas": classificacao.nao_classificadas,
        "motivos_nao_classificadas": classificacao.motivos,
        "territorios": territorios,
    }


def prioridades_para_missao(
    db: Session, pesquisa_id: int, municipio_ids: Iterable[int]
) -> dict:
    """Recorte da missao do agente: so os municipios dos seus setores.

    Retorna sempre as chaves (aditivo, cliente antigo ignora). Sem plano ativo
    -> prioridades vazias e `plano_cota_perfil_ativo: false`.
    """
    plano = (
        db.query(models.PlanoCotaPerfil)
        .filter(models.PlanoCotaPerfil.pesquisa_id == pesquisa_id, models.PlanoCotaPerfil.ativo.is_(True))
        .first()
    )
    vazio = {
        "plano_cota_perfil_ativo": False,
        "prioridades_perfil": [],
        "perfil_status_territorios": {},
        "prioridades_perfil_snapshot_em": None,
    }
    if plano is None or not plano.cotas:
        return vazio
    alvo = set(m for m in municipio_ids if m is not None)
    progresso = calcular_progresso(db, plano, pesquisa_id)
    prioridades = []
    status_por_territorio: Dict[str, str] = {}
    for territorio in progresso["territorios"]:
        if territorio["territorio_id"] not in alvo:
            continue
        status_por_territorio[str(territorio["territorio_id"])] = territorio["status"]
        prioridades.extend(territorio["prioridades"])
    prioridades.sort(key=lambda c: (c["desvio_pp"], -c["restante"]))
    return {
        "plano_cota_perfil_ativo": True,
        "prioridades_perfil": prioridades,
        "perfil_status_territorios": status_por_territorio,
        "prioridades_perfil_snapshot_em": progresso["snapshot_em"].isoformat(),
    }


# --- contexto territorial (ADR-035) -----------------------------------------


def contexto_territorial(db: Session, pesquisa_id: int) -> list[dict]:
    """Municipios que recebem coletas dos setores OPERACIONAIS da pesquisa.

    Cota territorial e setorial; cota de perfil e municipal. Aqui se mostra ao
    coordenador de onde virao as entrevistas de cada municipio e a meta
    territorial consolidada -- informacao, nunca constraint.
    """
    from pesquisa360.services import setor_municipio

    setores = (
        db.query(models.Setor.id, models.Setor.nome, models.Setor.meta, models.Setor.finalidade)
        .filter(models.Setor.pesquisa_id == pesquisa_id)
        .all()
    )
    operacionais = [s for s in setores if setor_municipio.eh_operacional(s.finalidade)]
    resolucoes = pergunta_territorio.resolver_municipios_setores(db, [s.id for s in operacionais])
    por_municipio: Dict[int, dict] = {}
    for s in operacionais:
        r = resolucoes.get(s.id)
        if r is None or not r.resolvido:
            continue
        item = por_municipio.setdefault(
            r.municipio.id,
            {"territorio_id": r.municipio.id, "territorio_nome": r.municipio.nome, "setores_operacionais": [], "meta_territorial": 0},
        )
        item["setores_operacionais"].append({"id": s.id, "nome": s.nome, "meta": int(s.meta or 0)})
        item["meta_territorial"] += int(s.meta or 0)
    return [por_municipio[k] for k in sorted(por_municipio)]
