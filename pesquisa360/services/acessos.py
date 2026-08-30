"""Autorizacao multiempresa/multiprojeto (ADR-024).

Regra de ouro desta camada:

    O tenant pertence ao RECURSO. O usuario pode ter acesso a varios tenants.
    A autorizacao e `usuario + ACL + recurso`, resolvida no BANCO a cada
    request -- nunca por um claim do JWT nem por `usuario.company_id`.

`usuarios.company_id` continua existindo, mas passa a significar apenas
EMPRESA PRINCIPAL/DEFAULT (branding, contexto inicial da UI, compatibilidade de
contrato). Quem responde "este usuario pode ver este projeto?" e este modulo.

Superadmin e global: nao precisa de linha em `usuario_empresa_acessos` para
administrar o SaaS, e as expressoes abaixo viram `TRUE` para ele.

Nada aqui carrega projetos para a memoria: os helpers devolvem EXPRESSOES
SQLAlchemy, para que o filtro aconteca no banco (sem N+1 e sem filtrar em
Python).
"""
from __future__ import annotations

from typing import Iterable, Optional

from fastapi import HTTPException
from sqlalchemy import Select, and_, false, or_, select, true
from sqlalchemy.orm import Session

from pesquisa360.db import models


def is_superadmin(user) -> bool:
    """Reusa a regra oficial de perfil, importada tarde de proposito.

    `core.dependencies` importa `crud`, e `crud` importa este modulo: importar
    no topo fecharia um ciclo. A definicao continua unica -- aqui so ha o
    encaminhamento.

    A checagem entrou no caminho de TODA autorizacao, e ler o perfil pode
    disparar um lazy load. Se essa leitura falhar, a resposta e "nao e
    superadmin": na duvida, nunca conceder o privilegio mais alto.
    """
    from pesquisa360.core.dependencies import is_superadmin as _oficial

    try:
        return _oficial(user)
    except Exception:
        return False


def _nao_encontrado(detail: str = "Recurso nao encontrado ou acesso negado.") -> HTTPException:
    # 404 e deliberado: negar acesso nao pode revelar que o recurso existe.
    return HTTPException(status_code=404, detail=detail)


# --- Expressoes de filtro -----------------------------------------------------


def empresas_com_acesso_total(usuario_id: int) -> Select:
    """Subquery: companies em que o usuario enxerga TODOS os projetos."""
    return select(models.UsuarioEmpresaAcesso.company_id).where(
        models.UsuarioEmpresaAcesso.usuario_id == usuario_id,
        models.UsuarioEmpresaAcesso.ativo.is_(True),
        models.UsuarioEmpresaAcesso.acesso_todos_projetos.is_(True),
    )


def empresas_acessiveis_subquery(usuario_id: int) -> Select:
    """Subquery: todas as companies com vinculo ativo (total ou restrito)."""
    return select(models.UsuarioEmpresaAcesso.company_id).where(
        models.UsuarioEmpresaAcesso.usuario_id == usuario_id,
        models.UsuarioEmpresaAcesso.ativo.is_(True),
    )


def projetos_explicitos_subquery(usuario_id: int) -> Select:
    """Subquery: projetos autorizados um a um.

    O vinculo de empresa precisa continuar ativo -- desativar a empresa inteira
    nao pode ser contornado por uma autorizacao de projeto esquecida.
    """
    return (
        select(models.UsuarioProjetoAcesso.projeto_id)
        .join(
            models.UsuarioEmpresaAcesso,
            models.UsuarioEmpresaAcesso.usuario_id == models.UsuarioProjetoAcesso.usuario_id,
        )
        .join(models.Projeto, models.Projeto.id == models.UsuarioProjetoAcesso.projeto_id)
        .where(
            models.UsuarioProjetoAcesso.usuario_id == usuario_id,
            models.UsuarioProjetoAcesso.ativo.is_(True),
            models.UsuarioEmpresaAcesso.company_id == models.Projeto.company_id,
            models.UsuarioEmpresaAcesso.ativo.is_(True),
        )
    )


def sem_acl_configurada(usuario_id: int):
    """Usuario que nunca teve ACL gravada (nem ativa, nem inativa).

    Rede de compatibilidade: se o backfill nao rodou, ninguem fica trancado do
    lado de fora -- vale o comportamento legado (empresa principal). Revogar
    acesso NAO cai aqui: `substituir_acessos` deixa vinculo inativo, entao a
    linha existe e a ACL manda.
    """
    return ~select(models.UsuarioEmpresaAcesso.id).where(
        models.UsuarioEmpresaAcesso.usuario_id == usuario_id
    ).exists()


