"""Notificacao ao Gerente responsavel quando um projeto e acessado (ADR-040).

A notificacao e CONSEQUENCIA de um `PROJECT_ACCESS` efetivamente persistido
(ADR-039), nunca de uma requisicao HTTP em si. Quem controla a frequencia e a
deduplicacao de 15 minutos do proprio evento: um GET deduplicado nao chega
aqui. Este modulo nao tem janela propria, nem retry, nem fila.

Destinatario: `Projeto.coordenador_id`. Apesar do nome legado, a coluna e o
vinculo formal de RESPONSAVEL pelo projeto -- `crud.validate_project_coordinator`
so aceita usuario ativo do mesmo tenant com perfil Gerente ou Superadmin, e o
perfil Coordenador (ADR-037) nao e admitido nela. Nao existe, portanto,
escolha arbitraria de "algum Gerente do tenant".

Regras:

- ator == destinatario           -> SUPPRESSED (self_access)
- projeto sem responsavel        -> SUPPRESSED (no_project_manager)
- responsavel inativo            -> SUPPRESSED (inactive_recipient)
- responsavel sem e-mail         -> SUPPRESSED (missing_recipient_email)
- SMTP nao configurado           -> SUPPRESSED (smtp_not_configured)
- SMTP recusou / excecao         -> FAILED  (WARNING)
- entregue ao SMTP               -> SENT

Superadmin acessando o projeto NOTIFICA: e justamente o acesso administrativo
extraordinario que o Gerente quer saber.

Nada aqui derruba o GET do projeto: falha de e-mail e log + evento de
auditoria, nunca 500. O envio e sincrono e fail-soft, como o convite (ADR-035):
sem worker/fila no projeto, `BackgroundTasks` so adicionaria um ciclo de vida
de sessao a mais para ganhar poucos milissegundos.

O e-mail NUNCA contem token, senha, Authorization, corpo de requisicao ou
dado de pesquisa/entrevistado: apenas projeto, ator, perfil, empresa do
projeto, data/hora, IP e User-Agent ja normalizados pela auditoria.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from pesquisa360.core import rbac
from pesquisa360.services import auditoria
from pesquisa360.services import email as servico_email

logger = logging.getLogger(__name__)

_TAMANHO_MAX_USER_AGENT = 160
_TAMANHO_MAX_NOME = 120


@dataclass(frozen=True)
class ResultadoNotificacao:
    status: str                     # "SENT" | "FAILED" | "SUPPRESSED"
    reason: Optional[str] = None
    recipient_user_id: Optional[int] = None


def _texto(valor, limite: int = _TAMANHO_MAX_NOME) -> str:
    texto = (str(valor) if valor is not None else "").strip()
    return texto if len(texto) <= limite else texto[:limite] + "..."


def _formatar_data_hora(momento: datetime) -> str:
    """DD/MM/AAAA HH:mm em UTC, explicitado no texto.

    O projeto nao possui timezone operacional configurada; converter para uma
    zona arbitraria no Backend mentiria para implantacoes fora dela.
    """
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M") + " (UTC)"


def montar_mensagem(
    *,
    nome_destinatario: str,
    nome_projeto: str,
    projeto_id: int,
    nome_ator: str,
    perfil_ator: str,
    nome_empresa: str,
    momento: datetime,
    ip_address: Optional[str],
    user_agent: Optional[str],
) -> tuple[str, str]:
    """Devolve (assunto, corpo). Funcao pura: facil de testar e de auditar."""
    assunto = f"Acesso ao projeto — {_texto(nome_projeto)}"
    corpo = "\n".join(
        [
            f"Olá, {_texto(nome_destinatario) or 'Gerente'}.",
            "",
            "Registramos um acesso ao projeto:",
            "",
            f"Projeto: {_texto(nome_projeto)}",
            "",
            "Usuário:",
            _texto(nome_ator) or "(sem nome)",
            "",
            "Perfil:",
            _texto(perfil_ator) or "(não informado)",
            "",
            "Empresa (tenant do projeto):",
            _texto(nome_empresa) or "(não informada)",
            "",
            "Data e hora:",
            _formatar_data_hora(momento),
            "",
            "IP:",
            _texto(ip_address, 45) or "(não disponível)",
            "",
            "Dispositivo/Navegador:",
            _texto(user_agent, _TAMANHO_MAX_USER_AGENT) or "(não disponível)",
            "",
            "Abrir projeto:",
            f"{servico_email.web_base_url()}/projetos/{int(projeto_id)}",
            "",
            "Este é um aviso automático de segurança do Pesquisa360.",
        ]
    )
    return assunto, corpo


def resolver_destinatario(projeto) -> tuple[Optional[object], Optional[str]]:
    """(usuario, motivo_de_supressao). Usuario so quando pode receber."""
    try:
        responsavel = getattr(projeto, "coordenador", None)
    except Exception:  # noqa: BLE001 - sessao fechada, FK pendente...
        responsavel = None
    if responsavel is None:
        return None, "no_project_manager"
    if getattr(responsavel, "ativo", None) is not True:
        return responsavel, "inactive_recipient"
    email = (getattr(responsavel, "email", None) or "").strip()
    if not email or "@" not in email:
        return responsavel, "missing_recipient_email"
    return responsavel, None


def _auditar(status: str, reason: Optional[str], *, ator, projeto, recipient_user_id: Optional[int]) -> None:
    tipo, severidade = {
        "SENT": (auditoria.PROJECT_ACCESS_NOTIFICATION_SENT, auditoria.SEV_INFO),
        "SUPPRESSED": (auditoria.PROJECT_ACCESS_NOTIFICATION_SUPPRESSED, auditoria.SEV_INFO),
    }.get(status, (auditoria.PROJECT_ACCESS_NOTIFICATION_FAILED, auditoria.SEV_WARNING))
    details = {"recipient_user_id": recipient_user_id}
    if reason:
        details["reason"] = reason
    auditoria.registrar(
        tipo, severidade, user=ator, user_id=getattr(ator, "id", None),
        company_id=getattr(projeto, "company_id", None), project_id=getattr(projeto, "id", None),
        details=details,
    )


def notificar_acesso_projeto(ator, projeto, *, momento: Optional[datetime] = None) -> ResultadoNotificacao:
    """Chamar SOMENTE quando `registrar_acesso_projeto` devolveu True.

    Nunca propaga excecao: o resultado vira evento de auditoria
    (SENT / FAILED / SUPPRESSED) e log. Devolve o resultado para testes.
    """
    resultado: ResultadoNotificacao
    destinatario_id: Optional[int] = None
    try:
        destinatario, motivo = resolver_destinatario(projeto)
        destinatario_id = getattr(destinatario, "id", None)
        if motivo is None and destinatario_id is not None and destinatario_id == getattr(ator, "id", None):
            motivo = "self_access"
        if motivo is None and not servico_email.ConfiguracaoEmail().habilitado:
            motivo = "smtp_not_configured"
        if motivo is not None:
            resultado = ResultadoNotificacao("SUPPRESSED", motivo, destinatario_id)
        else:
            ctx = auditoria.contexto_atual()
            empresa = getattr(projeto, "company", None)
            assunto, corpo = montar_mensagem(
                nome_destinatario=getattr(destinatario, "nome", None) or "",
                nome_projeto=getattr(projeto, "nome", None) or f"#{getattr(projeto, 'id', '')}",
                projeto_id=getattr(projeto, "id", 0) or 0,
                nome_ator=getattr(ator, "nome", None) or getattr(ator, "email", None) or "",
                perfil_ator=rbac.papel_nome(ator) or getattr(ator, "perfil_nome", None) or "",
                nome_empresa=getattr(empresa, "name", None) or "",
                momento=momento or datetime.now(timezone.utc),
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
            )
            enviado = servico_email.enviar_email(destinatario.email, assunto, corpo)
            resultado = (
                ResultadoNotificacao("SENT", None, destinatario_id)
                if enviado
                else ResultadoNotificacao("FAILED", "smtp_error", destinatario_id)
            )
    except Exception as exc:  # noqa: BLE001 - notificacao nunca derruba o acesso
        logger.warning("falha ao notificar acesso ao projeto: %s", type(exc).__name__)
        resultado = ResultadoNotificacao("FAILED", "exception:" + type(exc).__name__, destinatario_id)

    _auditar(resultado.status, resultado.reason, ator=ator, projeto=projeto, recipient_user_id=resultado.recipient_user_id)
    return resultado
