from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from pesquisa360 import crud, schemas
from pesquisa360.core.dependencies import get_db, require_superadmin
from pesquisa360.db import models

router = APIRouter()

@router.get("/", response_model=List[schemas.CompanyRead])
def read_companies(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_superadmin),
):
    return crud.get_companies(db=db, skip=skip, limit=limit)

@router.post("/", response_model=schemas.CompanyRead, status_code=status.HTTP_201_CREATED)
def create_company(
    company: schemas.CompanyCreate,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_superadmin),
):
    return crud.create_company(db=db, company=company)

@router.get("/{company_id}", response_model=schemas.CompanyRead)
def read_company(
    company_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_superadmin),
):
    company = crud.get_company(db=db, company_id=company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa nao encontrada.")
    return company

@router.patch("/{company_id}", response_model=schemas.CompanyRead)
def update_company(
    company_id: int,
    company_update: schemas.CompanyUpdate,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_superadmin),
):
    company = crud.get_company(db=db, company_id=company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa nao encontrada.")
    return crud.update_company(db=db, db_company=company, company_update=company_update)