def filtro_projeto_acessivel(current_user):
    """Expressao para usar no lugar de `Projeto.company_id == current_user.company_id`.

    Deve ser aplicada em query que ja alcanca `models.Projeto`.
    """
    if is_superadmin(current_user):
        return true()
    company_principal = getattr(current_user, "company_id", None)
    usuario_id = getattr(current_user, "id", None)
    if usuario_id is None:
        # Sem identidade nao ha ACL possivel: vale o recorte legado.
        return models.Projeto.company_id == company_principal if company_principal is not None else false()
    legado = (
        and_(sem_acl_configurada(current_user.id), models.Projeto.company_id == company_principal)
        if company_principal is not None
        else false()
    )
    return or_(
        models.Projeto.company_id.in_(empresas_com_acesso_total(current_user.id)),
        models.Projeto.id.in_(projetos_explicitos_subquery(current_user.id)),
        legado,
    )


def filtro_company_acessivel(coluna, current_user):
    """Expressao para colunas `company_id` de dados operacionais.

    Usada onde a query ja restringe o Projeto: aqui a checagem e defensiva, para
    nao afrouxar filtros historicos que citavam `Coleta.company_id`.
    """
    if is_superadmin(current_user):
        return true()
    company_principal = getattr(current_user, "company_id", None)
    usuario_id = getattr(current_user, "id", None)
    if usuario_id is None:
        return coluna == company_principal if company_principal is not None else false()
    if company_principal is None:
        return coluna.in_(empresas_acessiveis_subquery(current_user.id))
    return or_(
        coluna.in_(empresas_acessiveis_subquery(current_user.id)),
        and_(sem_acl_configurada(current_user.id), coluna == company_principal),
    )


def filtro_usuario_visivel(current_user):
    """Usuarios que compartilham ao menos uma empresa com o solicitante.

    Substitui `Usuario.company_id == current_user.company_id`: com ACL, a
    empresa principal deixou de descrever o alcance de uma pessoa.
    """
    if is_superadmin(current_user):
        return true()
    company_principal = getattr(current_user, "company_id", None)
    usuario_id = getattr(current_user, "id", None)
    if usuario_id is None:
        return models.Usuario.company_id == company_principal if company_principal is not None else false()
    empresas = empresas_acessiveis_subquery(current_user.id)
    legado = (
        and_(sem_acl_configurada(current_user.id), models.Usuario.company_id == company_principal)
        if company_principal is not None
        else false()
    )
    return or_(
        models.Usuario.company_id.in_(empresas),
        models.Usuario.id.in_(
            select(models.UsuarioEmpresaAcesso.usuario_id).where(
                models.UsuarioEmpresaAcesso.ativo.is_(True),
                models.UsuarioEmpresaAcesso.company_id.in_(empresas),
            )
        ),
        legado,
    )


# --- Consultas ----------------------------------------------------------------


def listar_company_ids_acessiveis(db: Session, current_user) -> list[int]:
    """Empresas do usuario. Superadmin: todas as ativas."""
    if is_superadmin(current_user):
        return [
            linha[0]
            for linha in db.query(models.Company.id).order_by(models.Company.id).all()
        ]
    return [
        linha[0]
        for linha in db.query(models.UsuarioEmpresaAcesso.company_id)
        .filter(
            models.UsuarioEmpresaAcesso.usuario_id == current_user.id,
            models.UsuarioEmpresaAcesso.ativo.is_(True),
        )
        .order_by(models.UsuarioEmpresaAcesso.company_id)
        .all()
    ]


def usuario_tem_acesso_empresa(db: Session, current_user, company_id: int) -> bool:
    if is_superadmin(current_user):
        return True
    return (
        db.query(models.UsuarioEmpresaAcesso.id)
        .filter(
            models.UsuarioEmpresaAcesso.usuario_id == current_user.id,
            models.UsuarioEmpresaAcesso.company_id == company_id,
            models.UsuarioEmpresaAcesso.ativo.is_(True),
        )
        .first()
        is not None
    )


