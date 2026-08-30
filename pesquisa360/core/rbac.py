"""Autorizacao por PERFIL (ADR-037).

Tres perguntas independentes decidem cada request:

    QUEM E?              autenticacao   -> get_current_user
    DE QUAL EMPRESA?     tenant         -> Projeto.company_id
    PODE VER ESTE?       escopo/ACL     -> services.acessos (ADR-034)
    PODE FAZER ISTO?     capacidade     -> este modulo

Perfil e ACL nao se substituem. Um Cliente com `RELATORIO_VER` continua vendo
somente os projetos autorizados a ele; um Gerente com acesso a um projeto
continua sem poder administrar empresas. Capacidade e escopo sao ortogonais.

A matriz vive em codigo, nao em tabela: as capacidades sao poucas, estaveis e
precisam ser revisadas em code review. Uma tabela de permissoes editavel seria
um IAM completo -- outro projeto.

Codigo de status:

* 404 quando o recurso esta FORA do escopo (outro tenant/projeto sem ACL) --
  negar nao pode revelar que o recurso existe;
* 403 quando o recurso e visivel mas a OPERACAO e proibida ao perfil. Aqui nao
  ha vazamento: o usuario ja sabe que o recurso existe.
"""
from __future__ import annotations

from enum import Enum
from typing import Iterable, Optional

from fastapi import Depends, HTTPException, status

from sqlalchemy.orm import Session

from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.db import models


class Papel(str, Enum):
    SUPERADMIN = "SUPERADMIN"
    GERENTE = "GERENTE"
    COORDENADOR = "COORDENADOR"
    SUPERVISOR = "SUPERVISOR"
    CLIENTE = "CLIENTE"
    AGENTE = "AGENTE"


class Permissao(str, Enum):
    """Capacidades semanticas -- nomeadas pela OPERACAO, nao pela rota."""

    PROJETO_VER = "PROJETO_VER"
    PROJETO_CRIAR = "PROJETO_CRIAR"
    PROJETO_EDITAR = "PROJETO_EDITAR"
    PROJETO_EXCLUIR = "PROJETO_EXCLUIR"

    PESQUISA_VER = "PESQUISA_VER"
    PESQUISA_GERENCIAR = "PESQUISA_GERENCIAR"   # criar/editar/excluir + perguntas + geofence

    TERRITORIO_VER = "TERRITORIO_VER"
    TERRITORIO_GERENCIAR = "TERRITORIO_GERENCIAR"   # setores, cotas, composicao, base do projeto

    CAMPO_MONITORAR = "CAMPO_MONITORAR"          # monitoramento, controle de campo, cobertura
    RELATORIO_VER = "RELATORIO_VER"
    RELATORIO_CONFIGURAR = "RELATORIO_CONFIGURAR"   # configuracoes executivas, apuracao espontanea
    INTELIGENCIA_VER = "INTELIGENCIA_VER"

    LIDERANCA_VER = "LIDERANCA_VER"
    LIDERANCA_GERENCIAR = "LIDERANCA_GERENCIAR"

    BASE_ELEITORAL_VER = "BASE_ELEITORAL_VER"
    BASE_ELEITORAL_GERENCIAR = "BASE_ELEITORAL_GERENCIAR"

    USUARIO_GERENCIAR = "USUARIO_GERENCIAR"
    EMPRESA_GERENCIAR = "EMPRESA_GERENCIAR"

    COLETA_ENVIAR = "COLETA_ENVIAR"              # sincronizacao de campo (Mobile)
    MISSAO_SINCRONIZAR = "MISSAO_SINCRONIZAR"


_TODAS = set(Permissao)

# Leitura operacional comum a quem trabalha dentro do projeto.
_LEITURA_PROJETO = {
    Permissao.PROJETO_VER,
    Permissao.PESQUISA_VER,
    Permissao.TERRITORIO_VER,
    Permissao.RELATORIO_VER,
    Permissao.INTELIGENCIA_VER,
    Permissao.CAMPO_MONITORAR,
    Permissao.LIDERANCA_VER,
    Permissao.BASE_ELEITORAL_VER,
}

# Gestao completa do conteudo de um projeto (sem administrar o tenant).
_GESTAO_PROJETO = _LEITURA_PROJETO | {
    Permissao.PROJETO_EDITAR,
    Permissao.PESQUISA_GERENCIAR,
    Permissao.TERRITORIO_GERENCIAR,
    Permissao.RELATORIO_CONFIGURAR,
    Permissao.LIDERANCA_GERENCIAR,
    Permissao.BASE_ELEITORAL_GERENCIAR,
    Permissao.COLETA_ENVIAR,
}

MATRIZ: dict[Papel, set[Permissao]] = {
    # Administra a plataforma inteira. Mantido como estava.
    Papel.SUPERADMIN: set(_TODAS),
    # Dono do tenant: gestao completa + usuarios + criar/excluir projeto.
    Papel.GERENTE: _GESTAO_PROJETO | {
        Permissao.PROJETO_CRIAR,
        Permissao.PROJETO_EXCLUIR,
        Permissao.USUARIO_GERENCIAR,
        Permissao.MISSAO_SINCRONIZAR,
    },
    # Toca o projeto de ponta a ponta, mas nao administra o tenant nem cria ou
    # apaga projeto -- isso e decisao de quem responde pelo contrato.
    Papel.COORDENADOR: set(_GESTAO_PROJETO),
    # Operacao de campo: monitora, organiza setores/cotas e le relatorios.
    # Nao mexe em questionario nem na estrutura administrativa (§SUPERVISOR:
    # "sem regra consolidada, manter leitura").
    Papel.SUPERVISOR: _LEITURA_PROJETO | {
        Permissao.TERRITORIO_GERENCIAR,
        Permissao.COLETA_ENVIAR,
    },
    # READ ONLY. Ve resultado, nunca escreve -- nem no proprio projeto.
    Papel.CLIENTE: {
        Permissao.PROJETO_VER,
        Permissao.PESQUISA_VER,
        Permissao.TERRITORIO_VER,
        Permissao.RELATORIO_VER,
        Permissao.INTELIGENCIA_VER,
        Permissao.CAMPO_MONITORAR,
        Permissao.LIDERANCA_VER,
        Permissao.BASE_ELEITORAL_VER,
    },
    # Campo. Nao usa o Web administrativo: so missao e sincronizacao.
    Papel.AGENTE: {
        Permissao.MISSAO_SINCRONIZAR,
        Permissao.COLETA_ENVIAR,
    },
}

