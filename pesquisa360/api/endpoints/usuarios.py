# pesquisa360/api/endpoints/usuarios.
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Annotated

from pesquisa360 import crud, schemas
from pesquisa360.db import models
from pesquisa360.core.dependencies import get_db, get_current_user

router = APIRouter()

@router.post("/", response_model=schemas.Usuario, status_code=status.HTTP_201_CREATED)
def create_user(user: schemas.UsuarioCreate, db: Session = Depends(get_db)):
    db_user = crud.get_user_by_email(db, email=user.email)
    if db_user:
        raise HTTPException(status_code=400, detail="E-mail já cadastrado")

    # Cria perfis padrão se não existirem
    perfil_agente = crud.get_perfil_by_name(db, nome="Agente")
    if not perfil_agente:
        crud.create_perfil(db, perfil=schemas.PerfilCreate(nome="Agente"))

    perfil_admin = crud.get_perfil_by_name(db, nome="Gerente")
    if not perfil_admin:
        crud.create_perfil(db, perfil=schemas.PerfilCreate(nome="Gerente"))

    return crud.create_user(db=db, user=user)

@router.get("/me/", response_model=schemas.Usuario)
def read_users_me(current_user: Annotated[models.Usuario, Depends(get_current_user)]):
    return current_user

# --- ROTA NOVA: LISTAR USUÁRIOS ---
@router.get("/", response_model=List[schemas.Usuario])
def read_users(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user) # Exige login
):
    """
    Lista todos os usuários.
    """
    users = crud.get_users(db, skip=skip, limit=limit)
    return users