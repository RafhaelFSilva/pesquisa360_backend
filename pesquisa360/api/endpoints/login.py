from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from jose import ExpiredSignatureError, JWTError, jwt
from sqlalchemy.orm import Session
from typing import Annotated

from pesquisa360 import crud, schemas
from pesquisa360.core import security
from pesquisa360.services import auditoria
from pesquisa360.core.dependencies import get_db, is_user_access_active

router = APIRouter()


def _credentials_exception(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _emitir_sessao(email: str) -> dict:
    """Par access + refresh para o mesmo `sub`.

    O refresh tambem e reemitido a cada renovacao. Isso encurta a vida util de
    cada refresh em uso, mas NAO revoga o anterior: sem estado no servidor, o
    refresh antigo continua valido ate o proprio `exp`.
    """
    return {
        "access_token": security.create_access_token(data={"sub": email}),
        "refresh_token": security.create_refresh_token(data={"sub": email}),
        "token_type": "bearer",
        "expires_in": security.access_token_expires_in(),
    }

@router.post("/token", response_model=schemas.Token)
def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Session = Depends(get_db)
):
    # crud.get_user_by_email continua funcionando globalmente para login
    user = crud.get_user_by_email(db, email=form_data.username)
    
    # Validação de senha usando o novo security.verify_password (Bcrypt)
    senha_ok = bool(user) and security.verify_password(form_data.password, user.senha_hash)
    if not user or not senha_ok or not is_user_access_active(user):
        # ADR-039: o motivo fica so na trilha -- o cliente segue vendo a
        # mensagem generica. O e-mail tentado e guardado mesmo sem usuario.
        motivo = "usuario_inexistente" if not user else ("senha_invalida" if not senha_ok else "conta_inativa")
        # Credencial certa + conta/empresa inativa tem evento proprio: e um
        # sinal diferente de senha errada, e so a trilha ve a diferenca.
        tipo = auditoria.ACCOUNT_INACTIVE_LOGIN if motivo == "conta_inativa" else auditoria.LOGIN_FAILED
        auditoria.registrar(
            tipo, auditoria.SEV_WARNING,
            user=user if user else None, attempted_email=form_data.username,
            status_code=401, details={"motivo": motivo},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-mail ou senha incorretos",
            headers={"WWW-Authenticate": "Bearer"},
        )

    auditoria.registrar(auditoria.LOGIN_SUCCESS, auditoria.SEV_INFO, user=user, status_code=200)
    # Criação do token
    return _emitir_sessao(user.email)


@router.post("/refresh", response_model=schemas.Token)
def refresh_access_token(
    payload: schemas.RefreshTokenRequest,
    db: Session = Depends(get_db),
):
    """Troca um refresh token valido por uma nova sessao.

    Responde SEMPRE 401 quando nao da para renovar -- expirado, malformado,
    tipo errado ou usuario que perdeu acesso. E esse 401 que diz ao cliente
    que a sessao acabou de verdade, em oposicao ao 401 de access expirado,
    que ele resolve sozinho vindo aqui.
    """
    try:
        claims = jwt.decode(
            payload.refresh_token,
            security.SECRET_KEY,
            algorithms=[security.ALGORITHM],
        )
    except ExpiredSignatureError:
        raise _credentials_exception("Sessão expirada")
    except JWTError:
        raise _credentials_exception("Token inválido")

    # Ausencia de claim vale como access (token legado) -- e access nao renova.
    if security.token_type_of(claims) != security.REFRESH_TOKEN_TYPE:
        raise _credentials_exception("Token inválido")

    email = claims.get("sub")
    if not email:
        raise _credentials_exception("Token inválido")

    # O JWT continuar criptograficamente valido nao basta: quem foi desativado
    # (ou cuja empresa foi) nao pode renovar a sessao indefinidamente.
    user = crud.get_user_by_email(db, email=email)
    if not is_user_access_active(user):
        raise _credentials_exception("Sessão expirada")

    return _emitir_sessao(user.email)
