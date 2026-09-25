"""Cenarios de Base Eleitoral Operacional da Gestao de Liderancas (ADR-075).

A Base Eleitoral oficial e somente leitura aqui. O cenario guarda, por setor
da onda, o eleitorado que a METODOLOGIA considera no universo operacional --
um numero manual, versionavel, que so a Gestao de Liderancas consome.

Ciclo de vida:

  RASCUNHO  -> edita metadados e valores por setor; duplica; ativa; arquiva
  ATIVO     -> alimenta os calculos; valores imutaveis; duplica; arquiva
  ARQUIVADO -> historico, somente leitura; duplica

Alteracao metodologica e sempre DUPLICAR -> editar RASCUNHO -> ATIVAR. So um
cenario fica ATIVO por pesquisa: a aplicacao serializa a ativacao e o indice
parcial `uq_lideranca_cenarios_ativo_por_pesquisa` e a ultima barreira.

Tenant deriva de Pesquisa -> Projeto -> company_id; o cliente nunca envia
company_id. Recurso de outro tenant responde 404 (padrao do projeto).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Sequence

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from pesquisa360.db import models
from pesquisa360.services import lideranca as lideranca_service
from pesquisa360.services import setor_territorio

RASCUNHO = models.STATUS_CENARIO_RASCUNHO
ATIVO = models.STATUS_CENARIO_ATIVO
ARQUIVADO = models.STATUS_CENARIO_ARQUIVADO

MODO_PADRAO = "PADRAO"
MODO_CENARIO_OPERACIONAL = "CENARIO_OPERACIONAL"

PREFIXO_COPIA = "Cópia de "


def _nao_encontrado(entidade: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"{entidade} nao encontrado."
    )


def _conflito(detalhe: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detalhe)


def _invalido(detalhe) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detalhe)


def _agora() -> datetime:
    return datetime.now(timezone.utc)


# --- Acesso ------------------------------------------------------------------


def obter_pesquisa_do_projeto(
    db: Session, projeto_id: int, pesquisa_id: int, current_user: models.Usuario
) -> models.Pesquisa:
    """Pesquisa pelo caminho completo: projeto visivel pela ACL e onda dele."""
    projeto = lideranca_service.obter_projeto(db, projeto_id, current_user)
    pesquisa = (
        db.query(models.Pesquisa)
        .filter(models.Pesquisa.id == pesquisa_id, models.Pesquisa.projeto_id == projeto.id)
        .first()
    )
    if pesquisa is None:
        raise _nao_encontrado("Pesquisa")
    return pesquisa


def _query_cenarios(db: Session, projeto_id: int, current_user: models.Usuario):
    """Cenarios alcancaveis pelo usuario dentro do projeto (cadeia de tenant completa)."""
    from pesquisa360.services import acessos

    return (
        db.query(models.LiderancaCenario)
        .join(models.Pesquisa, models.Pesquisa.id == models.LiderancaCenario.pesquisa_id)
        .join(models.Projeto, models.Projeto.id == models.Pesquisa.projeto_id)
        .filter(
            models.Pesquisa.projeto_id == projeto_id,
            acessos.filtro_projeto_acessivel(current_user),
        )
    )


def obter_cenario(
    db: Session, projeto_id: int, cenario_id: int, current_user: models.Usuario
) -> models.LiderancaCenario:
    cenario = (
        _query_cenarios(db, projeto_id, current_user)
        .options(selectinload(models.LiderancaCenario.setores))
        .filter(models.LiderancaCenario.id == cenario_id)
        .first()
    )
    if cenario is None:
        # 404 tambem para cenario de outro tenant: nao revela que existe.
        raise _nao_encontrado("Cenario")
    return cenario


def listar_cenarios(
    db: Session,
    projeto_id: int,
    current_user: models.Usuario,
    *,
    pesquisa_id: Optional[int] = None,
) -> list[models.LiderancaCenario]:
    lideranca_service.obter_projeto(db, projeto_id, current_user)
    query = _query_cenarios(db, projeto_id, current_user).options(
        selectinload(models.LiderancaCenario.setores)
    )
    if pesquisa_id is not None:
        query = query.filter(models.LiderancaCenario.pesquisa_id == pesquisa_id)
    return query.order_by(
        models.LiderancaCenario.pesquisa_id,
        models.LiderancaCenario.criado_em.desc(),
        models.LiderancaCenario.id.desc(),
    ).all()


def obter_cenario_ativo(db: Session, pesquisa_id: int) -> Optional[models.LiderancaCenario]:
    """Cenario ATIVO da onda, sem ACL: uso interno dos calculos, que ja
    validaram o acesso ao projeto/pesquisa."""
    return (
        db.query(models.LiderancaCenario)
        .options(selectinload(models.LiderancaCenario.setores))
        .filter(
            models.LiderancaCenario.pesquisa_id == pesquisa_id,
            models.LiderancaCenario.status == ATIVO,
        )
        .first()
    )


# --- Derivacoes --------------------------------------------------------------


def total_operacional(cenario: models.LiderancaCenario) -> int:
    return sum(item.eleitorado_operacional for item in cenario.setores)


def total_oficial_referencia(cenario: models.LiderancaCenario) -> Optional[int]:
    """Soma dos snapshots; None se algum setor configurado nao tinha referencia."""
    if not cenario.setores:
        return None
    if any(item.eleitorado_oficial_referencia is None for item in cenario.setores):
        return None
    return sum(item.eleitorado_oficial_referencia for item in cenario.setores)


def peso_operacional(valor: Optional[int], total: int) -> Optional[float]:
    """operacional / soma operacional x 100, duas casas. Total zero -> None."""
    if valor is None or not total:
        return None
    percentual = (Decimal(valor) / Decimal(total)) * Decimal(100)
    return float(percentual.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def resumo(cenario: models.LiderancaCenario) -> dict:
    return {
        "id": cenario.id,
        "pesquisa_id": cenario.pesquisa_id,
        "nome": cenario.nome,
        "metodologia": cenario.metodologia,
        "data_referencia": cenario.data_referencia,
        "status": cenario.status,
        "quantidade_setores_configurados": len(cenario.setores),
        "total_eleitorado_operacional": total_operacional(cenario),
        "total_eleitorado_oficial_referencia": total_oficial_referencia(cenario),
        "criado_em": cenario.criado_em,
        "atualizado_em": cenario.atualizado_em,
        "ativado_em": cenario.ativado_em,
        "arquivado_em": cenario.arquivado_em,
    }


def detalhe(
    db: Session, projeto_id: int, cenario: models.LiderancaCenario, current_user: models.Usuario
) -> dict:
    """Resumo + uma linha por setor da onda (configurado ou nao).

    O oficial ATUAL vem em lote (`obter_universos_eleitorais_setores`): uma
    query para a composicao de todos os setores, nunca uma por linha.
    """
    setores = (
        db.query(models.Setor)
        .filter(models.Setor.pesquisa_id == cenario.pesquisa_id)
        .order_by(models.Setor.nome, models.Setor.id)
        .all()
    )
    universos = setor_territorio.obter_universos_eleitorais_setores(
        db, projeto_id, cenario.pesquisa_id, [setor.id for setor in setores], current_user
    )
    configurados = {item.setor_id: item for item in cenario.setores}
    total = total_operacional(cenario)
    linhas = []
    for setor in setores:
        universo = universos.get(setor.id)
        item = configurados.get(setor.id)
        linhas.append(
            {
                "setor_id": setor.id,
                "setor_nome": setor.nome,
                "configurado": item is not None,
                "status_oficial": (
                    universo["status"]
                    if universo
                    else setor_territorio.STATUS_UNIVERSO_SEM_COMPOSICAO
                ),
                "eleitorado_oficial_atual": universo["eleitorado_apto"] if universo else None,
                "eleitorado_oficial_referencia": (
                    item.eleitorado_oficial_referencia if item else None
                ),
                "eleitorado_operacional": item.eleitorado_operacional if item else None,
                "peso_operacional": peso_operacional(
                    item.eleitorado_operacional if item else None, total
                ),
                "observacao": item.observacao if item else None,
            }
        )
    dados = resumo(cenario)
    dados["setores"] = linhas
    return dados


# --- Escrita -----------------------------------------------------------------


def _exigir_rascunho(cenario: models.LiderancaCenario, acao: str) -> None:
    """ATIVO e ARQUIVADO sao imutaveis: mudanca metodologica e por duplicacao."""
    if cenario.status != RASCUNHO:
        raise _conflito(
            f"Cenario {cenario.status} nao pode ser {acao}. "
            "Duplique o cenario e edite a copia em RASCUNHO."
        )


def criar_cenario(
    db: Session,
    projeto_id: int,
    current_user: models.Usuario,
    *,
    pesquisa_id: int,
    nome: str,
    metodologia: Optional[str] = None,
    data_referencia: Optional[date] = None,
) -> models.LiderancaCenario:
    pesquisa = obter_pesquisa_do_projeto(db, projeto_id, pesquisa_id, current_user)
    lideranca_service.assegurar_permissao_escrita(db, current_user)
    cenario = models.LiderancaCenario(
        pesquisa_id=pesquisa.id,
        nome=nome.strip(),
        metodologia=(metodologia or "").strip() or None,
        data_referencia=data_referencia,
        status=RASCUNHO,
        criado_por_id=current_user.id,
    )
    db.add(cenario)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return obter_cenario(db, projeto_id, cenario.id, current_user)


def atualizar_cenario(
    db: Session,
    projeto_id: int,
    cenario_id: int,
    current_user: models.Usuario,
    *,
    campos: dict,
) -> models.LiderancaCenario:
    """PATCH de metadados. `campos` traz apenas o que o cliente informou."""
    cenario = obter_cenario(db, projeto_id, cenario_id, current_user)
    lideranca_service.assegurar_permissao_escrita(db, current_user)
    _exigir_rascunho(cenario, "editado")
    if "nome" in campos and campos["nome"] is not None:
        cenario.nome = campos["nome"].strip()
    if "metodologia" in campos:
        cenario.metodologia = (campos["metodologia"] or "").strip() or None
    if "data_referencia" in campos:
        cenario.data_referencia = campos["data_referencia"]
    db.add(cenario)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return obter_cenario(db, projeto_id, cenario.id, current_user)


def salvar_setores(
    db: Session,
    projeto_id: int,
    cenario_id: int,
    current_user: models.Usuario,
    *,
    setores: Sequence[dict],
) -> models.LiderancaCenario:
    """Substituicao integral e transacional dos valores por setor.

    So RASCUNHO. Cada setor precisa pertencer a onda do cenario; um id
    invalido derruba o lote inteiro. O snapshot `eleitorado_oficial_referencia`
    e capturado AQUI, da Base Eleitoral atual -- se nao houver referencia
    segura, fica ausente: jamais um valor fabricado.
    """
    cenario = obter_cenario(db, projeto_id, cenario_id, current_user)
    lideranca_service.assegurar_permissao_escrita(db, current_user)
    _exigir_rascunho(cenario, "reconfigurado")

    ids = [item["setor_id"] for item in setores]
    if len(ids) != len(set(ids)):
        raise _invalido("setores nao pode repetir setor_id")
    for item in setores:
        valor = item["eleitorado_operacional"]
        if isinstance(valor, bool) or not isinstance(valor, int) or valor < 0:
            raise _invalido("eleitorado_operacional deve ser inteiro maior ou igual a zero")

    if ids:
        existentes = {
            row[0]
            for row in db.query(models.Setor.id)
            .filter(models.Setor.id.in_(ids), models.Setor.pesquisa_id == cenario.pesquisa_id)
            .all()
        }
        if existentes != set(ids):
            # Setor de outra onda, de outro tenant ou inexistente: 404 para todos.
            raise _nao_encontrado("Setor")

    universos = setor_territorio.obter_universos_eleitorais_setores(
        db, projeto_id, cenario.pesquisa_id, ids, current_user
    )
    try:
        db.query(models.LiderancaCenarioSetor).filter(
            models.LiderancaCenarioSetor.cenario_id == cenario.id
        ).delete(synchronize_session=False)
        for item in setores:
            universo = universos.get(item["setor_id"])
            db.add(
                models.LiderancaCenarioSetor(
                    cenario_id=cenario.id,
                    setor_id=item["setor_id"],
                    eleitorado_oficial_referencia=(
                        universo["eleitorado_apto"] if universo else None
                    ),
                    eleitorado_operacional=item["eleitorado_operacional"],
                    observacao=item.get("observacao") or None,
                )
            )
        cenario.atualizado_em = _agora()
        db.add(cenario)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.expire_all()
    return obter_cenario(db, projeto_id, cenario.id, current_user)


def duplicar_cenario(
    db: Session, projeto_id: int, cenario_id: int, current_user: models.Usuario
) -> models.LiderancaCenario:
    """Novo RASCUNHO com metadados, valores e snapshots copiados. O original
    nao muda; o status nunca e copiado."""
    origem = obter_cenario(db, projeto_id, cenario_id, current_user)
    lideranca_service.assegurar_permissao_escrita(db, current_user)
    copia = models.LiderancaCenario(
        pesquisa_id=origem.pesquisa_id,
        nome=(PREFIXO_COPIA + origem.nome)[:200],
        metodologia=origem.metodologia,
        data_referencia=origem.data_referencia,
        status=RASCUNHO,
        criado_por_id=current_user.id,
    )
    db.add(copia)
    try:
        db.flush()
        for item in origem.setores:
            db.add(
                models.LiderancaCenarioSetor(
                    cenario_id=copia.id,
                    setor_id=item.setor_id,
                    eleitorado_oficial_referencia=item.eleitorado_oficial_referencia,
                    eleitorado_operacional=item.eleitorado_operacional,
                    observacao=item.observacao,
                )
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return obter_cenario(db, projeto_id, copia.id, current_user)


def problemas_para_ativar(db: Session, cenario: models.LiderancaCenario) -> list[str]:
    """Lista (possivelmente vazia) do que impede a ativacao.

    Regra central: toda lideranca ATIVA da onda com setor de referencia precisa
    de valor no cenario. Lideranca sem setor nao exige nada -- nem ganha setor
    inventado; suas metricas territoriais seguem N/A como hoje.
    """
    problemas: list[str] = []
    if not (cenario.nome or "").strip():
        problemas.append("Informe o nome do cenario.")
    if not cenario.setores:
        problemas.append("Configure o eleitorado operacional de ao menos um setor.")
    if any(item.eleitorado_operacional < 0 for item in cenario.setores):
        problemas.append("Ha eleitorado operacional negativo.")
    if cenario.setores and total_operacional(cenario) <= 0:
        problemas.append("A soma do eleitorado operacional precisa ser maior que zero.")
    ids = [item.setor_id for item in cenario.setores]
    if len(ids) != len(set(ids)):
        problemas.append("Ha setor duplicado no cenario.")

    configurados = set(ids)
    dependentes = (
        db.query(models.Setor.id, models.Setor.nome)
        .join(
            models.LiderancaPesquisaConfig,
            models.LiderancaPesquisaConfig.setor_id == models.Setor.id,
        )
        .join(
            models.LiderancaPolitica,
            models.LiderancaPolitica.id == models.LiderancaPesquisaConfig.lideranca_id,
        )
        .filter(
            models.LiderancaPesquisaConfig.pesquisa_id == cenario.pesquisa_id,
            models.LiderancaPolitica.ativo.is_(True),
        )
        .distinct()
        .order_by(models.Setor.nome)
        .all()
    )
    faltantes = [nome for setor_id, nome in dependentes if setor_id not in configurados]
    if faltantes:
        problemas.append(
            "Setores com lideranca vinculada sem eleitorado operacional: "
            + ", ".join(faltantes)
            + "."
        )
    return problemas


def _bloquear_pesquisa(db: Session, pesquisa_id: int) -> None:
    """Serializa ativacoes concorrentes da mesma onda (FOR UPDATE na pesquisa).

    Em SQLite o FOR UPDATE nao e emitido; la o indice parcial unico responde.
    """
    db.query(models.Pesquisa.id).filter(models.Pesquisa.id == pesquisa_id).with_for_update().all()


def ativar_cenario(
    db: Session, projeto_id: int, cenario_id: int, current_user: models.Usuario
) -> models.LiderancaCenario:
    """RASCUNHO -> ATIVO, transacional. O ATIVO anterior vira ARQUIVADO na
    mesma transacao: nunca ha janela com dois ativos nem com nenhum no meio
    de uma troca. Conflito de concorrencia responde 409, nunca meio salvo."""
    cenario = obter_cenario(db, projeto_id, cenario_id, current_user)
    lideranca_service.assegurar_permissao_escrita(db, current_user)
    if cenario.status == ATIVO:
        raise _conflito("Cenario ja esta ATIVO.")
    if cenario.status != RASCUNHO:
        raise _conflito("Somente cenario em RASCUNHO pode ser ativado. Duplique o cenario.")
    problemas = problemas_para_ativar(db, cenario)
    if problemas:
        raise _invalido({"mensagem": "Cenario nao pode ser ativado.", "problemas": problemas})

    agora = _agora()
    try:
        _bloquear_pesquisa(db, cenario.pesquisa_id)
        anteriores = (
            db.query(models.LiderancaCenario)
            .filter(
                models.LiderancaCenario.pesquisa_id == cenario.pesquisa_id,
                models.LiderancaCenario.status == ATIVO,
                models.LiderancaCenario.id != cenario.id,
            )
            .all()
        )
        for anterior in anteriores:
            anterior.status = ARQUIVADO
            anterior.arquivado_em = agora
            db.add(anterior)
        # Flush antes de promover: o indice parcial ve o anterior ja arquivado.
        db.flush()
        cenario.status = ATIVO
        cenario.ativado_em = agora
        cenario.ativado_por_id = current_user.id
        db.add(cenario)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _conflito("Outro cenario foi ativado ao mesmo tempo. Recarregue e tente de novo.")
    except Exception:
        db.rollback()
        raise
    return obter_cenario(db, projeto_id, cenario.id, current_user)


def arquivar_cenario(
    db: Session, projeto_id: int, cenario_id: int, current_user: models.Usuario
) -> models.LiderancaCenario:
    """RASCUNHO ou ATIVO -> ARQUIVADO. Arquivar o ATIVO e acao explicita: a
    Gestao de Liderancas volta ao modo PADRAO, nunca a um estado hibrido."""
    cenario = obter_cenario(db, projeto_id, cenario_id, current_user)
    lideranca_service.assegurar_permissao_escrita(db, current_user)
    if cenario.status == ARQUIVADO:
        raise _conflito("Cenario ja esta ARQUIVADO.")
    cenario.status = ARQUIVADO
    cenario.arquivado_em = _agora()
    db.add(cenario)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return obter_cenario(db, projeto_id, cenario.id, current_user)


# --- Resolvedor unico do eleitorado ------------------------------------------
# Quem calcula nao pergunta "tem cenario?" em cada endpoint: pede a base de
# calculo da onda e consulta o eleitorado do setor por aqui.


@dataclass(frozen=True)
class BaseCalculoLiderancas:
    modo: str
    cenario: Optional[models.LiderancaCenario] = None
    operacional_por_setor: dict[int, int] = field(default_factory=dict)

    @property
    def usa_cenario(self) -> bool:
        return self.modo == MODO_CENARIO_OPERACIONAL

    @property
    def total_operacional(self) -> Optional[int]:
        if not self.usa_cenario:
            return None
        return sum(self.operacional_por_setor.values())

    def eleitorado_operacional(self, setor_id: Optional[int]) -> Optional[int]:
        """Valor do cenario para o setor; None = setor nao configurado.

        Sem cenario ATIVO devolve None tambem -- e a chamada legada quem decide
        a fonte nesse caso. Dentro de cenario ATIVO, None NAO vira oficial: o
        consumidor deve declarar CENARIO_SETOR_NAO_CONFIGURADO.
        """
        if not self.usa_cenario or setor_id is None:
            return None
        return self.operacional_por_setor.get(setor_id)

    def contrato(self) -> dict:
        if not self.usa_cenario or self.cenario is None:
            return {
                "modo": MODO_PADRAO,
                "cenario_id": None,
                "cenario_nome": None,
                "data_referencia": None,
                "total_eleitorado_operacional": None,
            }
        return {
            "modo": MODO_CENARIO_OPERACIONAL,
            "cenario_id": self.cenario.id,
            "cenario_nome": self.cenario.nome,
            "data_referencia": self.cenario.data_referencia,
            "total_eleitorado_operacional": self.total_operacional,
        }


def resolver_base_calculo(db: Session, pesquisa_id: int) -> BaseCalculoLiderancas:
    """SE existe cenario ATIVO na onda -> CENARIO_OPERACIONAL; SENAO -> PADRAO.

    Uma unica consulta (cenario + setores). Chamado uma vez por request de
    analise, nunca por lideranca ou por card.
    """
    cenario = obter_cenario_ativo(db, pesquisa_id)
    if cenario is None:
        return BaseCalculoLiderancas(modo=MODO_PADRAO)
    return BaseCalculoLiderancas(
        modo=MODO_CENARIO_OPERACIONAL,
        cenario=cenario,
        operacional_por_setor={
            item.setor_id: item.eleitorado_operacional for item in cenario.setores
        },
    )