def usuario_tem_acesso_projeto(db: Session, current_user, projeto_id: int) -> bool:
    """Superadmin OU (empresa ativa E (acesso total OU projeto autorizado))."""
    if is_superadmin(current_user):
        return (
            db.query(models.Projeto.id).filter(models.Projeto.id == projeto_id).first() is not None
        )
    return (
        db.query(models.Projeto.id)
        .filter(models.Projeto.id == projeto_id, filtro_projeto_acessivel(current_user))
        .first()
        is not None
    )


def assegurar_acesso_empresa(db: Session, current_user, company_id: int) -> None:
    if not usuario_tem_acesso_empresa(db, current_user, company_id):
        raise _nao_encontrado("Empresa nao encontrada ou acesso negado.")


def assegurar_acesso_projeto(db: Session, current_user, projeto_id: int) -> models.Projeto:
    projeto = (
        db.query(models.Projeto)
        .filter(models.Projeto.id == projeto_id, filtro_projeto_acessivel(current_user))
        .first()
    )
    if projeto is None:
        # ADR-039: o cliente ve 404 em qualquer caso; a trilha distingue
        # inexistente / sem ACL / outro tenant (este com severidade alta).
        from pesquisa360.services import auditoria

        auditoria.registrar_negacao_projeto(db, current_user, projeto_id)
        raise _nao_encontrado("Projeto nao encontrado ou acesso negado.")
    return projeto


def aplicar_filtro_projetos_acessiveis(query, current_user):
    """Aplica o recorte de ACL a uma query que ja envolve `models.Projeto`."""
    return query.filter(filtro_projeto_acessivel(current_user))


# --- Tenant do dado operacional ----------------------------------------------


def company_id_da_pesquisa(db: Session, pesquisa_id: int) -> Optional[int]:
    """Tenant que o dado operacional deve carregar: Pesquisa -> Projeto -> Company.

    NUNCA usar `current_user.company_id` para isso: um agente multiempresa
    coletando no projeto da Empresa B gravaria o dado como da Empresa A.
    """
    linha = (
        db.query(models.Projeto.company_id)
        .join(models.Pesquisa, models.Pesquisa.projeto_id == models.Projeto.id)
        .filter(models.Pesquisa.id == pesquisa_id)
        .first()
    )
    return linha[0] if linha else None


def agente_pode_operar_projeto(db: Session, agente, projeto_id: int) -> bool:
    """Agente ativo com ACL no projeto. Vinculo de setor/missao e verificado
    separadamente pelas regras operacionais que ja existiam."""
    if not getattr(agente, "ativo", True):
        return False
    return usuario_tem_acesso_projeto(db, agente, projeto_id)


# --- Administracao da ACL -----------------------------------------------------


def acessos_do_usuario(db: Session, usuario: models.Usuario) -> dict:
    empresas = (
        db.query(models.UsuarioEmpresaAcesso, models.Company.name)
        .join(models.Company, models.Company.id == models.UsuarioEmpresaAcesso.company_id)
        .filter(models.UsuarioEmpresaAcesso.usuario_id == usuario.id)
        .order_by(models.UsuarioEmpresaAcesso.company_id)
        .all()
    )
    # Um SELECT para todos os projetos explicitos: nada de consulta por empresa.
    projetos = (
        db.query(models.UsuarioProjetoAcesso.projeto_id, models.Projeto.company_id)
        .join(models.Projeto, models.Projeto.id == models.UsuarioProjetoAcesso.projeto_id)
        .filter(
            models.UsuarioProjetoAcesso.usuario_id == usuario.id,
            models.UsuarioProjetoAcesso.ativo.is_(True),
        )
        .order_by(models.UsuarioProjetoAcesso.projeto_id)
        .all()
    )
    por_company: dict[int, list[int]] = {}
    for projeto_id, company_id in projetos:
        por_company.setdefault(company_id, []).append(projeto_id)

    return {
        "usuario_id": usuario.id,
        "empresa_principal_id": usuario.company_id,
        "empresas": [
            {
                "company_id": acesso.company_id,
                "company_nome": nome,
                "acesso_todos_projetos": bool(acesso.acesso_todos_projetos),
                "ativo": bool(acesso.ativo),
                "principal": bool(acesso.principal),
                "projeto_ids": por_company.get(acesso.company_id, []),
            }
            for acesso, nome in empresas
        ],
    }


