from typing import List, Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from pesquisa360 import crud, schemas
from pesquisa360.db import models
from pesquisa360.core.dependencies import (
    get_db,
    get_current_user,
    is_superadmin,
    require_manager_or_superadmin,
    require_superadmin,
)

router = APIRouter()
admin_router = APIRouter(prefix="/admin/usuarios", tags=["Admin Usuarios"])
profiles_router = APIRouter(prefix="/perfis", tags=["Perfis"])

ASSIGNABLE_PROFILE_CODES = {
    "agente": "AGENT",
    "gerente": "MANAGER",
    "superadmin": "SUPERADMIN",
}
ASSIGNABLE_PROFILE_ORDER = ("AGENT", "MANAGER", "SUPERADMIN")


def _profile_name(user: models.Usuario) -> str:
    perfil = getattr(user, "perfil", None)
    return (getattr(perfil, "nome", None) or "").strip().casefold()


def _profile_code(nome: str | None) -> str | None:
    return ASSIGNABLE_PROFILE_CODES.get((nome or "").strip().casefold())


@profiles_router.get("/", response_model=List[schemas.PerfilAtribuivel])
def read_assignable_profiles(
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_manager_or_superadmin),
):
    allowed_codes = {"AGENT", "MANAGER"} if is_superadmin(current_user) else {"AGENT"}
    profiles_by_code = {}

    for perfil in db.query(models.Perfil).order_by(models.Perfil.id).all():
        code = _profile_code(perfil.nome)
        if code in allowed_codes and code not in profiles_by_code:
            profiles_by_code[code] = schemas.PerfilAtribuivel(
                id=perfil.id,
                code=code,
                nome=perfil.nome,
            )

    return [
        profiles_by_code[code]
        for code in ASSIGNABLE_PROFILE_ORDER
        if code in profiles_by_code
    ]


def _get_managed_user(
    db: Session,
    usuario_id: int,
    current_user: models.Usuario,
) -> models.Usuario:
    user = crud.get_user_by_id(db=db, usuario_id=usuario_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")

    if is_superadmin(current_user):
        return user

    if user.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")
    if user.id == current_user.id:
        raise HTTPException(status_code=403, detail="Nao e permitido alterar o proprio usuario.")
    if _profile_name(user) != "agente":
        raise HTTPException(status_code=403, detail="Gerente pode administrar somente Agentes.")
    return user

@router.post("/", response_model=schemas.Usuario, status_code=status.HTTP_201_CREATED)
def create_user(
    user: schemas.UsuarioCreate, 
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_manager_or_superadmin)
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

    perfil = crud.get_perfil(db=db, perfil_id=user.perfil_id)
    if not perfil:
        raise HTTPException(status_code=404, detail="Perfil nao encontrado.")

    perfil_nome = perfil.nome.strip().casefold()
    if is_superadmin(current_user):
        if perfil_nome not in {"gerente", "agente"}:
            raise HTTPException(status_code=403, detail="Perfil nao permitido para esta operacao.")
        if user.company_id is None or not crud.get_company(db=db, company_id=user.company_id):
            raise HTTPException(status_code=404, detail="Empresa nao encontrada.")
        company_id = user.company_id
    else:
        if perfil_nome != "agente":
            raise HTTPException(status_code=403, detail="Gerente pode criar somente Agentes.")
        company_id = current_user.company_id

    return crud.create_user(
        db=db,
        user=user,
        current_user=current_user,
        company_id=company_id,
    )

@router.get("/me/", response_model=schemas.Usuario)
def read_users_me(current_user: Annotated[models.Usuario, Depends(get_current_user)]):
    """Retorna dados do usuário logado."""
    return current_user

@router.get("/", response_model=List[schemas.Usuario])
def read_users(
    skip: int = 0, 
    limit: int = 100, 
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_manager_or_superadmin)
):
    """
    Lista todos os usuários DA MESMA EMPRESA.
    """
    if is_superadmin(current_user):
        return crud.get_admin_users(db=db, skip=skip, limit=limit)
    return crud.get_users(db, current_user=current_user, skip=skip, limit=limit)

@router.get("/agentes/", response_model=List[schemas.Usuario])
def read_agentes(
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_manager_or_superadmin)
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


@router.get("/{usuario_id}", response_model=schemas.Usuario)
def read_user(
    usuario_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_manager_or_superadmin),
):
    return _get_managed_user(db=db, usuario_id=usuario_id, current_user=current_user)


@router.patch("/{usuario_id}", response_model=schemas.Usuario)
def update_user(
    usuario_id: int,
    user_update: schemas.UsuarioAdminUpdate,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_manager_or_superadmin),
):
    user = _get_managed_user(db=db, usuario_id=usuario_id, current_user=current_user)
    if is_superadmin(current_user):
        if user_update.company_id is not None and not crud.get_company(
            db=db,
            company_id=user_update.company_id,
        ):
            raise HTTPException(status_code=404, detail="Empresa nao encontrada.")
        if user_update.perfil_id is not None and not crud.get_perfil(
            db=db,
            perfil_id=user_update.perfil_id,
        ):
            raise HTTPException(status_code=404, detail="Perfil nao encontrado.")
        return crud.update_admin_user(db=db, db_user=user, user_update=user_update)

    update_data = user_update.model_dump(exclude_unset=True)
    requested_profile_id = update_data.pop("perfil_id", None)
    update_data.pop("company_id", None)
    if requested_profile_id is not None:
        perfil = crud.get_perfil(db=db, perfil_id=requested_profile_id)
        if not perfil:
            raise HTTPException(status_code=404, detail="Perfil nao encontrado.")
        if perfil.nome.strip().casefold() != "agente":
            raise HTTPException(status_code=403, detail="Gerente nao pode elevar o perfil de Agente.")

    safe_update = schemas.UsuarioAdminUpdate(**update_data)
    return crud.update_admin_user(db=db, db_user=user, user_update=safe_update)


@router.patch("/{usuario_id}/senha", response_model=schemas.Usuario)
def reset_user_password(
    usuario_id: int,
    password_reset: schemas.UsuarioPasswordReset,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_manager_or_superadmin),
):
    user = _get_managed_user(db=db, usuario_id=usuario_id, current_user=current_user)
    return crud.update_admin_user(
        db=db,
        db_user=user,
        user_update=schemas.UsuarioAdminUpdate(senha=password_reset.senha),
    )

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
