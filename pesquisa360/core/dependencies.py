# pesquisa360/core/dependencies.py

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from jose import JWTError, jwt

from pesquisa360 import crud, schemas
from pesquisa360.core import security
from pesquisa360.db.session import SessionLocal

# Define o esquema de autenticação. "tokenUrl" aponta para o nosso endpoint de login.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login/token")

def get_db():
    """
    Dependência para obter a sessão do banco de dados.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user(
    db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)
):
    """
    Dependência para obter o usuário atual a partir de um token JWT.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Não foi possível validar as credenciais",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        # Decodifica o token para pegar o e-mail (subject)
        payload = jwt.decode(
            token, security.SECRET_KEY, algorithms=[security.ALGORITHM]
        )
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
        token_data = schemas.TokenData(email=email)
    except JWTError:
        raise credentials_exception

    # Busca o usuário no banco de dados
    user = crud.get_user_by_email(db, email=token_data.email)
    if user is None:
        raise credentials_exception
    return user