def _validar_payload_acessos(db: Session, payload) -> dict[int, list[int]]:
    company_ids = [item.company_id for item in payload.empresas]
    if len(company_ids) != len(set(company_ids)):
        raise HTTPException(status_code=422, detail="Empresa repetida na configuracao de acessos.")

    existentes = {
        linha[0]
        for linha in db.query(models.Company.id)
        .filter(models.Company.id.in_(company_ids or [-1]))
        .all()
    }
    faltando = set(company_ids) - existentes
    if faltando:
        raise HTTPException(status_code=422, detail=f"Empresa inexistente: {sorted(faltando)}.")

    inativas = {
        linha[0]
        for linha in db.query(models.Company.id)
        .filter(models.Company.id.in_(company_ids or [-1]), models.Company.is_active.is_(False))
        .all()
    }
    if inativas:
        raise HTTPException(status_code=422, detail=f"Empresa inativa: {sorted(inativas)}.")

    if payload.empresa_principal_id is not None and payload.empresa_principal_id not in company_ids:
        raise HTTPException(
            status_code=422,
            detail="A empresa principal precisa estar entre as empresas autorizadas.",
        )

    projetos_por_company: dict[int, list[int]] = {}
    todos_projetos: list[int] = []
    for item in payload.empresas:
        ids = list(item.projeto_ids or [])
        if len(ids) != len(set(ids)):
            raise HTTPException(status_code=422, detail="Projeto repetido na configuracao.")
        projetos_por_company[item.company_id] = ids
        todos_projetos.extend(ids)

    if todos_projetos:
        # Um unico SELECT valida existencia E pertencimento a empresa informada.
        reais = {
            projeto_id: company_id
            for projeto_id, company_id in db.query(models.Projeto.id, models.Projeto.company_id)
            .filter(models.Projeto.id.in_(todos_projetos))
            .all()
        }
        for company_id, ids in projetos_por_company.items():
            for projeto_id in ids:
                if projeto_id not in reais:
                    raise HTTPException(
                        status_code=422, detail=f"Projeto inexistente: {projeto_id}."
                    )
                if reais[projeto_id] != company_id:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"Projeto {projeto_id} nao pertence a empresa {company_id}."
                        ),
                    )
    return projetos_por_company


def substituir_acessos(db: Session, usuario: models.Usuario, payload) -> dict:
    """Reescreve a ACL do usuario em UMA transacao.

    Ou tudo e valido e tudo e gravado, ou nada muda: a validacao inteira roda
    antes de qualquer escrita, e a excecao aborta sem commit parcial.
    """
    projetos_por_company = _validar_payload_acessos(db, payload)
    company_ids = [item.company_id for item in payload.empresas]

    try:
        db.query(models.UsuarioProjetoAcesso).filter(
            models.UsuarioProjetoAcesso.usuario_id == usuario.id
        ).delete(synchronize_session=False)
        db.query(models.UsuarioEmpresaAcesso).filter(
            models.UsuarioEmpresaAcesso.usuario_id == usuario.id
        ).delete(synchronize_session=False)

        principal_id = payload.empresa_principal_id
        if not payload.empresas and usuario.company_id is not None:
            # Lista vazia e uma decisao administrativa: deixa a marca (inativa)
            # para o usuario NAO recair no comportamento legado.
            db.add(
                models.UsuarioEmpresaAcesso(
                    usuario_id=usuario.id,
                    company_id=usuario.company_id,
                    acesso_todos_projetos=False,
                    ativo=False,
                    principal=False,
                )
            )
        for item in payload.empresas:
            db.add(
                models.UsuarioEmpresaAcesso(
                    usuario_id=usuario.id,
                    company_id=item.company_id,
                    acesso_todos_projetos=bool(item.acesso_todos_projetos),
                    ativo=True,
                    principal=item.company_id == principal_id,
                )
            )
            for projeto_id in projetos_por_company.get(item.company_id, []):
                db.add(
                    models.UsuarioProjetoAcesso(
                        usuario_id=usuario.id, projeto_id=projeto_id, ativo=True
                    )
                )

        # Empresa principal (legado/default) acompanha a ACL para nao apontar
        # para uma empresa que o usuario nao acessa mais.
        if principal_id is not None:
            usuario.company_id = principal_id
        elif company_ids and usuario.company_id not in company_ids:
            usuario.company_id = company_ids[0]
        db.add(usuario)
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(usuario)
    from pesquisa360.services import auditoria

    auditoria.registrar(
        auditoria.ACL_CHANGED, auditoria.SEV_INFO, user_id=usuario.id, company_id=usuario.company_id,
        details={
            "empresas": [item.company_id for item in payload.empresas],
            "projetos": sorted({pid for lista in projetos_por_company.values() for pid in lista}),
        },
    )
    return acessos_do_usuario(db, usuario)


