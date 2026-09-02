from datetime import datetime
from typing import List, Annotated, Optional
from fastapi import Query, APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from pesquisa360 import crud, schemas
from pesquisa360.db import models
from pesquisa360.core import rbac
from pesquisa360.services import acessos, ativacao, auditoria, modulos
from pesquisa360.core.dependencies import (
    get_db,
    get_current_user,
    is_superadmin,
    require_manager_or_superadmin,
    require_superadmin,
)

router = APIRouter()
admin_router = APIRouter(prefix="/admin/usuarios", tags=["Admin Usuarios"])
# ADR-039: leitura da trilha. Nao e o painel -- e a fonte que o painel usara.
auditoria_router = APIRouter(prefix="/admin/auditoria", tags=["Admin Auditoria"])
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

def _com_convite(usuario: models.Usuario, convite) -> dict:
    """Monta a resposta de criacao com o link transitorio do convite."""
    corpo = schemas.Usuario.model_validate(usuario, from_attributes=True).model_dump()
    corpo["convite"] = {
        "activation_url": convite.activation_url,
        "expira_em": convite.expira_em,
        "email_enviado": convite.email_enviado,
    }
    return corpo


# --- Ativacao de conta (PUBLICO) ---------------------------------------------
# Sao as unicas rotas anonimas deste modulo: quem tem o link ainda nao tem
# sessao. A autoridade continua sendo o token de uso unico, validado no banco.


@router.get("/ativacao/validar", response_model=schemas.AtivacaoTokenStatus)
def validar_token_ativacao(token: str = "", db: Session = Depends(get_db)):
    """Diz ao Web se o link e valido, expirou, ja foi usado ou nao existe."""
    return ativacao.descrever_token(db, token)


@router.post("/ativacao", response_model=schemas.AtivacaoContaResponse)
def ativar_conta(payload: schemas.AtivacaoContaRequest, db: Session = Depends(get_db)):
    """Define a primeira senha e ativa a conta (transacao unica)."""
    if payload.confirmacao_senha is not None and payload.confirmacao_senha != payload.senha:
        raise HTTPException(status_code=422, detail="As senhas nao conferem.")
    try:
        usuario = ativacao.ativar_conta(db, payload.token, payload.senha)
    except ativacao.ErroAtivacao as erro:
        raise HTTPException(status_code=erro.status_code, detail=erro.mensagem)
    return {
        "ativado": True,
        "email": usuario.email,
        "mensagem": "Conta ativada com sucesso. Sua senha foi definida.",
    }


@router.post("/", response_model=schemas.UsuarioCriadoResponse, status_code=status.HTTP_201_CREATED)
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

    criado = crud.create_user(
        db=db,
        user=user,
        current_user=current_user,
        company_id=company_id,
    )
    # Sem senha no payload = cadastro por convite: a conta nasce inativa e o
    # proprio usuario define a senha pelo link. Com senha, o comportamento
    # anterior e preservado.
    if not user.senha:
        criado.ativo = False
        db.add(criado)
        convite = ativacao.enviar_convite(db, criado)
        db.commit()
        db.refresh(criado)
        # Link devolvido UMA vez, para o administrador repassar por outro meio
        # se o e-mail nao chegar. Nao e persistido nem recuperavel depois.
        return _com_convite(criado, convite)
    return criado

@router.get("/me/", response_model=schemas.Usuario)
def read_users_me(
    current_user: Annotated[models.Usuario, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """Dados do usuário logado.

    ADR-024: `company_id` continua sendo a empresa PRINCIPAL/default. Os campos
    `company_ids`/`multiempresa` sao aditivos e dizem onde ele pode trabalhar.
    A arvore de projetos NAO vem aqui: para isso existe `GET /projetos/`.
    """
    company_ids = acessos.listar_company_ids_acessiveis(db, current_user)
    dados = schemas.Usuario.model_validate(current_user, from_attributes=True)
    dados.company_ids = company_ids
    dados.multiempresa = len(company_ids) > 1
    # ADR-037: a UI deriva daqui o que mostrar. Duplicar a matriz no React
    # criaria duas verdades sobre a mesma regra.
    dados.papel = rbac.papel_nome(current_user)
    dados.permissions = rbac.resumo_permissoes(current_user)
    return dados


@router.get("/me/modulos/", response_model=schemas.ModulosUsuarioResponse)
def read_current_user_modules(
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user),
):
    """Capacidades comerciais da empresa principal do usuario autenticado.

    Nao e ACL nem autorizacao final por perfil, e nao recebe company_id do
    cliente. O gating das rotas existentes permanece desativado nesta fase.
    """
    return {"modulos": modulos.resolver_modulos_empresa(db, current_user.company_id)}

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
             .filter(acessos.filtro_usuario_visivel(current_user))\
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

@admin_router.post("/", response_model=schemas.UsuarioCriadoResponse, status_code=status.HTTP_201_CREATED)
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

    criado = crud.create_admin_user(db=db, user=user)
    if not user.senha:
        # Mesmo cadastro por convite da rota de tenant.
        criado.ativo = False
        db.add(criado)
        convite = ativacao.enviar_convite(db, criado)
        db.commit()
        db.refresh(criado)
        return _com_convite(criado, convite)
    return criado

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


# --- ACL multiempresa/multiprojeto (ADR-024) ---------------------------------
# Somente Superadmin nesta fase: permitir que um Gerente da Empresa A conceda
# acesso a Empresa B seria escalacao de privilegio.


@admin_router.get("/{usuario_id}/acessos", response_model=schemas.UsuarioAcessosResponse)
def obter_acessos_usuario(
    usuario_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_superadmin),
):
    usuario = db.query(models.Usuario).filter(models.Usuario.id == usuario_id).first()
    if usuario is None:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")
    return acessos.acessos_do_usuario(db, usuario)


