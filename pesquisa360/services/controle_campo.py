"""Painel Web consolidado de Controle de Campo (PROMPT 07).

Supervisao/coordenacao: reune, para o tenant e a pesquisa inteira,
territorio (motor oficial da cota), perfil (services.cota_perfil),
abordagens (tentativas_campo, GROUP BY resultado), entrevistas (coletas) e
atividade espacial (services.cobertura_campo). Nao recalcula nenhum motor;
apenas agrega e aplica filtros de forma previsivel:

    Bloco                     municipio  setor  agente  periodo
    resumo/tentativas/mapa    sim        sim    sim     sim
    cota territorial oficial  sim        sim    NAO     NAO
    cota de perfil            sim        (via territorio) NAO NAO

Snapshot do servidor: nada aqui e tempo real.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, List, Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from pesquisa360 import crud, schemas
from pesquisa360.db import models
from pesquisa360.services import acessos
from pesquisa360.services import cobertura_campo, cota_perfil, pergunta_territorio

RESULTADOS_ENCERRADOS = [
    r.value for r in schemas.TentativaResultado if r != schemas.TentativaResultado.EM_ANDAMENTO
]
ORDEM_STATUS_COTA = {"ATENCAO": 0, "ABERTO": 1, "ENCERRADO": 2, "SEM_COTA": 3}
ORDEM_PRIORIDADE = {"ALTO": 0, "MEDIO": 1, "BAIXO": 2, "EQUILIBRADO": 3}


@dataclass
class FiltrosControleCampo:
    municipio_id: Optional[int] = None
    setor_ids: List[int] = field(default_factory=list)
    agente_ids: List[int] = field(default_factory=list)
    data_inicio: Optional[datetime] = None
    data_fim: Optional[datetime] = None
    resultado: Optional[str] = None

    @property
    def tem_periodo(self) -> bool:
        return self.data_inicio is not None or self.data_fim is not None


def _pesquisa_do_tenant(db: Session, projeto_id: int, pesquisa_id: int, current_user) -> models.Pesquisa:
    pesquisa = crud.get_pesquisa(db=db, pesquisa_id=pesquisa_id, current_user=current_user)
    if pesquisa is None or pesquisa.projeto_id != projeto_id:
        raise HTTPException(status_code=404, detail="Pesquisa nao encontrada ou acesso negado.")
    return pesquisa


def _validar_agentes(db: Session, agente_ids: List[int], current_user) -> None:
    if not agente_ids:
        return
    encontrados = (
        db.query(func.count(models.Usuario.id))
        .filter(models.Usuario.id.in_(agente_ids), acessos.filtro_usuario_visivel(current_user))
        .scalar()
    )
    if encontrados != len(set(agente_ids)):
        raise HTTPException(status_code=404, detail="Agente nao encontrado para este tenant.")


def _aplicar_periodo(query, coluna, filtros: FiltrosControleCampo):
    if filtros.data_inicio is not None:
        query = query.filter(coluna >= filtros.data_inicio)
    if filtros.data_fim is not None:
        query = query.filter(coluna <= filtros.data_fim)
    return query


def montar_painel(
    db: Session, projeto_id: int, pesquisa_id: int, current_user, filtros: FiltrosControleCampo
) -> dict:
    _pesquisa_do_tenant(db, projeto_id, pesquisa_id, current_user)
    _validar_agentes(db, filtros.agente_ids, current_user)

    # --- Territorio: todos os setores da pesquisa (tenant ja validado) --------
    setores = (
        db.query(models.Setor)
        .filter(models.Setor.pesquisa_id == pesquisa_id)
        .order_by(models.Setor.id.asc())
        .all()
    )
    if filtros.setor_ids:
        ids_validos = {s.id for s in setores}
        if not set(filtros.setor_ids).issubset(ids_validos):
            raise HTTPException(status_code=404, detail="Setor nao encontrado para esta pesquisa.")
    resolucoes = pergunta_territorio.resolver_municipios_setores(db, [s.id for s in setores])

    def municipio_do_setor(setor_id: int):
        r = resolucoes.get(setor_id)
        return r.municipio if r is not None and r.resolvido else None

    setores_escopo = [
        s for s in setores
        if (not filtros.setor_ids or s.id in filtros.setor_ids)
        and (
            filtros.municipio_id is None
            or (municipio_do_setor(s.id) is not None and municipio_do_setor(s.id).id == filtros.municipio_id)
        )
    ]
    setor_ids_escopo = [s.id for s in setores_escopo]
    escopo_restrito = bool(filtros.setor_ids) or filtros.municipio_id is not None

    # Motor oficial (COUNT+MAX por setor, status, limite, agentes vinculados).
    progressos = crud.obter_progressos_setores(db, setores_escopo, pesquisa_id=pesquisa_id)
    setores_payload = []
    resumo_territorial = {"abertos": 0, "atencao": 0, "encerrados": 0, "sem_cota": 0, "com_excedente": 0}
    for setor in setores_escopo:
        p = progressos[setor.id]
        municipio = municipio_do_setor(setor.id)
        status = p["status_cota"]
        chave = {"ABERTO": "abertos", "ATENCAO": "atencao", "ENCERRADO": "encerrados"}.get(status, "sem_cota")
        resumo_territorial[chave] += 1
        if p["excedente"] > 0:
            resumo_territorial["com_excedente"] += 1
        setores_payload.append(
            {
                "setor_id": setor.id,
                "setor_nome": setor.nome,
                "finalidade": setor.finalidade,
                "municipio_id": municipio.id if municipio else None,
                "municipio_nome": municipio.nome if municipio else None,
                "meta": p["meta"],
                "realizado": p["realizado"],
                "restante": p["restante"],
                "excedente": p["excedente"],
                "percentual_atingimento": p["percentual_atingimento"],
                "status_cota": status,
                "limite_atencao_realizado": p["limite_atencao_realizado"],
                "agentes_atribuidos_total": p["agentes_atribuidos_total"],
                "snapshot_ate_coleta_id": p["snapshot_ate_coleta_id"],
            }
        )
    setores_payload.sort(
        key=lambda s: (ORDEM_STATUS_COTA.get(s["status_cota"], 9), -(s["percentual_atingimento"] or 0), s["setor_id"])
    )

    # --- Entrevistas (coletas) --------------------------------------------------
    coletas_q = db.query(func.count(models.Coleta.id)).filter(models.Coleta.pesquisa_id == pesquisa_id)
    if escopo_restrito:
        coletas_q = coletas_q.filter(models.Coleta.setor_id.in_(setor_ids_escopo or [-1]))
    if filtros.agente_ids:
        coletas_q = coletas_q.filter(models.Coleta.agente_id.in_(filtros.agente_ids))
    coletas_q = _aplicar_periodo(coletas_q, models.Coleta.data_inicio_coleta, filtros)
    entrevistas = int(coletas_q.scalar() or 0)

    # --- Abordagens (tentativas) GROUP BY resultado ----------------------------
    tent_q = db.query(models.TentativaCampo.resultado, func.count(models.TentativaCampo.id)).filter(
        models.TentativaCampo.pesquisa_id == pesquisa_id
    )
    if escopo_restrito:
        tent_q = tent_q.filter(models.TentativaCampo.setor_id.in_(setor_ids_escopo or [-1]))
    if filtros.agente_ids:
        tent_q = tent_q.filter(models.TentativaCampo.agente_id.in_(filtros.agente_ids))
    tent_q = _aplicar_periodo(tent_q, models.TentativaCampo.iniciada_em, filtros)
    por_resultado = {r: 0 for r in [*RESULTADOS_ENCERRADOS, schemas.TentativaResultado.EM_ANDAMENTO.value]}
    for resultado, total in tent_q.group_by(models.TentativaCampo.resultado).all():
        por_resultado[resultado] = int(total)
    encerradas = sum(por_resultado[r] for r in RESULTADOS_ENCERRADOS)
    concluidas = por_resultado["CONCLUIDA"]

    # Coletas COM tentativa vinculada (mesmo escopo/filtros das coletas).
    vinc_q = db.query(func.count(models.TentativaCampo.coleta_id)).join(
        models.Coleta, models.Coleta.id == models.TentativaCampo.coleta_id
    ).filter(models.Coleta.pesquisa_id == pesquisa_id)
    if escopo_restrito:
        vinc_q = vinc_q.filter(models.Coleta.setor_id.in_(setor_ids_escopo or [-1]))
    if filtros.agente_ids:
        vinc_q = vinc_q.filter(models.Coleta.agente_id.in_(filtros.agente_ids))
    vinc_q = _aplicar_periodo(vinc_q, models.Coleta.data_inicio_coleta, filtros)
    coletas_com_tentativa = int(vinc_q.scalar() or 0)

    resumo = {
        "meta_territorial": sum(s["meta"] for s in setores_payload),
        "realizado_territorial": sum(s["realizado"] for s in setores_payload),
        "entrevistas_concluidas": entrevistas,
        "coletas_com_tentativa": coletas_com_tentativa,
        "coletas_sem_tentativa": max(entrevistas - coletas_com_tentativa, 0),
        "tentativas_encerradas": encerradas,
        "tentativas_em_andamento": por_resultado["EM_ANDAMENTO"],
        "tentativas_concluidas": concluidas,
        "recusas": por_resultado["RECUSA"],
        "nao_elegiveis": por_resultado["NAO_ELEGIVEL"],
        "desistencias": por_resultado["DESISTENCIA"],
        "incompletas": por_resultado["INCOMPLETA"],
        "problemas_tecnicos": por_resultado["PROBLEMA_TECNICO"],
        "outros": por_resultado["OUTRO"],
        # Base explicita: so o universo com TentativaCampo. Coletas legadas
        # (sem tentativa) contam como entrevistas, nunca na taxa.
        "taxa_conclusao_tentativas": round(concluidas * 100.0 / encerradas, 2) if encerradas else None,
        "nota_taxa": (
            "taxa_conclusao_tentativas = tentativas CONCLUIDA / tentativas encerradas; "
            "coletas sem TentativaCampo (legadas) entram em entrevistas_concluidas mas nao na taxa."
        ),
    }
    tentativas_por_resultado = [
        {"resultado": r, "total": por_resultado[r]} for r in RESULTADOS_ENCERRADOS
    ]

    # --- Perfil (motor oficial), recortado aos municipios em escopo ------------
    municipios_escopo = {m.id for m in (municipio_do_setor(s.id) for s in setores_escopo) if m is not None}
    plano = (
        db.query(models.PlanoCotaPerfil)
        .filter(models.PlanoCotaPerfil.pesquisa_id == pesquisa_id, models.PlanoCotaPerfil.ativo.is_(True))
        .first()
    )
    cotas_perfil = {"plano_ativo": False, "territorios": [], "nao_classificadas": 0, "motivos_nao_classificadas": {}, "snapshot_em": None}
    if plano is not None and plano.cotas:
        progresso = cota_perfil.calcular_progresso(db, plano, pesquisa_id)
        territorios = [
            t for t in progresso["territorios"]
            if not escopo_restrito or t["territorio_id"] in municipios_escopo
        ]
        for t in territorios:
            t["celulas"].sort(key=lambda c: (ORDEM_PRIORIDADE.get(c["prioridade"], 9), c["desvio_pp"]))
            t.pop("prioridades", None)
        cotas_perfil = {
            "plano_ativo": True,
            "territorios": territorios,
            "nao_classificadas": progresso["nao_classificadas"],
            "motivos_nao_classificadas": progresso["motivos_nao_classificadas"],
            "snapshot_em": progresso["snapshot_em"],
        }

    # --- Atividade espacial (mesma regra/deduplicacao do agente) --------------
    eventos = cobertura_campo.eventos_cobertura(db, pesquisa_id, setores_escopo, incluir_agente=True)
    if filtros.agente_ids:
        eventos = [e for e in eventos if e.get("agente_id") in filtros.agente_ids]
    if filtros.tem_periodo:
        def _no_periodo(e):
            t = e.get("ocorrido_em")
            if t is None:
                return False
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            ini = filtros.data_inicio
            fim = filtros.data_fim
            if ini is not None and ini.tzinfo is None:
                ini = ini.replace(tzinfo=timezone.utc)
            if fim is not None and fim.tzinfo is None:
                fim = fim.replace(tzinfo=timezone.utc)
            return (ini is None or t >= ini) and (fim is None or t <= fim)
        eventos = [e for e in eventos if _no_periodo(e)]
    if filtros.resultado:
        eventos = [e for e in eventos if e["tipo"] == "TENTATIVA" and e.get("resultado") == filtros.resultado]
    distancia, configurada = cobertura_campo.distancia_recomendada(db, pesquisa_id)

    # --- Alertas derivados (sem motor proprio) --------------------------------
    alertas = []
    if resumo_territorial["atencao"]:
        alertas.append({"tipo": "SETORES_ATENCAO", "total": resumo_territorial["atencao"],
                        "mensagem": f"{resumo_territorial['atencao']} setor(es) próximo(s) da meta."})
    if resumo_territorial["com_excedente"]:
        alertas.append({"tipo": "SETORES_EXCEDENTE", "total": resumo_territorial["com_excedente"],
                        "mensagem": f"{resumo_territorial['com_excedente']} setor(es) com excedente acima da meta."})
    if cotas_perfil["nao_classificadas"]:
        alertas.append({"tipo": "PERFIL_NAO_CLASSIFICADAS", "total": cotas_perfil["nao_classificadas"],
                        "mensagem": f"{cotas_perfil['nao_classificadas']} coleta(s) não classificada(s) em cotas de perfil."})

    municipios = sorted(
        {(m.id, m.nome) for m in (municipio_do_setor(s.id) for s in setores) if m is not None},
        key=lambda x: x[1],
    )
    agentes = (
        db.query(models.Usuario.id, models.Usuario.nome)
        .join(models.SetorAgente, models.SetorAgente.agente_id == models.Usuario.id)
        .join(models.Setor, models.Setor.id == models.SetorAgente.setor_id)
        .filter(models.Setor.pesquisa_id == pesquisa_id, acessos.filtro_usuario_visivel(current_user))
        .distinct()
        .all()
    )

    return {
        "pesquisa_id": pesquisa_id,
        "snapshot_em": datetime.now(timezone.utc),
        "filtros": {
            "municipio_id": filtros.municipio_id,
            "setor_ids": filtros.setor_ids,
            "agente_ids": filtros.agente_ids,
            "data_inicio": filtros.data_inicio,
            "data_fim": filtros.data_fim,
            "resultado": filtros.resultado,
            "nota": "Cota territorial e cota de perfil sao oficiais e acumuladas: nao respondem a agente/periodo.",
        },
        "opcoes": {
            "municipios": [{"id": i, "nome": n} for i, n in municipios],
            "setores": [{"id": s.id, "nome": s.nome} for s in setores],
            "agentes": [{"id": i, "nome": n} for i, n in sorted(agentes, key=lambda a: a[1] or "")],
        },
        "resumo": resumo,
        "resumo_territorial": resumo_territorial,
        "alertas": alertas,
        "setores": setores_payload,
        "cotas_perfil": cotas_perfil,
        "tentativas_por_resultado": tentativas_por_resultado,
        "atividade_campo": {
            "distancia_recomendada_entre_abordagens_metros": distancia,
            "distancia_configurada": configurada,
            "eventos": eventos,
        },
    }
