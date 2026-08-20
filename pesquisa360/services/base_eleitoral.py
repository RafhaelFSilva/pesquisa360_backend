"""Servico de dominio da Base Eleitoral.

Concentra visibilidade multitenant, vinculo com o Projeto, maquina de estados e
resolucao humana de divergencia. O parsing/persistencia de lote vive em
`base_eleitoral_import.py`; a absorcao do legado em `base_eleitoral_legado.py`.

Regras fixadas aqui (ADR-023):
  company_id IS NULL -> base oficial/global, visivel por todos os tenants;
  company_id = N     -> base privada do tenant N;
  Projeto e a ancora da campanha; Pesquisa nao se vincula a base;
  base de outro tenant e tratada como inexistente (404), nunca 403.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from pesquisa360.core.dependencies import _get_profile_name, _is_manager_name, _is_superadmin_name
from pesquisa360.db import models

STATUS_IMPORTADA = "IMPORTADA"
STATUS_EM_CONFERENCIA = "EM_CONFERENCIA"
STATUS_VALIDADA = "VALIDADA"
STATUS_SUBSTITUIDA = "SUBSTITUIDA"


class BaseEleitoralNaoValidadaError(Exception):
    """Base sem validacao humana nao pode alimentar calculo eleitoral."""

    def __init__(self, base_eleitoral_id: int, status_atual: str):
        self.base_eleitoral_id = base_eleitoral_id
        self.status_atual = status_atual
        super().__init__(
            "Base eleitoral {} esta em {} e nao pode ser usada para calculo; "
            "exige status VALIDADA.".format(base_eleitoral_id, status_atual)
        )


def _nao_encontrada(entidade: str = "Base eleitoral") -> HTTPException:
    # 404 tambem para base de outro tenant: nao revelar que o recurso existe.
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{entidade} nao encontrada.")


def _agora() -> datetime:
    return datetime.now(timezone.utc)


# --- Visibilidade -------------------------------------------------------------


def filtro_visibilidade_base_eleitoral(company_id: Optional[int]):
    """Expressao canonica de visibilidade. Nunca use SELECT sem ela."""
    return or_(
        models.BaseEleitoral.company_id.is_(None),
        models.BaseEleitoral.company_id == company_id,
    )


def listar_bases_eleitorais_visiveis(
    db: Session,
    current_user: models.Usuario,
    *,
    status_filtro: Optional[str] = None,
    uf: Optional[str] = None,
    ano: Optional[int] = None,
):
    """Bases oficiais mais as privadas do proprio tenant. company_id vem do JWT."""
    query = db.query(models.BaseEleitoral).filter(
        filtro_visibilidade_base_eleitoral(current_user.company_id)
    )
    if status_filtro:
        query = query.filter(models.BaseEleitoral.status == status_filtro)
    if uf:
        query = query.filter(models.BaseEleitoral.uf == uf.strip().upper())
    if ano is not None:
        query = query.filter(models.BaseEleitoral.ano == ano)
    return query.order_by(
        models.BaseEleitoral.ano.desc(),
        models.BaseEleitoral.uf,
        models.BaseEleitoral.versao,
        models.BaseEleitoral.id,
    ).all()


def obter_base_eleitoral_visivel(
    db: Session, base_id: int, current_user: models.Usuario
) -> models.BaseEleitoral:
    base = (
        db.query(models.BaseEleitoral)
        .filter(
            models.BaseEleitoral.id == base_id,
            filtro_visibilidade_base_eleitoral(current_user.company_id),
        )
        .first()
    )
    if base is None:
        raise _nao_encontrada()
    return base


# --- Permissoes ---------------------------------------------------------------


def assegurar_permissao_escrita_base(
    db: Session, base: models.BaseEleitoral, current_user: models.Usuario
) -> None:
    """Base oficial exige Superadmin; base privada exige Gerente/Superadmin do dono.

    Reutiliza os perfis ja existentes (superadmin/gerente/agente): nenhum perfil
    novo e criado. Agente nunca importa nem valida base eleitoral.
    """
    perfil = _get_profile_name(db, current_user)
    if base.company_id is None:
        if not _is_superadmin_name(perfil):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Base eleitoral oficial e restrita a Superadmin.",
            )
        return

    if base.company_id != current_user.company_id:
        # Nao deveria chegar aqui apos obter_base_eleitoral_visivel, mas mantem
        # a resposta 404 caso a funcao seja chamada isoladamente.
        raise _nao_encontrada()

    if not (_is_superadmin_name(perfil) or _is_manager_name(perfil)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso restrito a Gerente ou Superadmin.",
        )


# --- Vinculo Projeto <-> Base -------------------------------------------------


def _obter_projeto_do_tenant(
    db: Session, projeto_id: int, current_user: models.Usuario
) -> models.Projeto:
    projeto = (
        db.query(models.Projeto)
        .filter(
            models.Projeto.id == projeto_id,
            models.Projeto.company_id == current_user.company_id,
        )
        .first()
    )
    if projeto is None:
        raise _nao_encontrada("Projeto")
    return projeto


def _assegurar_base_elegivel_para_projeto(
    base: models.BaseEleitoral, projeto: models.Projeto
) -> None:
    """Projeto do tenant A so usa base oficial ou privada do proprio A.

    Esta regra nao e expressa por FK (exigiria duplicar company_id no vinculo),
    entao vive obrigatoriamente aqui, conforme ADR-023.
    """
    if base.company_id is not None and base.company_id != projeto.company_id:
        raise _nao_encontrada()


def vincular_base_eleitoral_ao_projeto(
    db: Session,
    projeto_id: int,
    base_eleitoral_id: int,
    current_user: models.Usuario,
    principal: bool = True,
) -> models.ProjetoBaseEleitoral:
    projeto = _obter_projeto_do_tenant(db, projeto_id, current_user)
    base = obter_base_eleitoral_visivel(db, base_eleitoral_id, current_user)
    _assegurar_base_elegivel_para_projeto(base, projeto)

    existente = (
        db.query(models.ProjetoBaseEleitoral)
        .filter(
            models.ProjetoBaseEleitoral.projeto_id == projeto.id,
            models.ProjetoBaseEleitoral.base_eleitoral_id == base.id,
        )
        .first()
    )

    try:
        if principal:
            # Troca atomica: a principal anterior vira historico, nunca e apagada.
            db.query(models.ProjetoBaseEleitoral).filter(
                models.ProjetoBaseEleitoral.projeto_id == projeto.id,
                models.ProjetoBaseEleitoral.principal.is_(True),
                models.ProjetoBaseEleitoral.base_eleitoral_id != base.id,
            ).update({models.ProjetoBaseEleitoral.principal: False}, synchronize_session=False)

        if existente is None:
            vinculo = models.ProjetoBaseEleitoral(
                projeto_id=projeto.id,
                base_eleitoral_id=base.id,
                principal=principal,
            )
            db.add(vinculo)
        else:
            vinculo = existente
            vinculo.principal = principal
            db.add(vinculo)
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(vinculo)
    return vinculo


def obter_base_principal_projeto_opcional(
    db: Session, projeto_id: int, current_user: models.Usuario
) -> Optional[models.BaseEleitoral]:
    """Base principal do projeto, ou None quando ainda nao ha vinculo.

    Projeto inexistente ou de outro tenant continua sendo 404: ausencia de
    vinculo e um estado funcional normal e nao deve ser confundida com projeto
    invisivel. Usada pelo Workspace/conferencia, nunca pelo caminho de calculo.
    """
    projeto = _obter_projeto_do_tenant(db, projeto_id, current_user)
    base = (
        db.query(models.BaseEleitoral)
        .join(
            models.ProjetoBaseEleitoral,
            models.ProjetoBaseEleitoral.base_eleitoral_id == models.BaseEleitoral.id,
        )
        .filter(
            models.ProjetoBaseEleitoral.projeto_id == projeto.id,
            models.ProjetoBaseEleitoral.principal.is_(True),
            filtro_visibilidade_base_eleitoral(current_user.company_id),
        )
        .first()
    )
    if base is None:
        return None
    _assegurar_base_elegivel_para_projeto(base, projeto)
    return base


def _obter_base_principal(
    db: Session, projeto_id: int, current_user: models.Usuario
) -> models.BaseEleitoral:
    base = obter_base_principal_projeto_opcional(db, projeto_id, current_user)
    if base is None:
        raise _nao_encontrada("Base eleitoral principal do projeto")
    return base


def obter_base_principal_projeto_para_conferencia(
    db: Session, projeto_id: int, current_user: models.Usuario
) -> models.BaseEleitoral:
    """Leitura de conferencia: aceita IMPORTADA, EM_CONFERENCIA e VALIDADA."""
    return _obter_base_principal(db, projeto_id, current_user)


def obter_base_principal_projeto_para_calculo(
    db: Session, projeto_id: int, current_user: models.Usuario
) -> models.BaseEleitoral:
    """Porta de entrada dos calculos das Fases 4+. Exige validacao humana.

    Nao retorna base IMPORTADA/EM_CONFERENCIA silenciosamente, nem SUBSTITUIDA:
    leitura historica e outra operacao, e nao passa por aqui.
    """
    base = _obter_base_principal(db, projeto_id, current_user)
    if base.status != STATUS_VALIDADA:
        raise BaseEleitoralNaoValidadaError(base.id, base.status)
    return base


# --- Maquina de estados -------------------------------------------------------


def contar_territorios_em_conferencia(db: Session, base_id: int) -> int:
    return (
        db.query(func.count(models.TerritorioEleitoral.id))
        .filter(
            models.TerritorioEleitoral.base_eleitoral_id == base_id,
            models.TerritorioEleitoral.status_validacao == STATUS_EM_CONFERENCIA,
        )
        .scalar()
        or 0
    )


def validar_base_eleitoral(
    db: Session, base_id: int, current_user: models.Usuario
) -> models.BaseEleitoral:
    """Promove a base para VALIDADA. Somente por acao humana explicita."""
    base = obter_base_eleitoral_visivel(db, base_id, current_user)
    assegurar_permissao_escrita_base(db, base, current_user)

    if base.status == STATUS_SUBSTITUIDA:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Base substituida nao pode ser validada.",
        )

    pendentes = contar_territorios_em_conferencia(db, base.id)
    if pendentes > 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Existem {} territorios em conferencia. "
                "Resolva as divergencias antes de validar.".format(pendentes)
            ),
        )

    base.status = STATUS_VALIDADA
    db.add(base)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(base)
    return base


# --- Territorios e divergencias ----------------------------------------------


def listar_territorios(
    db: Session,
    base_id: int,
    current_user: models.Usuario,
    *,
    tipo: Optional[str] = None,
    status_validacao: Optional[str] = None,
    municipio_id: Optional[int] = None,
    q: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    base = obter_base_eleitoral_visivel(db, base_id, current_user)
    query = _aplicar_filtros_territorio(
        db,
        base.id,
        tipo=tipo,
        status_validacao=status_validacao,
        municipio_id=municipio_id,
        q=q,
    )
    return (
        query.order_by(
            models.TerritorioEleitoral.tipo,
            models.TerritorioEleitoral.nome,
            models.TerritorioEleitoral.id,
        )
        .offset(_normalizar_offset(offset))
        .limit(_normalizar_limit(limit))
        .all()
    )


def _normalizar_limit(limit: int) -> int:
    return max(min(limit, 500), 1)


def _normalizar_offset(offset: int) -> int:
    return max(offset, 0)


def _aplicar_filtros_territorio(
    db: Session,
    base_id: int,
    *,
    tipo: Optional[str] = None,
    status_validacao: Optional[str] = None,
    municipio_id: Optional[int] = None,
    q: Optional[str] = None,
):
    """Filtros compartilhados entre a pagina e o total.

    Contar com filtros diferentes dos itens produziria uma paginacao mentirosa,
    entao os dois caminhos passam obrigatoriamente por aqui.
    """
    # Geometria nao trafega crua (ADR-004): esta listagem e alfanumerica.
    query = db.query(models.TerritorioEleitoral).filter(
        models.TerritorioEleitoral.base_eleitoral_id == base_id
    )
    if tipo:
        query = query.filter(models.TerritorioEleitoral.tipo == tipo)
    if status_validacao:
        query = query.filter(models.TerritorioEleitoral.status_validacao == status_validacao)
    if municipio_id is not None:
        query = query.filter(models.TerritorioEleitoral.municipio_id == municipio_id)
    if q:
        from pesquisa360.services.base_eleitoral_import import normalizar_nome_territorio

        chave = normalizar_nome_territorio(q)
        if chave:
            query = query.filter(models.TerritorioEleitoral.nome_normalizado.contains(chave))
    return query


def contar_territorios(
    db: Session,
    base_id: int,
    current_user: models.Usuario,
    *,
    tipo: Optional[str] = None,
    status_validacao: Optional[str] = None,
    municipio_id: Optional[int] = None,
    q: Optional[str] = None,
) -> int:
    """Total apos os filtros e antes de limit/offset."""
    base = obter_base_eleitoral_visivel(db, base_id, current_user)
    return _aplicar_filtros_territorio(
        db,
        base.id,
        tipo=tipo,
        status_validacao=status_validacao,
        municipio_id=municipio_id,
        q=q,
    ).count()


def contar_territorios_por_tipo(db: Session, base_id: int) -> dict:
    """GROUP BY no banco: nao carrega os territorios para contar em Python."""
    linhas = (
        db.query(models.TerritorioEleitoral.tipo, func.count(models.TerritorioEleitoral.id))
        .filter(models.TerritorioEleitoral.base_eleitoral_id == base_id)
        .group_by(models.TerritorioEleitoral.tipo)
        .all()
    )
    contagem = {tipo: 0 for tipo in models.TIPOS_TERRITORIO_ELEITORAL}
    for tipo, total in linhas:
        contagem[tipo] = int(total or 0)
    return contagem


def obter_raiz_estado(db: Session, base_id: int) -> Optional[models.TerritorioEleitoral]:
    """Raiz ESTADO da base.

    Saber que o total da base vive no no ESTADO e regra de dominio; o cliente
    nao deve precisar conhece-la.
    """
    return (
        db.query(models.TerritorioEleitoral)
        .filter(
            models.TerritorioEleitoral.base_eleitoral_id == base_id,
            models.TerritorioEleitoral.tipo == "ESTADO",
        )
        .order_by(models.TerritorioEleitoral.id)
        .first()
    )


def listar_importacoes(
    db: Session, base_id: int, current_user: models.Usuario
) -> list[models.ImportacaoBaseEleitoral]:
    """Lotes de importacao da base, em ordem de execucao.

    Nao ha unicidade garantida de importacao por base (nenhuma constraint impede
    dois lotes na mesma versao), entao o backend NAO elege uma "importacao de
    origem" arbitrariamente: devolve a lista auditavel e deixa a escolha
    explicita para quem consome.
    """
    base = obter_base_eleitoral_visivel(db, base_id, current_user)
    return (
        db.query(models.ImportacaoBaseEleitoral)
        .filter(models.ImportacaoBaseEleitoral.base_eleitoral_id == base.id)
        .order_by(models.ImportacaoBaseEleitoral.id)
        .all()
    )


def obter_auditoria_data_referencia(db: Session, base_id: int) -> Optional[dict]:
    """Bloco estruturado gravado na raiz ESTADO, quando existir.

    Nunca deduzido de `data_referencia == 01/01`: ou a auditoria foi persistida
    na importacao, ou nao ha auditoria.
    """
    raiz = obter_raiz_estado(db, base_id)
    if raiz is None:
        return None
    bloco = (raiz.metadados or {}).get("data_referencia_auditoria")
    return bloco if isinstance(bloco, dict) else None


def _territorios_por_chave_importacao(
    db: Session, base_id: int, chaves: set[str]
) -> dict[str, models.TerritorioEleitoral]:
    """Casa divergencia -> territorio pela chave do lote, nunca pelo nome.

    Nome repete entre municipios; `chave_importacao` e o mesmo identificador
    usado por `_marcar_divergencia_resolvida`.
    """
    if not chaves:
        return {}
    territorios = (
        db.query(models.TerritorioEleitoral)
        .filter(models.TerritorioEleitoral.base_eleitoral_id == base_id)
        .all()
    )
    encontrados: dict[str, models.TerritorioEleitoral] = {}
    for territorio in territorios:
        chave = (territorio.metadados or {}).get("chave_importacao")
        if chave in chaves:
            encontrados[chave] = territorio
    return encontrados


def _composicao_valor_final(
    territorio: models.TerritorioEleitoral,
    filhos: list[models.TerritorioEleitoral],
    registro: dict,
) -> Optional[dict]:
    """Explica como o valor operacional do territorio foi formado.

    Read model puro: nada e persistido. A ancora dos ajustes e
    `soma_filhos_original` (a soma registrada na conferencia), nunca o valor
    declarado pela fonte -- somar os ajustes sobre o declarado produziria
    semantica errada.
    """
    soma_filhos_original = registro.get("valor_detalhe")
    valor_resumo = registro.get("valor_resumo")
    if not isinstance(soma_filhos_original, int) or not isinstance(valor_resumo, int):
        # Divergencias que nao sao de conferencia resumo/detalhe nao tem composicao.
        return None

    computaveis = [f for f in filhos if f.eleitorado_apto is not None]
    soma_filhos_atual = sum(f.eleitorado_apto for f in computaveis) if computaveis else None

    ajustes_filhos = []
    for filho in sorted(filhos, key=lambda f: (f.nome or "")):
        anterior = filho.eleitorado_apto_origem
        atual = filho.eleitorado_apto
        if anterior is None or atual is None or anterior == atual:
            continue
        ajustes_filhos.append(
            {
                "territorio_id": filho.id,
                "territorio": filho.nome,
                "tipo": filho.tipo,
                "valor_anterior": anterior,
                "valor_final": atual,
                "ajuste": atual - anterior,
            }
        )

    resolucao = registro.get("resolucao") or {}
    valor_operacional_final = resolucao.get("valor_final")
    if not isinstance(valor_operacional_final, int):
        valor_operacional_final = None

    return {
        "soma_filhos_original": soma_filhos_original,
        "soma_filhos_atual": soma_filhos_atual,
        "ajuste_total_filhos": (
            None if soma_filhos_atual is None else soma_filhos_atual - soma_filhos_original
        ),
        "valor_operacional_final": valor_operacional_final,
        # Modulo: a UI escolhe a linguagem ("N eleitores a menos").
        "diferenca_final_fonte": (
            None
            if valor_operacional_final is None
            else abs(valor_resumo - valor_operacional_final)
        ),
        "ajustes_filhos": ajustes_filhos,
        "total_filhos": len(filhos),
    }


def listar_divergencias(db: Session, base_id: int, current_user: models.Usuario) -> list[dict]:
    base = obter_base_eleitoral_visivel(db, base_id, current_user)
    importacoes = (
        db.query(models.ImportacaoBaseEleitoral)
        .filter(models.ImportacaoBaseEleitoral.base_eleitoral_id == base.id)
        .order_by(models.ImportacaoBaseEleitoral.id)
        .all()
    )
    resultado: list[dict] = []
    for importacao in importacoes:
        for item in importacao.divergencias or []:
            registro = dict(item)
            registro["importacao_id"] = importacao.id
            registro["arquivo_origem"] = importacao.arquivo_origem
            resultado.append(registro)

    chaves = {
        registro["territorio_chave"]
        for registro in resultado
        if isinstance(registro.get("territorio_chave"), str)
    }
    territorios = _territorios_por_chave_importacao(db, base.id, chaves)
    filhos_por_pai: dict[int, list[models.TerritorioEleitoral]] = {}
    if territorios:
        ids_pais = [t.id for t in territorios.values()]
        for filho in (
            db.query(models.TerritorioEleitoral)
            .filter(
                models.TerritorioEleitoral.base_eleitoral_id == base.id,
                models.TerritorioEleitoral.parent_id.in_(ids_pais),
            )
            .all()
        ):
            filhos_por_pai.setdefault(filho.parent_id, []).append(filho)

    for registro in resultado:
        territorio = territorios.get(registro.get("territorio_chave"))
        if territorio is None:
            continue
        registro["territorio_id"] = territorio.id
        composicao = _composicao_valor_final(
            territorio, filhos_por_pai.get(territorio.id, []), registro
        )
        if composicao is not None:
            registro["composicao_valor_final"] = composicao
    return resultado


def resolver_divergencia_territorio(
    db: Session,
    territorio_id: int,
    valor_final: int,
    justificativa: str,
    current_user: models.Usuario,
) -> models.TerritorioEleitoral:
    """Resolucao humana da divergencia, com auditoria preservada.

    O valor declarado pela fonte permanece em `eleitorado_apto_origem` e a
    divergencia original continua no historico, apenas marcada como resolvida.
    """
    territorio = db.query(models.TerritorioEleitoral).filter(
        models.TerritorioEleitoral.id == territorio_id
    ).first()
    if territorio is None:
        raise _nao_encontrada("Territorio eleitoral")

    base = obter_base_eleitoral_visivel(db, territorio.base_eleitoral_id, current_user)
    assegurar_permissao_escrita_base(db, base, current_user)

    if base.status == STATUS_SUBSTITUIDA:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Base substituida nao aceita resolucao de divergencia.",
        )
    if valor_final < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="valor_final deve ser maior ou igual a zero.",
        )
    texto_justificativa = (justificativa or "").strip()
    if not texto_justificativa:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="justificativa e obrigatoria.",
        )

    valor_anterior = territorio.eleitorado_apto
    resolvido_em = _agora().isoformat()

    # Novo dict: mutacao in-place em JSON nao e detectada pelo SQLAlchemy.
    metadados = dict(territorio.metadados or {})
    historico = list(metadados.get("resolucao_divergencia_historico") or [])
    resolucao = {
        "valor_anterior": valor_anterior,
        "valor_final": valor_final,
        "justificativa": texto_justificativa,
        "usuario_id": current_user.id,
        "resolvido_em": resolvido_em,
    }
    historico.append(resolucao)
    metadados["resolucao_divergencia"] = resolucao
    metadados["resolucao_divergencia_historico"] = historico
    territorio.metadados = metadados

    territorio.eleitorado_apto = valor_final
    territorio.eleitorado_apto_divergente = False
    if territorio.status_validacao == STATUS_EM_CONFERENCIA:
        # Resolver o territorio nao valida a base: isso continua sendo acao humana.
        territorio.status_validacao = STATUS_IMPORTADA
    db.add(territorio)

    _marcar_divergencia_resolvida(db, base.id, territorio, resolucao)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(territorio)
    return territorio


def _marcar_divergencia_resolvida(
    db: Session, base_id: int, territorio: models.TerritorioEleitoral, resolucao: dict
) -> None:
    """Marca a divergencia do lote como resolvida, sem apagar o registro original."""
    importacoes = (
        db.query(models.ImportacaoBaseEleitoral)
        .filter(models.ImportacaoBaseEleitoral.base_eleitoral_id == base_id)
        .order_by(models.ImportacaoBaseEleitoral.id)
        .all()
    )
    chave_territorio = (territorio.metadados or {}).get("chave_importacao")
    for importacao in importacoes:
        itens = importacao.divergencias or []
        alterou = False
        novos = []
        for item in itens:
            registro = dict(item)
            # Casamento estrito pela chave do lote: nome pode repetir entre municipios.
            mesmo_territorio = (
                chave_territorio is not None
                and registro.get("territorio_chave") == chave_territorio
            )
            if mesmo_territorio and not registro.get("resolvida"):
                registro["resolvida"] = True
                registro["resolucao"] = resolucao
                registro["resolvido_por_id"] = resolucao["usuario_id"]
                registro["resolvido_em"] = resolucao["resolvido_em"]
                alterou = True
            novos.append(registro)
        if alterou:
            # Nova lista para o SQLAlchemy detectar a mudanca do JSON.
            importacao.divergencias = novos
            db.add(importacao)


# --- Parametros de projecao eleitoral -----------------------------------------
#
# comparecimento_estimado  = proporcao dos aptos que se espera comparecer;
# percentual_votos_validos = proporcao dos comparecidos que sera voto valido.
#
# Ambos sao decisao metodologica humana. O sistema NUNCA os preenche por
# convencao: sem eles, a projecao de votos simplesmente nao existe.

_ESCALA_PARAMETRO = Decimal("0.0001")

# Sentinela: distingue "campo ausente no PATCH" (manter) de "null" (limpar).
_NAO_INFORMADO = object()


def _normalizar_parametro(valor) -> Optional[Decimal]:
    if valor is None:
        return None
    decimal = Decimal(str(valor))
    if not decimal.is_finite():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Parametro de projecao deve ser um numero finito entre 0 e 1.",
        )
    if decimal < 0 or decimal > 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Parametro de projecao deve estar entre 0 e 1.",
        )
    # Numeric(5,4) e a forma canonica: 0.8 vira 0.8000, nunca 80.
    return decimal.quantize(_ESCALA_PARAMETRO, rounding=ROUND_HALF_UP)


def definir_parametros_projecao(
    db: Session,
    base_id: int,
    current_user: models.Usuario,
    comparecimento_estimado=_NAO_INFORMADO,
    percentual_votos_validos=_NAO_INFORMADO,
) -> models.BaseEleitoral:
    """Configura os parametros de projecao da base.

    Permitido mesmo com a base VALIDADA (ADR-025): sao parametros de projecao,
    nao alteram territorio, eleitorado, hierarquia nem a conferencia da fonte.
    Base SUBSTITUIDA e uma versao morta e nao aceita escrita.

    Nao toca em nenhum outro campo da base nem em nenhum territorio.
    """
    base = obter_base_eleitoral_visivel(db, base_id, current_user)
    assegurar_permissao_escrita_base(db, base, current_user)

    if base.status == STATUS_SUBSTITUIDA:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Base substituida nao aceita alteracao de parametros.",
        )

    if comparecimento_estimado is not _NAO_INFORMADO:
        base.comparecimento_estimado = _normalizar_parametro(comparecimento_estimado)
    if percentual_votos_validos is not _NAO_INFORMADO:
        base.percentual_votos_validos = _normalizar_parametro(percentual_votos_validos)

    db.add(base)
    db.commit()
    db.refresh(base)
    return base