_ALIAS = {
    "superadmin": Papel.SUPERADMIN,
    "super admin": Papel.SUPERADMIN,
    "gerente": Papel.GERENTE,
    "coordenador": Papel.COORDENADOR,
    "coordenadora": Papel.COORDENADOR,
    "supervisor": Papel.SUPERVISOR,
    "supervisora": Papel.SUPERVISOR,
    "cliente": Papel.CLIENTE,
    "agente": Papel.AGENTE,
}


def papel_de_nome(nome: Optional[str]) -> Optional[Papel]:
    """Normaliza o nome do perfil para um papel conhecido."""
    if not nome:
        return None
    return _ALIAS.get(str(nome).strip().casefold())


def papel_do_usuario(user) -> Optional[Papel]:
    """Papel a partir do NOME do perfil, nunca do id.

    Ids de perfil variam por instalacao (o seed nao garante ordem); o nome e o
    que o dominio usa em toda parte -- inclusive nas checagens que ja existiam.
    Perfil desconhecido devolve None e, por consequencia, nao recebe permissao
    nenhuma: na duvida, negar.
    """
    if user is None:
        return None
    # `perfil` pode ser lazy: ler dispara SELECT. Se a leitura falhar, seguimos
    # com o que houver em memoria em vez de estourar 500 no meio da checagem.
    nome = None
    try:
        perfil = getattr(user, "perfil", None)
        nome = getattr(perfil, "nome", None)
    except Exception:
        nome = None
    if not nome:
        try:
            nome = getattr(user, "perfil_nome", None)
        except Exception:
            nome = None
    if not nome:
        return None
    return _ALIAS.get(str(nome).strip().casefold())


def permissoes_do_usuario(user) -> set[Permissao]:
    papel = papel_do_usuario(user)
    if papel is None:
        return set()
    return set(MATRIZ.get(papel, set()))


def tem_permissao(user, permissao: Permissao) -> bool:
    if user is None or getattr(user, "ativo", None) is False:
        return False
    return permissao in permissoes_do_usuario(user)


def _auditar_negacao(user, permissoes) -> None:
    """ADR-039. Import tardio: auditoria importa models, nao este modulo."""
    from pesquisa360.services import auditoria

    auditoria.registrar(
        auditoria.RBAC_DENIED, auditoria.SEV_WARNING, user=user, status_code=403,
        details={"permissoes_exigidas": [p.value for p in permissoes], "papel": papel_nome(user)},
    )


def assegurar_permissao(user, permissao: Permissao) -> None:
    """403: o recurso pode ser visivel, mas a operacao nao e do perfil."""
    if not tem_permissao(user, permissao):
        _auditar_negacao(user, [permissao])
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seu perfil nao permite esta operacao.",
        )


def require_permissao(*permissoes: Permissao):
    """Dependency de rota. Aceita varias permissoes (basta UMA).

    Usada em `dependencies=[...]` para nao alterar a assinatura dos endpoints
    -- o RBAC entra como camada, sem reescrever o corpo de 100 rotas.
    """

    def _verificar(
        db: Session = Depends(get_db),
        current_user: models.Usuario = Depends(get_current_user),
    ) -> models.Usuario:
        if any(tem_permissao(current_user, p) for p in permissoes):
            return current_user

        # Usuario entregue apenas com `perfil_id` (relacionamento nao
        # carregado): resolve o NOME pelo banco. Seleciona so a coluna `nome` --
        # carregar a entidade inteira exigiria colunas que nem todo caminho
        # precisa, e o papel nunca dependeu delas.
        if papel_do_usuario(current_user) is None:
            perfil_id = getattr(current_user, "perfil_id", None)
            nome = None
            if perfil_id is not None:
                nome = (
                    db.query(models.Perfil.nome)
                    .filter(models.Perfil.id == perfil_id)
                    .scalar()
                )
            papel = papel_de_nome(nome)
            if papel is not None and any(p in MATRIZ.get(papel, set()) for p in permissoes):
                if getattr(current_user, "ativo", None) is not False:
                    return current_user

        _auditar_negacao(current_user, permissoes)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seu perfil nao permite esta operacao.",
        )

    return _verificar


def papeis_com(permissao: Permissao) -> set[Papel]:
    """Util para documentacao e teste da matriz."""
    return {papel for papel, permissoes in MATRIZ.items() if permissao in permissoes}


def resumo_permissoes(user) -> list[str]:
    """Lista ordenada para o `/usuarios/me/` -- o Web apenas REPRESENTA isto."""
    return sorted(p.value for p in permissoes_do_usuario(user))


def papel_nome(user) -> Optional[str]:
    papel = papel_do_usuario(user)
    return papel.value if papel else None
