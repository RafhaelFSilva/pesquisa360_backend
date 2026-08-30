"""Convite e ativacao de conta.

Ciclo: usuario nasce INATIVO -> recebe link com token de uso unico -> define a
primeira senha -> conta ativa.

Decisoes que sustentam a seguranca do fluxo:

* o token e `secrets.token_urlsafe(32)` -- aleatorio criptografico, nao um id
  sequencial nem um hash de dados do usuario;
* o banco guarda apenas o SHA-256 do token. SHA-256 aqui e adequado porque o
  segredo tem entropia alta e vida curta; senha continua em bcrypt/Passlib;
* a busca e feita PELO HASH, entao o token puro nunca precisa ser comparado
  linha a linha;
* uso unico via `usado_em`, e nao DELETE: apagar perderia a evidencia de que o
  convite foi consumido;
* validade padrao de 24h.

Nada aqui loga token, link ou senha.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from pesquisa360.core import security
from pesquisa360.core.password_policy import erro_da_senha
from pesquisa360.db import models
from pesquisa360.services import email as servico_email

logger = logging.getLogger(__name__)

VALIDADE_HORAS = 24

STATUS_VALIDO = "VALIDO"
STATUS_INVALIDO = "INVALIDO"
STATUS_EXPIRADO = "EXPIRADO"
STATUS_UTILIZADO = "UTILIZADO"

ASSUNTO_CONVITE = "Ative seu acesso ao Pesquisa360"


@dataclass(frozen=True)
class ConviteGerado:
    """Convite recem-criado, para uso IMEDIATO de quem chamou.

    Vive apenas em memoria, no ciclo da request: `activation_url` embute o token
    puro e equivale temporariamente a uma credencial de ativacao. Nada disto e
    persistido -- o banco continua com o SHA-256 e mais nada -- e por isso o
    link nao pode ser recuperado depois. Para quem perdeu o link, o caminho e
    gerar um convite novo, que invalida o anterior.
    """

    token: str
    activation_url: str
    expira_em: datetime
    email_enviado: bool


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _com_fuso(valor: Optional[datetime]) -> Optional[datetime]:
    """SQLite devolve datetime ingenuo; comparar com aware estouraria."""
    if valor is None:
        return None
    return valor if valor.tzinfo is not None else valor.replace(tzinfo=timezone.utc)


def gerar_token_ativacao(
    db: Session, usuario: models.Usuario, validade_horas: int = VALIDADE_HORAS
) -> tuple[str, models.UserActivationToken]:
    """Cria o convite e devolve o token PURO -- que so o e-mail vera.

    Convites anteriores ainda abertos sao invalidados: dois links validos ao
    mesmo tempo multiplicam a superficie de ataque sem ganho nenhum.
    """
    agora = _agora()
    db.query(models.UserActivationToken).filter(
        models.UserActivationToken.usuario_id == usuario.id,
        models.UserActivationToken.usado_em.is_(None),
    ).update({models.UserActivationToken.usado_em: agora}, synchronize_session=False)

    token = secrets.token_urlsafe(32)
    registro = models.UserActivationToken(
        usuario_id=usuario.id,
        token_hash=hash_token(token),
        expira_em=agora + timedelta(hours=validade_horas),
    )
    db.add(registro)
    # `flush` sem commit: o chamador continua dono da transacao, mas ja pode ler
    # `expira_em` para devolver ao painel.
    db.flush()
    return token, registro


def url_ativacao(token: str) -> str:
    return f"{servico_email.web_base_url()}/ativar-conta?token={token}"


def corpo_convite(nome: Optional[str], url: str, validade_horas: int = VALIDADE_HORAS) -> str:
    saudacao = (nome or "").strip() or "Olá"
    return (
        f"Olá, {saudacao}.\n\n"
        "Foi criado um acesso para você no Pesquisa360.\n\n"
        "Para ativar sua conta e definir sua senha, acesse:\n\n"
        f"{url}\n\n"
        f"Este link expira em {validade_horas} horas.\n\n"
        "Caso você não reconheça este convite, ignore esta mensagem.\n"
    )


def enviar_convite(
    db: Session, usuario: models.Usuario, validade_horas: int = VALIDADE_HORAS
) -> ConviteGerado:
    """Gera o convite, dispara o e-mail e devolve o link para uso imediato.

    O commit e de quem chama: criar o usuario e criar o convite pertencem a
    mesma unidade de trabalho. `email_enviado=False` NAO e falha do convite --
    o token esta gravado e o administrador pode compartilhar o link por outro
    meio.
    """
    token, registro = gerar_token_ativacao(db, usuario, validade_horas=validade_horas)
    url = url_ativacao(token)
    enviado = servico_email.enviar_email(
        destinatario=usuario.email,
        assunto=ASSUNTO_CONVITE,
        corpo=corpo_convite(usuario.nome, url, validade_horas),
    )
    return ConviteGerado(
        token=token,
        activation_url=url,
        expira_em=_com_fuso(registro.expira_em),
        email_enviado=bool(enviado),
    )


def avaliar_token(db: Session, token: Optional[str]) -> tuple[str, Optional[models.UserActivationToken]]:
    """(status, registro). Status distingue invalido, expirado e utilizado.

    O Web precisa dessa distincao para orientar o convidado; nenhuma delas
    revela dado de outro usuario.
    """
    if not token:
        return STATUS_INVALIDO, None
    registro = (
        db.query(models.UserActivationToken)
        .filter(models.UserActivationToken.token_hash == hash_token(token))
        .first()
    )
    if registro is None:
        return STATUS_INVALIDO, None
    if registro.usado_em is not None:
        return STATUS_UTILIZADO, registro
    if _com_fuso(registro.expira_em) <= _agora():
        return STATUS_EXPIRADO, registro
    return STATUS_VALIDO, registro


def descrever_token(db: Session, token: Optional[str]) -> dict:
    """Resposta do endpoint publico de validacao.

    Devolve o minimo para a tela se orientar: nunca `senha_hash`, `token_hash`,
    id interno ou dado de outro usuario. O e-mail so aparece quando o token e
    valido -- e e o e-mail de quem ja tem o link em maos.
    """
    status, registro = avaliar_token(db, token)
    corpo = {"status": status, "valido": status == STATUS_VALIDO, "email": None, "nome": None}
    if status == STATUS_VALIDO and registro is not None:
        usuario = db.query(models.Usuario).filter(models.Usuario.id == registro.usuario_id).first()
        if usuario is None:
            return {"status": STATUS_INVALIDO, "valido": False, "email": None, "nome": None}
        corpo["email"] = usuario.email
        corpo["nome"] = usuario.nome
        corpo["expira_em"] = _com_fuso(registro.expira_em)
    return corpo


class ErroAtivacao(Exception):
    """Falha de negocio da ativacao, com status HTTP sugerido."""

    def __init__(self, mensagem: str, status_code: int = 400, status_token: Optional[str] = None):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status_code = status_code
        self.status_token = status_token


def ativar_conta(db: Session, token: Optional[str], senha: str) -> models.Usuario:
    """Define a primeira senha e ativa a conta, de forma ATOMICA.

    Ou tudo acontece (senha gravada + conta ativa + token consumido) ou nada:
    qualquer excecao faz rollback, e o token so e marcado como usado no mesmo
    commit da senha -- nunca antes.
    """
    status, registro = avaliar_token(db, token)
    if status != STATUS_VALIDO or registro is None:
        mensagens = {
            STATUS_EXPIRADO: "Este link de ativacao expirou.",
            STATUS_UTILIZADO: "Este link de ativacao ja foi utilizado.",
            STATUS_INVALIDO: "Link de ativacao invalido.",
        }
        raise ErroAtivacao(mensagens.get(status, "Link de ativacao invalido."), 400, status)

    problema = erro_da_senha(senha)
    if problema:
        raise ErroAtivacao(problema, 422, STATUS_VALIDO)

    usuario = db.query(models.Usuario).filter(models.Usuario.id == registro.usuario_id).first()
    if usuario is None:
        raise ErroAtivacao("Link de ativacao invalido.", 400, STATUS_INVALIDO)

    try:
        usuario.senha_hash = security.get_password_hash(senha)
        usuario.ativo = True
        registro.usado_em = _agora()
        db.add(usuario)
        db.add(registro)
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(usuario)
    logger.info("Conta ativada. usuario_id=%s", usuario.id)
    from pesquisa360.services import auditoria

    auditoria.registrar(auditoria.ACCOUNT_ACTIVATED, auditoria.SEV_INFO, user=usuario, status_code=200)
    return usuario
