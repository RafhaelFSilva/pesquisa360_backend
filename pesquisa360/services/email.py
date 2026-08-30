"""Envio de e-mail transacional (SMTP padrao).

O projeto nao tinha mecanismo de e-mail; esta e a abstracao minima, ligada por
configuracao e sem acoplar o dominio a fornecedor nenhum -- qualquer SMTP serve
(provedor proprio, SES, SendGrid, Resend...).

Sem `SMTP_HOST` configurado o envio fica em modo REGISTRO: o e-mail nao sai, a
operacao NAO falha e o assunto/destinatario ficam no log. Isso mantem DEV e a
suite de testes funcionando sem servidor de e-mail, e deixa explicito no log que
nada foi entregue.

Nunca sao logados: corpo do e-mail, link de ativacao, token ou SMTP_PASSWORD.
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


class ConfiguracaoEmail:
    """Le o ambiente na hora do envio (facilita teste e troca sem reiniciar)."""

    def __init__(self) -> None:
        self.host = (os.getenv("SMTP_HOST") or "").strip()
        self.port = int(os.getenv("SMTP_PORT", "587") or 587)
        self.user = os.getenv("SMTP_USER") or ""
        self.password = os.getenv("SMTP_PASSWORD") or ""
        self.remetente = (os.getenv("SMTP_FROM") or self.user or "nao-responda@pesquisa360.local").strip()
        self.usar_tls = (os.getenv("SMTP_USE_TLS", "true") or "true").strip().lower() not in {"0", "false", "no"}
        self.timeout = int(os.getenv("SMTP_TIMEOUT_SECONDS", "15") or 15)

    @property
    def habilitado(self) -> bool:
        return bool(self.host)


def enviar_email(destinatario: str, assunto: str, corpo: str) -> bool:
    """True quando o e-mail saiu de fato; False quando so foi registrado.

    Falha de SMTP NAO propaga: um convite gravado no banco nao pode ser perdido
    porque o servidor de e-mail estava fora do ar. O erro vai para o log e o
    reenvio resolve.
    """
    config = ConfiguracaoEmail()
    if not config.habilitado:
        logger.info(
            "E-mail nao enviado (SMTP_HOST ausente). destinatario=%s assunto=%s", destinatario, assunto
        )
        return False

    mensagem = EmailMessage()
    mensagem["From"] = config.remetente
    mensagem["To"] = destinatario
    mensagem["Subject"] = assunto
    mensagem.set_content(corpo)

    try:
        with smtplib.SMTP(config.host, config.port, timeout=config.timeout) as servidor:
            if config.usar_tls:
                servidor.starttls()
            if config.user:
                servidor.login(config.user, config.password)
            servidor.send_message(mensagem)
        logger.info("E-mail enviado. destinatario=%s assunto=%s", destinatario, assunto)
        return True
    except Exception:
        # Sem `exc_info` com credencial: a mensagem do smtplib pode citar o
        # usuario autenticado.
        logger.warning("Falha ao enviar e-mail. destinatario=%s assunto=%s", destinatario, assunto)
        return False


def web_base_url() -> str:
    """Base do link de ativacao. Nunca hardcoded no dominio."""
    return (os.getenv("WEB_BASE_URL") or "http://localhost:5173").rstrip("/")
