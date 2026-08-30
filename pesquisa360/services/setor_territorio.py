"""Composicao eleitoral do Setor: quais unidades da Base Eleitoral o compoem.

O Setor e divisao operacional/analitica da Pesquisa; o TerritorioEleitoral
pertence a Base Eleitoral. O sistema NAO infere um do outro -- a composicao e
declarada pelo usuario. Nao ha geometria, nome, proximidade nem rateio aqui.

Tenant deriva sempre de Setor -> Pesquisa -> Projeto -> company_id; o cliente
nunca envia company_id, e recurso de outro tenant responde 404.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from pesquisa360.crud import FINALIDADES_ANALITICAS
from pesquisa360.db import models
from pesquisa360.services import acessos
from pesquisa360.services import base_eleitoral as base_service

# Unico nivel aceito hoje. A Base atual so tem eleitorado em BAIRRO: SECAO e
# LOCAL_VOTACAO nao existem, e MUNICIPIO/ESTADO sao agregacoes dos bairros --
# aceita-los somaria o mesmo eleitor duas vezes.
TIPO_TERRITORIO_ACEITO = "BAIRRO"

# Estados do universo eleitoral. Os valores espelham schemas.StatusUniversoEleitoralSetor;
# o servico nao importa schemas para nao inverter a dependencia do dominio.
STATUS_UNIVERSO_DISPONIVEL = "DISPONIVEL"
STATUS_UNIVERSO_SEM_COMPOSICAO = "SEM_COMPOSICAO_ELEITORAL"
STATUS_UNIVERSO_BASE_DESATUALIZADA = "COMPOSICAO_BASE_DESATUALIZADA"
STATUS_UNIVERSO_BASE_NAO_CONFIGURADA = "BASE_ELEITORAL_NAO_CONFIGURADA"
STATUS_UNIVERSO_BASE_NAO_VALIDADA = "BASE_ELEITORAL_NAO_VALIDADA"
STATUS_UNIVERSO_ELEITORADO_INDISPONIVEL = "ELEITORADO_TERRITORIO_INDISPONIVEL"


def _nao_encontrado(entidade: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"{entidade} nao encontrado."
    )


def eh_analitico(finalidade: Optional[str]) -> bool:
    """RELATORIO e AMBOS formam a malha analitica.

    Espelha `crud.FINALIDADES_ANALITICAS`, o mesmo conjunto que
    `_validar_setores_analiticos` usa para decidir quais setores entram em
    Mapas Estrategicos, Cruzamentos e na analise de liderancas. Setor OPERACAO
    nunca alcanca esses indicadores.
    """
    return finalidade in FINALIDADES_ANALITICAS


def obter_setor(
    db: Session,
    projeto_id: int,
    pesquisa_id: int,
    setor_id: int,
    current_user: models.Usuario,
) -> models.Setor:
    """Setor pelo caminho completo, com tenant derivado do Projeto."""
    setor = (
        db.query(models.Setor)
        .join(models.Pesquisa, models.Pesquisa.id == models.Setor.pesquisa_id)
        .join(models.Projeto, models.Projeto.id == models.Pesquisa.projeto_id)
        .filter(
            models.Setor.id == setor_id,
            models.Setor.pesquisa_id == pesquisa_id,
            models.Pesquisa.projeto_id == projeto_id,
            acessos.filtro_projeto_acessivel(current_user),
        )
        .first()
    )
    if setor is None:
        # 404 tambem para setor de outro tenant: nao revela que existe.
        raise _nao_encontrado("Setor")
    return setor


def listar_territorios(db: Session, setor_id: int) -> list[models.TerritorioEleitoral]:
    return (
        db.query(models.TerritorioEleitoral)
        .join(
            models.SetorTerritorioEleitoral,
            models.SetorTerritorioEleitoral.territorio_eleitoral_id
            == models.TerritorioEleitoral.id,
        )
        .filter(models.SetorTerritorioEleitoral.setor_id == setor_id)
        .order_by(models.TerritorioEleitoral.nome, models.TerritorioEleitoral.id)
        .all()
    )


def _exigir_base_principal(db: Session, projeto_id: int, current_user: models.Usuario):
    base = base_service.obter_base_principal_projeto_opcional(db, projeto_id, current_user)
    if base is None:
        # Nunca escolher "a primeira" ou "a mais recente": sem base declarada
        # nao existe universo eleitoral para compor.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="O projeto nao possui Base Eleitoral principal vinculada.",
        )
    return base


def _validar_territorios(
    db: Session, base_id: int, territorio_ids: Sequence[int]
) -> list[models.TerritorioEleitoral]:
    """Territorios existentes, da base principal e do nivel aceito.

    Um unico id invalido derruba o lote: base errada, tenant errado, nivel
    errado e inexistente sao indistinguiveis de proposito.
    """
    encontrados = (
        db.query(models.TerritorioEleitoral)
        .filter(
            models.TerritorioEleitoral.id.in_(territorio_ids),
            models.TerritorioEleitoral.base_eleitoral_id == base_id,
            models.TerritorioEleitoral.tipo == TIPO_TERRITORIO_ACEITO,
        )
        .all()
    )
    if len(encontrados) != len(set(territorio_ids)):
        raise _nao_encontrado("Territorio eleitoral")
    return encontrados


def _bloquear_territorios(db: Session, territorio_ids: Sequence[int]) -> None:
    """Serializa transacoes que disputam os mesmos bairros.

    Duas requisicoes simultaneas atribuindo o mesmo bairro a setores analiticos
    distintos precisam passar por aqui antes de conferir conflito. `ORDER BY id`
    fixa a ordem de aquisicao e evita deadlock quando os conjuntos se cruzam.

    Em SQLite (suite de testes) o `FOR UPDATE` nao e emitido; la a serializacao
    vem do proprio modelo de escrita do banco.
    """
    if not territorio_ids:
        return
    (
        db.query(models.TerritorioEleitoral.id)
        .filter(models.TerritorioEleitoral.id.in_(territorio_ids))
        .order_by(models.TerritorioEleitoral.id)
        .with_for_update()
        .all()
    )


def _conflitos_analiticos(
    db: Session,
    pesquisa_id: int,
    setor_id: int,
    territorio_ids: Sequence[int],
) -> list[tuple[models.TerritorioEleitoral, models.Setor]]:
    """Bairros ja usados por OUTRO setor analitico da mesma pesquisa.

    O bairro e indivisivel na Base atual: se entrasse em dois universos
    analiticos, o mesmo eleitorado seria contado duas vezes.
    """
    if not territorio_ids:
        return []
    return (
        db.query(models.TerritorioEleitoral, models.Setor)
        .join(
            models.SetorTerritorioEleitoral,
            models.SetorTerritorioEleitoral.territorio_eleitoral_id
            == models.TerritorioEleitoral.id,
        )
        .join(models.Setor, models.Setor.id == models.SetorTerritorioEleitoral.setor_id)
        .filter(
            models.Setor.pesquisa_id == pesquisa_id,
            models.Setor.id != setor_id,
            models.Setor.finalidade.in_(FINALIDADES_ANALITICAS),
            models.SetorTerritorioEleitoral.territorio_eleitoral_id.in_(territorio_ids),
        )
        .order_by(models.TerritorioEleitoral.nome)
        .all()
    )


def _erro_conflito(conflitos) -> HTTPException:
    detalhes = ", ".join(
        "{} (setor {})".format(territorio.nome, setor.nome) for territorio, setor in conflitos
    )
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "Territorio ja vinculado a outro setor analitico desta pesquisa: "
            + detalhes
        ),
    )


def definir_territorios(
    db: Session,
    projeto_id: int,
    pesquisa_id: int,
    setor_id: int,
    territorio_ids: Sequence[int],
    current_user: models.Usuario,
) -> list[models.TerritorioEleitoral]:
    """Substituicao integral e transacional da composicao eleitoral do Setor.

    Idempotente: enviar o mesmo conjunto duas vezes deixa o mesmo resultado.
    Lista vazia limpa a composicao. Qualquer recusa deixa o estado anterior
    intacto -- nunca meio salvo.
    """
    # Acesso por tenant, como no proprio CRUD de Setor: quem administra o setor
    # administra a composicao dele. Um gate de perfil aqui seria mais restritivo
    # que criar ou excluir o setor.
    setor = obter_setor(db, projeto_id, pesquisa_id, setor_id, current_user)

    unicos = list(dict.fromkeys(territorio_ids))

    try:
        if unicos:
            base = _exigir_base_principal(db, projeto_id, current_user)
            _validar_territorios(db, base.id, unicos)
            # Trava antes de conferir: sem isso duas requisicoes concorrentes
            # leem "sem conflito" e ambas gravam.
            _bloquear_territorios(db, unicos)
            if eh_analitico(setor.finalidade):
                conflitos = _conflitos_analiticos(db, pesquisa_id, setor.id, unicos)
                if conflitos:
                    raise _erro_conflito(conflitos)

        db.query(models.SetorTerritorioEleitoral).filter(
            models.SetorTerritorioEleitoral.setor_id == setor.id
        ).delete(synchronize_session=False)
        for territorio_id in unicos:
            db.add(
                models.SetorTerritorioEleitoral(
                    setor_id=setor.id, territorio_eleitoral_id=territorio_id
                )
            )
        # ADR-035: composicao e a segunda fonte do municipio de referencia.
        # Sem geometrias municipais, e ela quem resolve -- e a referencia
        # acompanha a composicao em vez de ficar contraditoria em silencio.
        from pesquisa360.services import setor_municipio

        db.flush()
        setor_municipio.aplicar(db, setor, projeto_id, current_user)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return listar_territorios(db, setor.id)


def assegurar_finalidade_sem_conflito(
    db: Session, setor: models.Setor, nova_finalidade: Optional[str]
) -> None:
    """Impede criar conflito pela porta lateral da mudanca de finalidade.

    Um setor OPERACAO pode dividir bairros com a malha analitica. Ao virar
    RELATORIO/AMBOS ele passa a disputar o mesmo universo, e a sobreposicao
    que era legitima vira dupla contagem. So a transicao
    nao-analitico -> analitico precisa ser conferida.
    """
    if nova_finalidade is None:
        return
    if eh_analitico(setor.finalidade) or not eh_analitico(nova_finalidade):
        return

    territorio_ids = [
        linha.territorio_eleitoral_id
        for linha in db.query(models.SetorTerritorioEleitoral.territorio_eleitoral_id)
        .filter(models.SetorTerritorioEleitoral.setor_id == setor.id)
        .all()
    ]
    conflitos = _conflitos_analiticos(db, setor.pesquisa_id, setor.id, territorio_ids)
    if conflitos:
        raise _erro_conflito(conflitos)


# --- Universo eleitoral do Setor ---------------------------------------------
# Fonte unica da soma. Endpoint, frontend e analytics consomem daqui; ninguem
# repete a matematica.


def territorios_fora_da_base(
    territorios: Iterable[models.TerritorioEleitoral], base_id: int
) -> list[models.TerritorioEleitoral]:
    """Unidades que NAO pertencem a base informada.

    Helper isolado porque a mesma pergunta vale para a composicao do Setor e
    para os bairros da Lideranca -- os dois guardam `territorio_eleitoral_id`
    sem reler a base depois. Aqui so responde; nao decide nem altera nada.
    """
    return [
        territorio
        for territorio in territorios
        if territorio.base_eleitoral_id != base_id
    ]


def _territorios_da_composicao(
    db: Session, setor_id: int
) -> list[models.TerritorioEleitoral]:
    """Unidades vinculadas, deduplicadas por id.

    O UNIQUE(setor_id, territorio_eleitoral_id) ja impede repeticao, mas a soma
    nao pode depender disso: se um join futuro multiplicar linhas, contar duas
    vezes o mesmo bairro dobraria o universo em silencio. A deduplicacao aqui e
    explicita.
    """
    linhas = (
        db.query(models.TerritorioEleitoral)
        .join(
            models.SetorTerritorioEleitoral,
            models.SetorTerritorioEleitoral.territorio_eleitoral_id
            == models.TerritorioEleitoral.id,
        )
        .filter(models.SetorTerritorioEleitoral.setor_id == setor_id)
        .all()
    )
    unicos: dict[int, models.TerritorioEleitoral] = {}
    for territorio in linhas:
        unicos[territorio.id] = territorio
    return list(unicos.values())


def _universo(
    setor_id: int,
    status: str,
    *,
    base=None,
    quantidade: int = 0,
    eleitorado_apto: Optional[int] = None,
) -> dict:
    indisponivel = status != STATUS_UNIVERSO_DISPONIVEL
    return {
        "setor_id": setor_id,
        "status": status,
        "motivo_indisponibilidade": status if indisponivel else None,
        "base_eleitoral_id": base.id if base is not None else None,
        "base_eleitoral_nome": base.nome if base is not None else None,
        "quantidade_territorios": quantidade,
        "eleitorado_apto": eleitorado_apto,
    }


def obter_universo_eleitoral_setor(
    db: Session,
    projeto_id: int,
    pesquisa_id: int,
    setor_id: int,
    current_user: models.Usuario,
) -> dict:
    """Quantos eleitores aptos compoem este Setor, hoje.

    Soma o `eleitorado_apto` das unidades explicitamente vinculadas, desde que
    todas pertencam a Base principal ATUAL do projeto. Nao ha rateio, geometria,
    projecao nem remapeamento entre bases -- so a soma do que foi declarado.

    Calculado em leitura: o valor depende da composicao atual, do eleitorado
    atual e da base principal atual, e persistir a soma criaria um numero que
    envelhece sem aviso.

    Precedencia dos estados, do contexto para o detalhe:

      1. sem base principal      -> BASE_ELEITORAL_NAO_CONFIGURADA
      2. base nao VALIDADA       -> BASE_ELEITORAL_NAO_VALIDADA
      3. nenhum vinculo          -> SEM_COMPOSICAO_ELEITORAL
      4. algum vinculo de outra base -> COMPOSICAO_BASE_DESATUALIZADA
      5. algum eleitorado ausente    -> ELEITORADO_TERRITORIO_INDISPONIVEL
      6. caso contrario          -> DISPONIVEL

    A base vem primeiro porque e a precondicao de tudo: sem ela nem da para
    julgar se a composicao esta desatualizada.
    """
    setor = obter_setor(db, projeto_id, pesquisa_id, setor_id, current_user)

    base = base_service.obter_base_principal_projeto_opcional(
        db, projeto_id, current_user
    )
    territorios = _territorios_da_composicao(db, setor.id)
    quantidade = len(territorios)

    if base is None:
        # Nunca escolher base implicitamente para nao devolver um numero certo
        # sobre o universo errado.
        return _universo(
            setor.id, STATUS_UNIVERSO_BASE_NAO_CONFIGURADA, quantidade=quantidade
        )

    if base.status != base_service.STATUS_VALIDADA:
        # Mesma exigencia do restante do motor eleitoral: sem validacao humana
        # a base nao alimenta calculo.
        return _universo(
            setor.id,
            STATUS_UNIVERSO_BASE_NAO_VALIDADA,
            base=base,
            quantidade=quantidade,
        )

    if not territorios:
        # Ausencia de configuracao nao e universo de zero eleitores.
        return _universo(
            setor.id, STATUS_UNIVERSO_SEM_COMPOSICAO, base=base, quantidade=0
        )

    if territorios_fora_da_base(territorios, base.id):
        # Basta UMA unidade da base anterior. Somar so as atuais entregaria um
        # universo parcial com cara de completo -- pior que nao responder.
        # Os vinculos antigos ficam no banco: apagar destruiria a auditoria e a
        # chance de o usuario ver o que precisa reconfigurar.
        return _universo(
            setor.id,
            STATUS_UNIVERSO_BASE_DESATUALIZADA,
            base=base,
            quantidade=quantidade,
        )

    if any(territorio.eleitorado_apto is None for territorio in territorios):
        # NULL nao e zero. Somar ignorando entregaria universo menor que o real.
        return _universo(
            setor.id,
            STATUS_UNIVERSO_ELEITORADO_INDISPONIVEL,
            base=base,
            quantidade=quantidade,
        )

    return _universo(
        setor.id,
        STATUS_UNIVERSO_DISPONIVEL,
        base=base,
        quantidade=quantidade,
        eleitorado_apto=sum(territorio.eleitorado_apto for territorio in territorios),
    )
