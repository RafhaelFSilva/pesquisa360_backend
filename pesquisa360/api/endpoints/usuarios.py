from typing import List, Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from pesquisa360 import crud, schemas
from pesquisa360.db import models
from pesquisa360.core.dependencies import get_db, get_current_user

router = APIRouter()

@router.post("/", response_model=schemas.Usuario, status_code=status.HTTP_201_CREATED)
def create_user(
    user: schemas.UsuarioCreate, 
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user) # <--- OBRIGATÓRIO AGORA
):
    """
    Cria um novo usuário.
    IMPORTANTE: O usuário criado será vinculado automaticamente à EMPRESA do 'current_user'.
    Ex: Um Gerente cria um Agente para sua própria equipe.
    """
    # 1. Verifica se o email já existe (Globalmente é mais seguro para login único)
    db_user = crud.get_user_by_email(db, email=user.email)
    if db_user:
        raise HTTPException(status_code=400, detail="E-mail já cadastrado no sistema")

    # 2. Garante que perfis básicos existam (Agente/Gerente)
    # Isso é útil para setup inicial, mas idealmente já deve estar no banco
    perfil_agente = crud.get_perfil_by_name(db, nome="Agente")
    if not perfil_agente:
        crud.create_perfil(db, perfil=schemas.PerfilCreate(nome="Agente"))

    perfil_admin = crud.get_perfil_by_name(db, nome="Gerente")
    if not perfil_admin:
        crud.create_perfil(db, perfil=schemas.PerfilCreate(nome="Gerente"))

    # 3. Cria o usuário vinculado à empresa
    return crud.create_user(db=db, user=user, current_user=current_user)

@router.get("/me/", response_model=schemas.Usuario)
def read_users_me(current_user: Annotated[models.Usuario, Depends(get_current_user)]):
    """Retorna dados do usuário logado."""
    return current_user

@router.get("/", response_model=List[schemas.Usuario])
def read_users(
    skip: int = 0, 
    limit: int = 100, 
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """
    Lista todos os usuários DA MESMA EMPRESA.
    """
    return crud.get_users(db, current_user=current_user, skip=skip, limit=limit)