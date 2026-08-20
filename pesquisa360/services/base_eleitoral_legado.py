"""Absorcao do legado eleitoral (bairros / locais_votacao) como Base Eleitoral.

O legado NAO e alterado nem removido por este modulo: ele apenas le as tabelas
existentes e produz registros normalizados para o motor de importacao.

A base gerada nasce sempre privada do tenant e em EM_CONFERENCIA, porque os
dados legados foram carregados sem versao, sem fonte e com deducoes implicitas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from pesquisa360.db import models
from pesquisa360.services.base_eleitoral_import import (
    RegistroTerritorioImportacao,
    importar_registros,
    normalizar_nome_territorio,
)

FONTE_MIGRACAO_LEGADO = "MIGRACAO_LEGADO"

DIVERGENCIA_MUNICIPIO_NAO_IDENTIFICADO = "MUNICIPIO_NAO_IDENTIFICADO"
DIVERGENCIA_MUNICIPIO_AMBIGUO = "MUNICIPIO_AMBIGUO"
DIVERGENCIA_SECAO_SEM_NUMERO = "SECAO_SEM_NUMERO"
DIVERGENCIA_SECOES_INVALIDAS = "SECOES_INVALIDAS"
DIVERGENCIA_LOCAL_SEM_MUNICIPIO = "LOCAL_SEM_MUNICIPIO"

# O importador legado grava {"num": ...}; o comentario do model menciona
# {"secao": ...}. Ambas as chaves sao reconhecidas explicitamente.
CHAVES_NUMERO_SECAO = ("num", "secao")


@dataclass
class PreviewAbsorcaoLegado:
    """Resultado somente-leitura: nada e persistido."""

    contagem: dict = field(default_factory=dict)
    divergencias: list = field(default_factory=list)
    registros: list = field(default_factory=list)

    @property
    def total_registros(self) -> int:
        return len(self.registros)


def _chave(*partes) -> str:
    return "|".join(str(parte) for parte in partes)


def _numero_secao(item) -> Optional[int]:
    if not isinstance(item, dict):
        return None
    for chave in CHAVES_NUMERO_SECAO:
        if chave in item and item[chave] is not None:
            try:
                numero = int(item[chave])
            except (TypeError, ValueError):
                return None
            return numero if numero > 0 else None
    return None


def _mapear_bairro_para_municipio(db: Session, company_id: int) -> dict[str, set]:
    """Descobre o municipio de cada bairro apenas por evidencia real dos locais.

    Nenhum municipio e inventado: o resultado pode ser vazio ou ambiguo, e isso
    vira divergencia em vez de escolha arbitraria.
    """
    mapeamento: dict[str, set] = {}
    linhas = (
        db.query(models.LocalVotacao.bairro, models.LocalVotacao.municipio)
        .filter(models.LocalVotacao.company_id == company_id)
        .all()
    )
    for bairro, municipio in linhas:
        chave_bairro = normalizar_nome_territorio(bairro)
        chave_municipio = normalizar_nome_territorio(municipio)
        if not chave_bairro or not chave_municipio:
            continue
        mapeamento.setdefault(chave_bairro, set()).add(chave_municipio)
    return mapeamento


def preview_absorcao_legado(
    db: Session,
    *,
    company_id: int,
    uf: str,
    nome_estado: Optional[str] = None,
) -> PreviewAbsorcaoLegado:
    """Monta os registros normalizados a partir do legado, sem gravar nada."""
    uf_normalizada = (uf or "").strip().upper()
    if len(uf_normalizada) != 2:
        raise ValueError("uf deve conter exatamente duas letras")

    divergencias: list[dict] = []
    registros: list[RegistroTerritorioImportacao] = []

    chave_estado = _chave("ESTADO", uf_normalizada)
    registros.append(
        RegistroTerritorioImportacao(
            tipo="ESTADO",
            nome=nome_estado or uf_normalizada,
            chave=chave_estado,
            codigo=uf_normalizada,
            metadados={"origem": FONTE_MIGRACAO_LEGADO},
        )
    )

    locais = (
        db.query(models.LocalVotacao)
        .filter(models.LocalVotacao.company_id == company_id)
        .order_by(models.LocalVotacao.id)
        .all()
    )
    bairros = (
        db.query(models.Bairro)
        .filter(models.Bairro.company_id == company_id)
        .order_by(models.Bairro.id)
        .all()
    )

    # --- Municipios: somente os observados em locais_votacao --------------------
    municipios: dict[str, str] = {}
    for local in locais:
        chave_municipio = normalizar_nome_territorio(local.municipio)
        if not chave_municipio or chave_municipio in municipios:
            continue
        chave = _chave("MUNICIPIO", chave_municipio)
        municipios[chave_municipio] = chave
        registros.append(
            RegistroTerritorioImportacao(
                tipo="MUNICIPIO",
                nome=(local.municipio or "").strip(),
                chave=chave,
                parent_chave=chave_estado,
                nome_normalizado=chave_municipio,
                metadados={"origem": "locais_votacao", "municipio_original": local.municipio},
            )
        )

    # --- Bairros: municipio so por evidencia dos locais -------------------------
    mapa_bairro_municipio = _mapear_bairro_para_municipio(db, company_id)
    chave_por_bairro: dict[str, str] = {}
    for bairro in bairros:
        chave_bairro = normalizar_nome_territorio(bairro.nome)
        if not chave_bairro:
            continue
        observados = mapa_bairro_municipio.get(chave_bairro, set())
        if len(observados) == 1:
            chave_municipio = next(iter(observados))
            parent = municipios.get(chave_municipio)
        elif not observados:
            divergencias.append(
                {
                    "tipo_divergencia": DIVERGENCIA_MUNICIPIO_NAO_IDENTIFICADO,
                    "territorio": bairro.nome,
                    "origem": "bairros",
                    "id_origem": bairro.id,
                    "resolvida": False,
                }
            )
            continue
        else:
            divergencias.append(
                {
                    "tipo_divergencia": DIVERGENCIA_MUNICIPIO_AMBIGUO,
                    "territorio": bairro.nome,
                    "origem": "bairros",
                    "id_origem": bairro.id,
                    "municipios_observados": sorted(observados),
                    "resolvida": False,
                }
            )
            continue

        if parent is None:
            divergencias.append(
                {
                    "tipo_divergencia": DIVERGENCIA_MUNICIPIO_NAO_IDENTIFICADO,
                    "territorio": bairro.nome,
                    "origem": "bairros",
                    "id_origem": bairro.id,
                    "resolvida": False,
                }
            )
            continue

        chave = _chave("BAIRRO", chave_bairro, chave_municipio)
        chave_por_bairro[chave_bairro] = chave
        registros.append(
            RegistroTerritorioImportacao(
                tipo="BAIRRO",
                nome=bairro.nome,
                chave=chave,
                parent_chave=parent,
                municipio_chave=parent,
                nome_normalizado=chave_bairro,
                eleitorado_apto=bairro.eleitores,
                metadados={
                    "origem": "bairros",
                    "id_origem": bairro.id,
                    "possui_geometria_legada": bairro.geometria is not None,
                },
            )
        )

    # --- Locais de votacao e secoes --------------------------------------------
    for local in locais:
        chave_municipio = normalizar_nome_territorio(local.municipio)
        parent_municipio = municipios.get(chave_municipio)
        if parent_municipio is None:
            divergencias.append(
                {
                    "tipo_divergencia": DIVERGENCIA_LOCAL_SEM_MUNICIPIO,
                    "territorio": local.nome,
                    "origem": "locais_votacao",
                    "id_origem": local.id,
                    "resolvida": False,
                }
            )
            continue

        chave_bairro = normalizar_nome_territorio(local.bairro)
        # A arvore aceita profundidade variavel: sem bairro resolvido, o local
        # fica direto sob o municipio em vez de inventar um bairro.
        parent = chave_por_bairro.get(chave_bairro, parent_municipio)

        chave_local = _chave("LOCAL_VOTACAO", local.id)
        registros.append(
            RegistroTerritorioImportacao(
                tipo="LOCAL_VOTACAO",
                nome=local.nome,
                chave=chave_local,
                parent_chave=parent,
                municipio_chave=parent_municipio,
                zona_eleitoral=local.zona,
                metadados={
                    "origem": "locais_votacao",
                    "id_origem": local.id,
                    "endereco": local.endereco,
                    "municipio_original": local.municipio,
                    "bairro_original": local.bairro,
                    "secoes_original": local.secoes,
                    "possui_geometria_legada": local.localizacao is not None,
                },
            )
        )

        secoes = local.secoes
        if secoes is None:
            continue
        if not isinstance(secoes, list):
            divergencias.append(
                {
                    "tipo_divergencia": DIVERGENCIA_SECOES_INVALIDAS,
                    "territorio": local.nome,
                    "origem": "locais_votacao",
                    "id_origem": local.id,
                    "resolvida": False,
                }
            )
            continue

        for posicao, item in enumerate(secoes):
            numero = _numero_secao(item)
            if numero is None:
                divergencias.append(
                    {
                        "tipo_divergencia": DIVERGENCIA_SECAO_SEM_NUMERO,
                        "territorio": local.nome,
                        "origem": "locais_votacao",
                        "id_origem": local.id,
                        "posicao": posicao,
                        "resolvida": False,
                    }
                )
                continue

            metadados_secao = {
                "origem": "locais_votacao",
                "id_origem": local.id,
                "secao_original": item,
            }
            if isinstance(item, dict) and item.get("votos") is not None:
                # "votos" do legado NAO e eleitorado apto: sem prova semantica,
                # o valor fica registrado, mas eleitorado_apto permanece NULL.
                metadados_secao["votos_legado"] = item["votos"]

            registros.append(
                RegistroTerritorioImportacao(
                    tipo="SECAO",
                    nome="Secao {}".format(numero),
                    chave=_chave("SECAO", local.id, numero),
                    parent_chave=chave_local,
                    municipio_chave=parent_municipio,
                    zona_eleitoral=local.zona,
                    numero_secao=numero,
                    eleitorado_apto=None,
                    metadados=metadados_secao,
                )
            )

    contagem = {tipo: 0 for tipo in models.TIPOS_TERRITORIO_ELEITORAL}
    for registro in registros:
        contagem[registro.tipo] = contagem.get(registro.tipo, 0) + 1

    return PreviewAbsorcaoLegado(
        contagem=contagem, divergencias=divergencias, registros=registros
    )


def absorver_legado(
    db: Session,
    *,
    company_id: int,
    uf: str,
    nome: str,
    versao: str,
    data_referencia: date,
    criado_por_id: int,
    ano: Optional[int] = None,
    nome_estado: Optional[str] = None,
) -> models.ImportacaoBaseEleitoral:
    """Cria uma base privada MIGRACAO_LEGADO e importa o preview.

    `company_id` vem do contexto interno (tenant do usuario), nunca de payload.
    A base nasce EM_CONFERENCIA e jamais oficial ou VALIDADA.
    """
    if company_id is None:
        raise ValueError("absorcao do legado exige um tenant proprietario")

    preview = preview_absorcao_legado(
        db, company_id=company_id, uf=uf, nome_estado=nome_estado
    )

    base = models.BaseEleitoral(
        nome=nome,
        ano=ano if ano is not None else data_referencia.year,
        uf=uf.strip().upper(),
        fonte=FONTE_MIGRACAO_LEGADO,
        fonte_referencia="bairros + locais_votacao",
        versao=versao,
        data_referencia=data_referencia,
        status="EM_CONFERENCIA",
        company_id=company_id,
        criado_por_id=criado_por_id,
    )
    db.add(base)
    try:
        db.flush()
    except Exception:
        db.rollback()
        raise

    importacao = importar_registros(
        db,
        base_eleitoral=base,
        registros=preview.registros,
        arquivo_origem=FONTE_MIGRACAO_LEGADO,
        executado_por_id=criado_por_id,
        hash_arquivo=None,
    )

    if preview.divergencias:
        # Divergencias estruturais do legado entram no mesmo lote de auditoria.
        combinadas = list(importacao.divergencias or []) + preview.divergencias
        importacao.divergencias = combinadas
        importacao.total_divergencias = len(combinadas)
        db.add(importacao)

    # A absorcao nunca promove a base: o legado sempre exige conferencia humana.
    base.status = "EM_CONFERENCIA"
    db.add(base)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(importacao)
    return importacao