@admin_router.put("/{usuario_id}/acessos", response_model=schemas.UsuarioAcessosResponse)
def definir_acessos_usuario(
    usuario_id: int,
    payload: schemas.UsuarioAcessosRequest,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_superadmin),
):
    """Substitui a ACL inteira do usuario em UMA transacao.

    Tudo e validado antes de qualquer escrita: empresa existente e ativa,
    projeto existente e pertencente a empresa informada, sem duplicatas e com a
    empresa principal entre as autorizadas. Um item invalido nao deixa metade da
    ACL gravada.
    """
    usuario = db.query(models.Usuario).filter(models.Usuario.id == usuario_id).first()
    if usuario is None:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")
    return acessos.substituir_acessos(db, usuario, payload)


@admin_router.post("/{usuario_id}/reenviar-ativacao", response_model=schemas.ReenvioAtivacaoResponse)
def reenviar_ativacao(
    usuario_id: int,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_superadmin),
):
    """Novo convite para conta ainda nao ativada.

    Reusa `enviar_convite`, que invalida o convite anterior ainda aberto -- dois
    links validos ao mesmo tempo so ampliariam a superficie de ataque. Conta ja
    ativa nao recebe convite: seria um caminho paralelo de troca de senha.
    """
    usuario = db.query(models.Usuario).filter(models.Usuario.id == usuario_id).first()
    if usuario is None:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")
    if usuario.ativo:
        raise HTTPException(status_code=400, detail="Usuario ja esta ativo.")

    convite = ativacao.enviar_convite(db, usuario)
    db.commit()
    return {
        "enviado": convite.email_enviado,
        "email": usuario.email,
        "expira_em": convite.expira_em,
        "activation_url": convite.activation_url,
    }


@auditoria_router.get("/eventos", response_model=schemas.AuditEventPage)
def listar_eventos_auditoria(
    event_type: Optional[str] = None,
    severity: Optional[str] = None,
    user_id: Optional[int] = None,
    project_id: Optional[int] = None,
    company_id: Optional[int] = None,
    ip_address: Optional[str] = None,
    data_inicio: Optional[datetime] = None,
    data_fim: Optional[datetime] = None,
    limit: int = Query(auditoria.LIMITE_PADRAO_LISTAGEM, ge=1, le=auditoria.LIMITE_MAX_LISTAGEM),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_manager_or_superadmin),
):
    """Trilha de seguranca (ADR-039), mais recente primeiro.

    Superadmin: global, `company_id` e filtro real.
    Gerente: SOMENTE o proprio tenant -- `company_id` da query e ignorado como
    seletor de escopo, nunca amplia. Demais perfis: 403 (dependency).
    """
    if not is_superadmin(current_user):
        company_id = current_user.company_id
    itens, total = auditoria.listar_eventos(
        db, event_type=event_type, severity=severity, user_id=user_id,
        project_id=project_id, company_id=company_id, ip_address=ip_address,
        data_inicio=data_inicio, data_fim=data_fim, limit=limit, offset=offset,
    )
    return {"items": itens, "total": total, "limit": limit, "offset": offset}


@auditoria_router.get("/resumo", response_model=schemas.AuditSummary)
def resumo_auditoria(
    data_inicio: Optional[datetime] = None,
    data_fim: Optional[datetime] = None,
    company_id: Optional[int] = None,
    project_id: Optional[int] = None,
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(require_manager_or_superadmin),
):
    """Agregacoes do painel de seguranca (ADR-041): cards, ranking de IPs,
    contas mais tentadas e serie temporal. Periodo padrao: ultimas 24h.

    Mesma regra de escopo de `/eventos`: Superadmin global (company_id e filtro
    real); Gerente SOMENTE o proprio tenant -- eventos sem company_id (login
    contra e-mail desconhecido, por exemplo) nao entram na visao gerencial.
    Nao gera evento: o painel so le.
    """
    if not is_superadmin(current_user):
        company_id = current_user.company_id
    return auditoria.resumo_seguranca(
        db, company_id=company_id, project_id=project_id, user_id=user_id,
        data_inicio=data_inicio, data_fim=data_fim,
    )
