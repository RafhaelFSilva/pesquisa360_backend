# pesquisa360/core/dependencies.py
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from jose import ExpiredSignatureError, JWTError, jwt
from pydantic import ValidationError

from pesquisa360 import crud, schemas
from pesquisa360.core import security
from pesquisa360.db import models
from pesquisa360.db.session import SessionLocal

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login/token")

def _credentials_exception(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user(
    db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)
) -> schemas.Usuario:
    try:
        payload = jwt.decode(
            token, security.SECRET_KEY, algorithms=[security.ALGORITHM]
        )
        email: str = payload.get("sub")
        if email is None:
            raise _credentials_exception("Token inválido")
            
        token_data = schemas.TokenData(email=email)
    except ExpiredSignatureError:
        raise _credentials_exception("Token expirado")
    except (JWTError, ValidationError):
        raise _credentials_exception("Token inválido")
        
    user = crud.get_user_by_email(db, email=token_data.email)
    if not is_user_access_active(user):
        raise _credentials_exception("Token inválido")
        
    return user

def _is_superadmin_name(nome: str | None) -> bool:
    return bool(nome and nome.strip().casefold() == "superadmin")

def _is_manager_name(nome: str | None) -> bool:
    return bool(nome and nome.strip().casefold() == "gerente")

def is_user_access_active(user) -> bool:
    if user is None or getattr(user, "ativo", None) is not True:
        return False

    perfil = getattr(user, "perfil", None)
    perfil_nome = getattr(perfil, "nome", None)
    if getattr(user, "company_id", None) is None:
        return _is_superadmin_name(perfil_nome)

    company = getattr(user, "company", None)
    return bool(company and getattr(company, "is_active", None) is True)

def _get_profile_name(db: Session, user) -> str | None:
    perfil = getattr(user, "perfil", None)
    if perfil is not None:
        return getattr(perfil, "nome", None)

    perfil_id = getattr(user, "perfil_id", None)
    if perfil_id is None:
        return None

    perfil = db.query(models.Perfil).filter(models.Perfil.id == perfil_id).first()
    return getattr(perfil, "nome", None)

def is_superadmin(user) -> bool:
    if user is None or user.ativo is False:
        return False

    perfil = getattr(user, "perfil", None)
    return _is_superadmin_name(getattr(perfil, "nome", None))

def require_superadmin(
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user),
) -> models.Usuario:
    if is_superadmin(current_user):
        return current_user

    perfil = None
    if getattr(current_user, "perfil_id", None) is not None:
        perfil = db.query(models.Perfil).filter(
            models.Perfil.id == current_user.perfil_id
        ).first()

    if perfil and _is_superadmin_name(perfil.nome):
        return current_user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Acesso restrito a Superadmin.",
    )

def require_manager_or_superadmin(
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user),
) -> models.Usuario:
    profile_name = _get_profile_name(db, current_user)
    if _is_superadmin_name(profile_name) or _is_manager_name(profile_name):
        return current_user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Acesso restrito a Gerente ou Superadmin.",
    )
