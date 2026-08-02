from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from typing import Annotated

from pesquisa360 import crud, schemas
from pesquisa360.core import security
from pesquisa360.core.dependencies import get_db, is_user_access_active

router = APIRouter()

@router.post("/token", response_model=schemas.Token)
def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Session = Depends(get_db)
):
    # crud.get_user_by_email continua funcionando globalmente para login
    user = crud.get_user_by_email(db, email=form_data.username)
    
    # Validação de senha usando o novo security.verify_password (Bcrypt)
    if (
        not user
        or not security.verify_password(form_data.password, user.senha_hash)
        or not is_user_access_active(user)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-mail ou senha incorretos",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Criação do token
    access_token = security.create_access_token(data={"sub": user.email})
    return {"access_token": access_token, "token_type": "bearer"}
