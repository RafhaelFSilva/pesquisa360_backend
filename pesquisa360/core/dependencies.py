# pesquisa360/core/dependencies.py
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from jose import ExpiredSignatureError, JWTError, jwt
from pydantic import ValidationError

from pesquisa360 import crud, schemas
from pesquisa360.core import security
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
    if user is None or user.ativo is False:
        raise _credentials_exception("Token inválido")
        
    return user