def garantir_acesso_principal(db: Session, usuario: models.Usuario) -> None:
    """Cria o vinculo default de um usuario recem-criado (mesma semantica do
    backfill: acesso total a propria empresa principal)."""
    if usuario.company_id is None:
        return
    existente = (
        db.query(models.UsuarioEmpresaAcesso)
        .filter(
            models.UsuarioEmpresaAcesso.usuario_id == usuario.id,
            models.UsuarioEmpresaAcesso.company_id == usuario.company_id,
        )
        .first()
    )
    if existente is not None:
        return
    db.add(
        models.UsuarioEmpresaAcesso(
            usuario_id=usuario.id,
            company_id=usuario.company_id,
            acesso_todos_projetos=True,
            ativo=True,
            principal=True,
        )
    )
    db.commit()


def usuarios_com_acesso_ao_projeto(db: Session, projeto_id: int, perfil_nome: Optional[str] = None):
    """Usuarios autorizados NAQUELE projeto (nao "da empresa do solicitante")."""
    projeto = db.query(models.Projeto).filter(models.Projeto.id == projeto_id).first()
    if projeto is None:
        return []
    query = (
        db.query(models.Usuario)
        .join(
            models.UsuarioEmpresaAcesso,
            models.UsuarioEmpresaAcesso.usuario_id == models.Usuario.id,
        )
        .filter(
            models.UsuarioEmpresaAcesso.company_id == projeto.company_id,
            models.UsuarioEmpresaAcesso.ativo.is_(True),
            models.Usuario.ativo.is_(True),
            or_(
                models.UsuarioEmpresaAcesso.acesso_todos_projetos.is_(True),
                models.Usuario.id.in_(
                    select(models.UsuarioProjetoAcesso.usuario_id).where(
                        models.UsuarioProjetoAcesso.projeto_id == projeto_id,
                        models.UsuarioProjetoAcesso.ativo.is_(True),
                    )
                ),
            ),
        )
    )
    if perfil_nome:
        query = query.join(models.Perfil, models.Perfil.id == models.Usuario.perfil_id).filter(
            models.Perfil.nome == perfil_nome
        )
    return query.distinct().order_by(models.Usuario.nome).all()


def ids_com_acesso_ao_projeto(db: Session, projeto_id: int, usuario_ids: Iterable[int]) -> set[int]:
    """Quais dos ids informados tem ACL no projeto (uma consulta para o lote)."""
    ids = list(dict.fromkeys(usuario_ids))
    if not ids:
        return set()
    projeto = db.query(models.Projeto).filter(models.Projeto.id == projeto_id).first()
    if projeto is None:
        return set()
    linhas = (
        db.query(models.UsuarioEmpresaAcesso.usuario_id)
        .filter(
            models.UsuarioEmpresaAcesso.usuario_id.in_(ids),
            models.UsuarioEmpresaAcesso.company_id == projeto.company_id,
            models.UsuarioEmpresaAcesso.ativo.is_(True),
            or_(
                models.UsuarioEmpresaAcesso.acesso_todos_projetos.is_(True),
                models.UsuarioEmpresaAcesso.usuario_id.in_(
                    select(models.UsuarioProjetoAcesso.usuario_id).where(
                        models.UsuarioProjetoAcesso.projeto_id == projeto_id,
                        models.UsuarioProjetoAcesso.ativo.is_(True),
                    )
                ),
            ),
        )
        .all()
    )
    autorizados = {linha[0] for linha in linhas}

    # Mesmo fallback do filtro principal: quem NUNCA teve ACL gravada continua
    # valendo pela empresa principal. Sem isto, um agente legado deixaria de
    # poder ser vinculado ao setor do proprio tenant.
    restantes = [uid for uid in ids if uid not in autorizados]
    if restantes:
        com_acl = {
            linha[0]
            for linha in db.query(models.UsuarioEmpresaAcesso.usuario_id)
            .filter(models.UsuarioEmpresaAcesso.usuario_id.in_(restantes))
            .all()
        }
        legado = (
            db.query(models.Usuario.id)
            .filter(
                models.Usuario.id.in_([uid for uid in restantes if uid not in com_acl] or [-1]),
                models.Usuario.company_id == projeto.company_id,
            )
            .all()
        )
        autorizados |= {linha[0] for linha in legado}
    return autorizados
