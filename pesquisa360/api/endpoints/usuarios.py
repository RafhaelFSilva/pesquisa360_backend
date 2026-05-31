from typing import List, Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from pesquisa360 import crud, schemas
from pesquisa360.db import models
from pesquisa360.core.dependencies import get_db, get_current_user, require_superadmin

router = APIRouter()
admin_router = APIRouter(prefix="/admin/usuarios", tags=["Admin Usuarios"])

@router.post("/", response_model=schemas.Usuario, status_code=status.HTTP_201_CREATED)
def create_user(
    user: schemas.UsuarioCreate, 
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_superadmin)
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

@router.get("/agentes/", response_model=List[schemas.Usuario])
def read_agentes(
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """
    Lista agentes ativos da mesma empresa.
    """
    return db.query(models.Usuario)\
             .join(models.Perfil)\
             .filter(models.Usuario.company_id == current_user.company_id)\
             .filter(models.Usuario.ativo == True)\
             .filter(models.Perfil.nome.ilike('%agente%'))\
             .all()

@admin_router.get("/", response_model=List[schemas.Usuario])
def read_admin_users(
    company_id: int | None = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_superadmin),
):
    return crud.get_admin_users(
        db=db,
        company_id=company_id,
        skip=skip,
        limit=limit,
    )

@admin_router.post("/", response_model=schemas.Usuario, status_code=status.HTTP_201_CREATED)
def create_admin_user(
    user: schemas.UsuarioAdminCreate,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_superadmin),
):
    if crud.get_user_by_email(db, email=user.email):
        raise HTTPException(status_code=400, detail="E-mail ja cadastrado no sistema")

    if not crud.get_company(db=db, company_id=user.company_id):
        raise HTTPException(status_code=404, detail="Empresa nao encontrada.")

    if not crud.get_perfil(db=db, perfil_id=user.perfil_id):
        raise HTTPException(status_code=404, detail="Perfil nao encontrado.")

    return crud.create_admin_user(db=db, user=user)

@admin_router.get("/{usuario_id}", response_model=schemas.Usuario)
def read_admin_user(
    usuario_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_superadmin),
):
    user = crud.get_user_by_id(db=db, usuario_id=usuario_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")
    return user

@admin_router.patch("/{usuario_id}", response_model=schemas.Usuario)
def update_admin_user(
    usuario_id: int,
    user_update: schemas.UsuarioAdminUpdate,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_superadmin),
):
    user = crud.get_user_by_id(db=db, usuario_id=usuario_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")

    if user_update.company_id is not None and not crud.get_company(db=db, company_id=user_update.company_id):
        raise HTTPException(status_code=404, detail="Empresa nao encontrada.")

    if user_update.perfil_id is not None and not crud.get_perfil(db=db, perfil_id=user_update.perfil_id):
        raise HTTPException(status_code=404, detail="Perfil nao encontrado.")

    return crud.update_admin_user(db=db, db_user=user, user_update=user_update)
