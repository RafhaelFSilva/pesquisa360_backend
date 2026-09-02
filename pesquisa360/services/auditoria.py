"""Auditoria central de seguranca e acesso (ADR-039).

Responde "quem tentou entrar, quem conseguiu, quem foi barrado, quem acessou
qual projeto, de onde e quando". Nasce no Backend, no ponto em que a decisao e
tomada -- o Frontend nunca e a fonte de um evento de seguranca.

Tres decisoes sustentam o desenho:

1. **Sessao propria.** O evento e gravado numa sessao INDEPENDENTE da request.
   Um 403 faz a transacao da request morrer sem commit; se o evento estivesse
   nela, a negacao nao deixaria rastro -- justamente o caso que mais importa.

2. **Nunca derruba a request.** Falha ao auditar vira log de aviso, nao 500.
   Auditoria e testemunha, nao guarda: se o banco de auditoria cair, a API
   continua servindo e o problema aparece no log.

3. **Nunca guarda segredo.** Sem senha, token, `Authorization`, corpo de
   requisicao ou dado de entrevistado. `details` e um dicionario pequeno e
   nao sensivel, filtrado por chave.

O contexto HTTP (ip, user-agent, metodo, caminho, request_id) vem de um
ContextVar preenchido pelo middleware -- assim `registrar()` funciona de dentro
de qualquer dependency ou servico sem receber `Request` por parametro.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from pesquisa360.db import models

logger = logging.getLogger(__name__)

# --- tipos de evento ----------------------------------------------------------
LOGIN_SUCCESS = "LOGIN_SUCCESS"
LOGIN_FAILED = "LOGIN_FAILED"                    # credencial invalida ou conta inativa
TOKEN_EXPIRED = "TOKEN_EXPIRED"                  # assinatura valida, prazo vencido
TOKEN_INVALID = "TOKEN_INVALID"                  # assinatura/estrutura invalida ou tipo errado (refresh como access)
TOKEN_REJECTED = "TOKEN_REJECTED"                # token bom, mas usuario inexistente/inativo
ACCOUNT_INACTIVE_LOGIN = "ACCOUNT_INACTIVE_LOGIN"   # credencial correta, conta ou empresa inativa
RBAC_DENIED = "RBAC_DENIED"                      # 403: perfil sem a capacidade
ACCESS_DENIED = "ACCESS_DENIED"                  # 404: projeto do tenant sem ACL, ou inexistente
CROSS_TENANT_ACCESS_ATTEMPT = "CROSS_TENANT_ACCESS_ATTEMPT"   # 404: projeto de OUTRO tenant
PROJECT_ACCESS = "PROJECT_ACCESS"                # entrada logica no projeto (GET detalhe)
ACCOUNT_ACTIVATED = "ACCOUNT_ACTIVATED"
ACL_CHANGED = "ACL_CHANGED"
MODULE_ENTITLEMENT_CREATED = "MODULE_ENTITLEMENT_CREATED"
MODULE_ENTITLEMENT_UPDATED = "MODULE_ENTITLEMENT_UPDATED"
MODULE_ENTITLEMENT_FEATURES_UPDATED = "MODULE_ENTITLEMENT_FEATURES_UPDATED"
# ADR-040: resultado da notificacao ao Gerente responsavel, consequencia de um
# PROJECT_ACCESS efetivamente persistido (nunca de um GET deduplicado).
PROJECT_ACCESS_NOTIFICATION_SENT = "PROJECT_ACCESS_NOTIFICATION_SENT"
PROJECT_ACCESS_NOTIFICATION_FAILED = "PROJECT_ACCESS_NOTIFICATION_FAILED"
PROJECT_ACCESS_NOTIFICATION_SUPPRESSED = "PROJECT_ACCESS_NOTIFICATION_SUPPRESSED"

SEV_INFO = "INFO"
SEV_WARNING = "WARNING"
SEV_HIGH = "HIGH"

# Chaves que NUNCA entram em `details`, venham de onde vierem.
_CHAVES_PROIBIDAS = {
    "senha", "password", "passwd", "token", "access_token", "refresh_token",
    "authorization", "senha_hash", "token_hash", "secret", "cookie",
}
_TAMANHO_MAX_VALOR = 500

# PROJECT_ACCESS = "entrada logica no projeto", nao "cada GET". Dentro desta
# janela, o mesmo usuario no mesmo projeto nao gera evento novo.
JANELA_PROJECT_ACCESS = timedelta(minutes=15)
LIMITE_MAX_LISTAGEM = 100
LIMITE_PADRAO_LISTAGEM = 50


@dataclass
class ContextoRequest:
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    http_method: Optional[str] = None
    path: Optional[str] = None
    request_id: Optional[str] = None
    extras: dict = field(default_factory=dict)


_contexto: ContextVar[Optional[ContextoRequest]] = ContextVar("auditoria_contexto", default=None)

# Fabrica de sessao: em producao e a SessionLocal; testes trocam pela deles.
_fabrica_sessao: Optional[Callable[[], Session]] = None


def configurar_sessao(fabrica: Optional[Callable[[], Session]]) -> None:
    global _fabrica_sessao
    _fabrica_sessao = fabrica


def _abrir_sessao() -> Optional[Session]:
    if _fabrica_sessao is not None:
        return _fabrica_sessao()
    try:
        from pesquisa360.db.session import SessionLocal  # import tardio: evita ciclo e exige DATABASE_URL so aqui
    except Exception:
        return None
    return SessionLocal()


def definir_contexto(contexto: Optional[ContextoRequest]):
    return _contexto.set(contexto)


def limpar_contexto(token) -> None:
    try:
        _contexto.reset(token)
    except Exception:
        _contexto.set(None)


def contexto_atual() -> ContextoRequest:
    return _contexto.get() or ContextoRequest()


# --- extracao de IP -----------------------------------------------------------


def confiar_no_proxy() -> bool:
    """`X-Forwarded-For` so vale quando ha proxy confiavel declarado.

    Sem isso, qualquer cliente forjaria o proprio IP na auditoria. O uvicorn do
    projeto roda sem `--proxy-headers`, entao o default e NAO confiar.
    """
    return (os.getenv("AUDIT_TRUST_PROXY") or "").strip().casefold() in {"1", "true", "yes"}


def extrair_ip(headers, client_host: Optional[str]) -> Optional[str]:
    if confiar_no_proxy():
        encaminhado = headers.get("x-forwarded-for") if headers else None
        if encaminhado:
            # Primeiro endereco da cadeia = cliente original.
            return encaminhado.split(",")[0].strip()[:45] or None
    return (client_host or None) and str(client_host)[:45]


def contexto_de_request(request, request_id: Optional[str] = None) -> ContextoRequest:
    headers = request.headers
    return ContextoRequest(
        ip_address=extrair_ip(headers, getattr(request.client, "host", None) if request.client else None),
        user_agent=(headers.get("user-agent") or None) and headers.get("user-agent")[:512],
        http_method=request.method,
        path=str(request.url.path)[:512],
        request_id=request_id,
    )


# --- saneamento de details ---------------------------------------------------


def _sanear(valor: Any, profundidade: int = 0) -> Any:
    if profundidade > 3:
        return "..."
    if isinstance(valor, dict):
        return {
            str(k): _sanear(v, profundidade + 1)
            for k, v in valor.items()
            if str(k).casefold() not in _CHAVES_PROIBIDAS
        }
    if isinstance(valor, (list, tuple, set)):
        return [_sanear(v, profundidade + 1) for v in list(valor)[:50]]
    if isinstance(valor, (int, float, bool)) or valor is None:
        return valor
    texto = str(valor)
    return texto if len(texto) <= _TAMANHO_MAX_VALOR else texto[:_TAMANHO_MAX_VALOR] + "..."


# --- registro -----------------------------------------------------------------


def registrar(
    event_type: str,
    severity: str = SEV_INFO,
    *,
    user=None,
    user_id: Optional[int] = None,
    company_id: Optional[int] = None,
    project_id: Optional[int] = None,
    attempted_email: Optional[str] = None,
    status_code: Optional[int] = None,
    details: Optional[dict] = None,
    contexto: Optional[ContextoRequest] = None,
) -> bool:
    """Grava um evento. Devolve True se persistiu; False nunca propaga erro."""
    ctx = contexto or contexto_atual()
    if user is not None:
        user_id = user_id if user_id is not None else getattr(user, "id", None)
        company_id = company_id if company_id is not None else getattr(user, "company_id", None)

    sessao = None
    try:
        sessao = _abrir_sessao()
        if sessao is None:
            logger.warning("auditoria sem sessao: %s", event_type)
            return False
        sessao.add(
            models.AuditEvent(
                event_type=event_type,
                severity=severity,
                user_id=user_id,
                company_id=company_id,
                project_id=project_id,
                attempted_email=(attempted_email or None) and str(attempted_email)[:320],
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
                http_method=ctx.http_method,
                path=ctx.path,
                status_code=status_code,
                request_id=ctx.request_id,
                details=_sanear(details) if details else None,
            )
        )
        sessao.commit()
        return True
    except Exception as exc:  # noqa: BLE001 - auditoria nunca derruba a request
        try:
            if sessao is not None:
                sessao.rollback()
        except Exception:
            pass
        logger.warning("falha ao registrar auditoria %s: %s", event_type, type(exc).__name__)
        return False
    finally:
        try:
            if sessao is not None:
                sessao.close()
        except Exception:
            pass


def adicionar_evento(
    db: Session,
    event_type: str,
    *,
    user,
    company_id: int,
    details: dict,
    severity: str = SEV_INFO,
) -> models.AuditEvent:
    """Inclui auditoria na mesma transacao de uma mutacao administrativa."""
    ctx = contexto_atual()
    evento = models.AuditEvent(
        event_type=event_type,
        severity=severity,
        user_id=getattr(user, "id", None),
        company_id=company_id,
        ip_address=ctx.ip_address,
        user_agent=ctx.user_agent,
        http_method=ctx.http_method,
        path=ctx.path,
        request_id=ctx.request_id,
        details=_sanear(details),
    )
    db.add(evento)
    return evento


# --- classificacao de negacao de projeto --------------------------------------


def classificar_negacao_projeto(db: Session, current_user, projeto_id: int) -> str:
    """Motivo INTERNO de um 404 de projeto (o cliente continua vendo so 404).

    inexistente   -> nao ha projeto com esse id
    outro_tenant  -> existe, mas e de empresa que o usuario nao acessa
    sem_acl       -> existe no proprio tenant, mas o usuario nao tem ACL nele
    """
    projeto = db.query(models.Projeto.company_id).filter(models.Projeto.id == projeto_id).first()
    if projeto is None:
        return "inexistente"
    from pesquisa360.services import acessos  # import tardio: acessos importa auditoria

    if acessos.usuario_tem_acesso_empresa(db, current_user, projeto[0]):
        return "sem_acl"
    return "outro_tenant"


def registrar_negacao_projeto(db: Session, current_user, projeto_id: int) -> str:
    motivo = classificar_negacao_projeto(db, current_user, projeto_id)
    if motivo == "outro_tenant":
        registrar(
            CROSS_TENANT_ACCESS_ATTEMPT, SEV_HIGH, user=current_user, project_id=projeto_id,
            status_code=404, details={"motivo": motivo},
        )
    else:
        registrar(
            ACCESS_DENIED, SEV_WARNING, user=current_user,
            project_id=projeto_id if motivo != "inexistente" else None,
            status_code=404, details={"motivo": motivo, "projeto_id": projeto_id},
        )
    return motivo


# --- entrada logica no projeto ------------------------------------------------


def _utc(valor: Optional[datetime]) -> Optional[datetime]:
    """Normaliza para UTC aware. Naive (SQLite devolve assim) e tratado como UTC."""
    if valor is None:
        return None
    return valor.replace(tzinfo=timezone.utc) if valor.tzinfo is None else valor.astimezone(timezone.utc)


def ultimo_acesso_projeto(user_id: int, project_id: int) -> Optional[datetime]:
    """occurred_at do PROJECT_ACCESS mais recente de (usuario, projeto), ou None.

    Consulta coberta pelo indice composto (event_type, user_id, project_id,
    occurred_at) da migration a4b5c6d7e8f9. Le na sessao de auditoria.
    """
    sessao = None
    try:
        sessao = _abrir_sessao()
        if sessao is None:
            return None
        valor = (
            sessao.query(models.AuditEvent.occurred_at)
            .filter(
                models.AuditEvent.event_type == PROJECT_ACCESS,
                models.AuditEvent.user_id == user_id,
                models.AuditEvent.project_id == project_id,
            )
            .order_by(models.AuditEvent.occurred_at.desc())
            .limit(1)
            .scalar()
        )
        return _utc(valor)
    except Exception as exc:  # noqa: BLE001
        logger.warning("falha ao consultar ultimo PROJECT_ACCESS: %s", type(exc).__name__)
        return None
    finally:
        try:
            if sessao is not None:
                sessao.close()
        except Exception:
            pass


def registrar_acesso_projeto(user, projeto, *, status_code: int = 200, agora: Optional[datetime] = None) -> bool:
    """Registra a entrada logica no projeto, deduplicada por 15 minutos.

    Chave: (PROJECT_ACCESS, user_id, project_id). `company_id` do evento e o
    tenant do PROJETO -- para um Superadmin em projeto de outra empresa, o que
    importa e "qual tenant foi acessado", nao a empresa nominal do ator.
    Devolve True quando um evento novo foi gravado.
    """
    user_id = getattr(user, "id", None)
    project_id = getattr(projeto, "id", None)
    if user_id is None or project_id is None:
        return False
    ultimo = ultimo_acesso_projeto(user_id, project_id)
    if ultimo is not None and (agora or datetime.now(timezone.utc)) - ultimo < JANELA_PROJECT_ACCESS:
        return False
    return registrar(
        PROJECT_ACCESS, SEV_INFO, user=user, user_id=user_id,
        company_id=getattr(projeto, "company_id", None), project_id=project_id,
        status_code=status_code,
    )


# --- consulta -----------------------------------------------------------------


def listar_eventos(
    db: Session,
    *,
    event_type: Optional[str] = None,
    severity: Optional[str] = None,
    user_id: Optional[int] = None,
    project_id: Optional[int] = None,
    company_id: Optional[int] = None,
    ip_address: Optional[str] = None,
    data_inicio: Optional[datetime] = None,
    data_fim: Optional[datetime] = None,
    limit: int = LIMITE_PADRAO_LISTAGEM,
    offset: int = 0,
):
    """Devolve (itens, total). Mais recente primeiro (occurred_at DESC, id DESC).

    `limit` e sempre limitado a LIMITE_MAX_LISTAGEM: a trilha cresce sem parar
    e uma pagina de 500 eventos so serve para derrubar o painel.
    """
    query = db.query(models.AuditEvent)
    if event_type:
        query = query.filter(models.AuditEvent.event_type == event_type)
    if severity:
        query = query.filter(models.AuditEvent.severity == severity)
    if user_id is not None:
        query = query.filter(models.AuditEvent.user_id == user_id)
    if project_id is not None:
        query = query.filter(models.AuditEvent.project_id == project_id)
    if company_id is not None:
        query = query.filter(models.AuditEvent.company_id == company_id)
    if ip_address:
        query = query.filter(models.AuditEvent.ip_address == ip_address.strip()[:45])
    if data_inicio is not None:
        query = query.filter(models.AuditEvent.occurred_at >= _utc(data_inicio))
    if data_fim is not None:
        query = query.filter(models.AuditEvent.occurred_at <= _utc(data_fim))
    total = query.order_by(None).count()
    itens = (
        query.order_by(models.AuditEvent.occurred_at.desc(), models.AuditEvent.id.desc())
        .offset(max(offset, 0))
        .limit(max(1, min(limit, LIMITE_MAX_LISTAGEM)))
        .all()
    )
    return itens, total


# --- painel de seguranca (ADR-041) --------------------------------------------

# Eventos que contam como "seguranca" no ranking de IPs e na serie temporal.
# LOGIN_SUCCESS, PROJECT_ACCESS e NOTIFICATION_* sao operacao normal: nao
# entram num ranking de comportamento incomum.
EVENTOS_LOGIN_FALHO = (LOGIN_FAILED, ACCOUNT_INACTIVE_LOGIN)
EVENTOS_NEGADOS = (RBAC_DENIED, ACCESS_DENIED)
EVENTOS_TOKEN = (TOKEN_INVALID, TOKEN_EXPIRED, TOKEN_REJECTED)
EVENTOS_SEGURANCA = EVENTOS_LOGIN_FALHO + EVENTOS_TOKEN + EVENTOS_NEGADOS + (CROSS_TENANT_ACCESS_ATTEMPT,)
LIMITE_RANKING = 10
JANELA_PADRAO_RESUMO = timedelta(hours=24)
LIMITE_GRANULARIDADE_HORA = timedelta(hours=48)


def _contagem(condicao):
    """SUM(CASE WHEN cond THEN 1 ELSE 0 END) -- portavel entre PostgreSQL e SQLite."""
    return func.coalesce(func.sum(case((condicao, 1), else_=0)), 0)


def _filtros_resumo(query, *, company_id, project_id, user_id, data_inicio, data_fim):
    E = models.AuditEvent
    if company_id is not None:
        query = query.filter(E.company_id == company_id)
    if project_id is not None:
        query = query.filter(E.project_id == project_id)
    if user_id is not None:
        query = query.filter(E.user_id == user_id)
    return query.filter(E.occurred_at >= data_inicio, E.occurred_at <= data_fim)


def _bucket_expr(db: Session, granularidade: str):
    """Expressao SQL que trunca occurred_at por hora/dia, no dialeto em uso."""
    E = models.AuditEvent
    dialeto = getattr(getattr(db, "bind", None), "dialect", None)
    nome = getattr(dialeto, "name", "")
    if nome == "postgresql":
        return func.to_char(
            func.date_trunc("hour" if granularidade == "hora" else "day", E.occurred_at),
            'YYYY-MM-DD"T"HH24:MI:SS',
        )
    formato = "%Y-%m-%dT%H:00:00" if granularidade == "hora" else "%Y-%m-%dT00:00:00"
    return func.strftime(formato, E.occurred_at)


def resumo_seguranca(
    db: Session,
    *,
    company_id: Optional[int] = None,
    project_id: Optional[int] = None,
    user_id: Optional[int] = None,
    data_inicio: Optional[datetime] = None,
    data_fim: Optional[datetime] = None,
) -> dict:
    """Agregacoes do painel de seguranca. Tudo em SQL: COUNT/SUM(CASE)/GROUP BY.

    Nunca carrega AuditEvent em Python. `company_id` aqui ja e o escopo
    EFETIVO decidido pelo endpoint (Gerente = proprio tenant): eventos sem
    company_id ficam automaticamente fora da visao gerencial.
    """
    E = models.AuditEvent
    fim = _utc(data_fim) or datetime.now(timezone.utc)
    inicio = _utc(data_inicio) or (fim - JANELA_PADRAO_RESUMO)
    filtros = dict(company_id=company_id, project_id=project_id, user_id=user_id, data_inicio=inicio, data_fim=fim)

    totais_q = _filtros_resumo(
        db.query(
            func.count(E.id).label("total"),
            _contagem(E.event_type.in_(EVENTOS_LOGIN_FALHO)).label("login_failed"),
            _contagem(E.event_type.in_(EVENTOS_NEGADOS)).label("access_denied"),
            _contagem(E.event_type == CROSS_TENANT_ACCESS_ATTEMPT).label("cross_tenant"),
            _contagem(E.event_type == PROJECT_ACCESS).label("project_access"),
            _contagem(E.event_type == PROJECT_ACCESS_NOTIFICATION_FAILED).label("notification_failed"),
            _contagem(E.severity == SEV_HIGH).label("high"),
        ),
        **filtros,
    )
    t = totais_q.one()
    totais = {
        "total_eventos": int(t.total or 0),
        "login_failed": int(t.login_failed), "access_denied": int(t.access_denied),
        "cross_tenant": int(t.cross_tenant), "project_access": int(t.project_access),
        "notification_failed": int(t.notification_failed), "high": int(t.high),
    }

    ips_q = _filtros_resumo(
        db.query(
            E.ip_address.label("ip_address"),
            func.count(E.id).label("total"),
            _contagem(E.event_type.in_(EVENTOS_LOGIN_FALHO)).label("login_failed"),
            _contagem(E.event_type.in_(EVENTOS_NEGADOS)).label("access_denied"),
            _contagem(E.event_type == CROSS_TENANT_ACCESS_ATTEMPT).label("cross_tenant"),
            func.max(E.occurred_at).label("last_event_at"),
        ).filter(E.ip_address.isnot(None), E.event_type.in_(EVENTOS_SEGURANCA)),
        **filtros,
    ).group_by(E.ip_address).order_by(func.count(E.id).desc(), func.max(E.occurred_at).desc()).limit(LIMITE_RANKING)
    top_ips = [
        {"ip_address": r.ip_address, "total": int(r.total), "login_failed": int(r.login_failed),
         "access_denied": int(r.access_denied), "cross_tenant": int(r.cross_tenant), "last_event_at": _utc(r.last_event_at)}
        for r in ips_q.all()
    ]

    contas_q = _filtros_resumo(
        db.query(
            E.attempted_email.label("attempted_email"),
            func.count(E.id).label("total"),
            func.max(E.occurred_at).label("last_event_at"),
        ).filter(E.attempted_email.isnot(None), E.event_type.in_(EVENTOS_LOGIN_FALHO)),
        **filtros,
    ).group_by(E.attempted_email).order_by(func.count(E.id).desc(), func.max(E.occurred_at).desc()).limit(LIMITE_RANKING)
    top_contas = [
        {"attempted_email": r.attempted_email, "total": int(r.total), "last_event_at": _utc(r.last_event_at)}
        for r in contas_q.all()
    ]

    granularidade = "hora" if (fim - inicio) <= LIMITE_GRANULARIDADE_HORA else "dia"
    bucket = _bucket_expr(db, granularidade)
    serie_q = _filtros_resumo(
        db.query(
            bucket.label("periodo"),
            _contagem(E.event_type.in_(EVENTOS_LOGIN_FALHO)).label("login_failed"),
            _contagem(E.event_type.in_(EVENTOS_NEGADOS)).label("access_denied"),
            _contagem(E.event_type == CROSS_TENANT_ACCESS_ATTEMPT).label("cross_tenant"),
        ).filter(E.event_type.in_(EVENTOS_SEGURANCA)),
        **filtros,
    ).group_by(bucket).order_by(bucket)
    timeline = [
        {"periodo": str(r.periodo), "login_failed": int(r.login_failed),
         "access_denied": int(r.access_denied), "cross_tenant": int(r.cross_tenant)}
        for r in serie_q.all()
    ]

    return {
        "periodo": {"data_inicio": inicio, "data_fim": fim},
        "granularidade": granularidade,
        "totais": totais,
        "top_ips": top_ips,
        "top_attempted_accounts": top_contas,
        "timeline": timeline,
    }
