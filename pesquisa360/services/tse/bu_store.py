"""Persistencia do Boletim de Urna (ADR-090).

Mesmas regras do restante do dominio TSE: dados globais (sem tenant),
boletins append-only e gravacao idempotente. O BU e a fonte oficial apenas do
nivel SECAO; nada aqui toca as tabelas do EA20.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from pesquisa360.db.models_tse import (
    TseBoletimUrna, TseBuCargo, TseBuControle, TseBuVoto, TseCandidato, TseCargo, TseEleicao,
    TsePartido, TseSecao, TseSnapshot,
)

from .bu_decoder import LEGENDA, NOMINAL, DecodedBU
from .repository import _iso

PROCESSADO, AGUARDANDO, ERRO = "PROCESSADO", "AGUARDANDO", "ERRO"
MAX_TENTATIVAS_PADRAO = 3
# Secao com arquivo no EA16 mas ainda sem BU 'Totalizado' no EA18 e reconsultada
# depois deste intervalo, mesmo que o carimbo do EA16 nao mude.
REVERIFICAR_AGUARDANDO_SEGUNDOS = 600


class BuIncompativel(ValueError):
    """BU valido, mas que nao pertence a secao/pleito/origem em que foi pedido."""


class BuStore:
    def __init__(self, session: Session):
        self.session = session
        self._eleicoes: dict[tuple, TseEleicao | None] = {}
        self._cargos: dict[str, TseCargo | None] = {}
        self._candidatos: dict[tuple, dict[str, int]] = {}
        self._partidos: dict[int, dict[str, int]] = {}

    # -------------------------------------------------------------- consultas
    def controle(self, secao_id: int) -> TseBuControle | None:
        return self.session.scalars(
            select(TseBuControle).where(TseBuControle.secao_id == secao_id)).first()

    def boletim(self, secao_id: int, sha256: str) -> TseBoletimUrna | None:
        return self.session.scalars(select(TseBoletimUrna).where(
            TseBoletimUrna.secao_id == secao_id, TseBoletimUrna.sha256 == sha256)).first()

    def _filtro_pendente(self, max_tentativas: int,
                         reverificar_segundos: int = REVERIFICAR_AGUARDANDO_SEGUNDOS):
        limite = datetime.now(timezone.utc) - timedelta(seconds=reverificar_segundos)
        return or_(
            TseBuControle.id.is_(None),
            TseBuControle.auxiliar_em.is_(None),
            # O EA16 trocou o carimbo do arquivo auxiliar: ha algo novo no EA18.
            TseBuControle.auxiliar_em != TseSecao.auxiliar_em,
            and_(TseBuControle.status == ERRO, TseBuControle.tentativas < max_tentativas),
            and_(TseBuControle.status == AGUARDANDO, TseBuControle.verificado_em <= limite),
        )

    def _secoes_com_arquivo(self, origem: str, pleito: str, uf: str,
                            municipios: tuple[str, ...] = ()):
        # So secao principal tem urna; so secao com `auxiliar_em` tem EA18.
        filtros = [TseSecao.origem == origem, TseSecao.pleito == str(pleito), TseSecao.uf == uf,
                   TseSecao.eh_principal.is_(True), TseSecao.auxiliar_em.is_not(None)]
        if municipios:
            filtros.append(TseSecao.municipio_codigo.in_(municipios))
        return filtros

    def pendentes(self, origem: str, pleito: str, uf: str, limite: int,
                  municipios: tuple[str, ...] = (),
                  max_tentativas: int = MAX_TENTATIVAS_PADRAO,
                  reverificar_segundos: int = REVERIFICAR_AGUARDANDO_SEGUNDOS) -> list[TseSecao]:
        """Secoes cujo BU ainda nao foi verificado para o carimbo atual do EA16."""
        return list(self.session.scalars(
            select(TseSecao)
            .outerjoin(TseBuControle, TseBuControle.secao_id == TseSecao.id)
            .where(*self._secoes_com_arquivo(origem, pleito, uf, municipios),
                   self._filtro_pendente(max_tentativas, reverificar_segundos))
            .order_by(TseSecao.municipio_codigo, TseSecao.zona, TseSecao.secao)
            .limit(limite)))

    def situacao(self, origem: str, pleito: str, uf: str,
                 max_tentativas: int = MAX_TENTATIVAS_PADRAO,
                 municipios: tuple[str, ...] = (),
                 reverificar_segundos: int = REVERIFICAR_AGUARDANDO_SEGUNDOS) -> dict[str, int]:
        """Esperado x disponivel x ingerido x pendente x erro (para log e operacao)."""
        base = [TseSecao.origem == origem, TseSecao.pleito == str(pleito), TseSecao.uf == uf,
                TseSecao.eh_principal.is_(True)]
        if municipios:
            base.append(TseSecao.municipio_codigo.in_(municipios))
        esperados = self.session.scalar(select(func.count(TseSecao.id)).where(*base)) or 0
        disponiveis = self.session.scalar(select(func.count(TseSecao.id)).where(
            *base, TseSecao.auxiliar_em.is_not(None))) or 0
        por_status = dict(self.session.execute(
            select(TseBuControle.status, func.count(TseBuControle.id))
            .join(TseSecao, TseSecao.id == TseBuControle.secao_id).where(*base)
            .group_by(TseBuControle.status)).all())
        com_bu = self.session.scalar(
            select(func.count(TseBuControle.id))
            .join(TseSecao, TseSecao.id == TseBuControle.secao_id)
            .where(*base, TseBuControle.boletim_id.is_not(None))) or 0
        pendentes = self.session.scalar(
            select(func.count(TseSecao.id))
            .outerjoin(TseBuControle, TseBuControle.secao_id == TseSecao.id)
            .where(*self._secoes_com_arquivo(origem, pleito, uf, municipios),
                   self._filtro_pendente(max_tentativas, reverificar_segundos))) or 0
        return {"esperados": esperados, "disponiveis": disponiveis, "ingeridos": com_bu,
                "pendentes": pendentes, "aguardando": por_status.get(AGUARDANDO, 0),
                "erro": por_status.get(ERRO, 0)}

    # ---------------------------------------------------------------- estado
    def marcar(self, secao: TseSecao, status: str, *, boletim: TseBoletimUrna | None = None,
               erro: str | None = None) -> TseBuControle:
        """Registra o resultado da verificacao da secao para o carimbo atual do EA16.

        `boletim` so e informado quando ha um BU NOVO: o ponteiro do corrente
        nunca volta para um boletim antigo.
        """
        row = self.controle(secao.id)
        if row is None:
            row = TseBuControle(secao_id=secao.id, status=status, tentativas=0)
            self.session.add(row)
        mesmo_carimbo = _iso(row.auxiliar_em) == _iso(secao.auxiliar_em)
        if status == ERRO:
            row.tentativas = (row.tentativas or 0) + 1 if mesmo_carimbo else 1
        else:
            row.tentativas = 0
        row.status = status
        row.erro = (erro or None) and erro[:300]
        row.auxiliar_em = secao.auxiliar_em
        row.verificado_em = datetime.now(timezone.utc)
        if boletim is not None:
            row.boletim_id = boletim.id
        self.session.flush()
        return row

    # ---------------------------------------------------------------- gravar
    def record_bu(self, secao: TseSecao, snapshot: TseSnapshot, bu: DecodedBU, *, sha256: str,
                  tamanho_bytes: int, hash_ea18: str, situacao_ea18: str | None = None,
                  recebido_em: datetime | None = None) -> tuple[TseBoletimUrna, bool]:
        """Grava um BU decodificado; devolve (boletim, criado).

        Idempotente por (secao, sha256 do arquivo): o mesmo arquivo recebido de
        novo devolve o boletim ja gravado e nao cria nada.
        """
        if not secao.eh_principal:
            raise BuIncompativel(
                f"Seção {secao.secao} é agregada: o BU pertence à principal {secao.secao_principal}.")
        if (bu.municipio, bu.zona, bu.secao) != (secao.municipio_codigo, secao.zona, secao.secao):
            raise BuIncompativel(
                f"BU da seção {bu.municipio}/{bu.zona}/{bu.secao} recebido para "
                f"{secao.municipio_codigo}/{secao.zona}/{secao.secao}.")
        if bu.origem != secao.origem:
            raise BuIncompativel(f"BU de fase {bu.origem} em seção de origem {secao.origem}.")
        if bu.pleito is not None and bu.pleito != secao.pleito:
            raise BuIncompativel(f"BU do pleito {bu.pleito} em seção do pleito {secao.pleito}.")

        existente = self.boletim(secao.id, sha256)
        if existente is not None:
            return existente, False

        boletim = TseBoletimUrna(
            origem=secao.origem, pleito=secao.pleito, secao_id=secao.id, snapshot_id=snapshot.id,
            sha256=sha256, tamanho_bytes=tamanho_bytes, hash_ea18=hash_ea18,
            situacao_ea18=situacao_ea18, recebido_em=recebido_em, tipo_bu=bu.tipo,
            local_votacao=bu.local, comparecimento=bu.comparecimento,
            gerado_local=bu.gerado_em, emitido_local=bu.emitido_em,
            abertura_local=bu.abertura_em, encerramento_local=bu.encerramento_em,
            tipo_urna=bu.urna.tipo_urna, tipo_arquivo=bu.urna.tipo_arquivo,
            versao_votacao=bu.urna.versao_votacao[:120],
            numero_interno_urna=bu.urna.numero_interno, codigo_carga=bu.urna.codigo_carga[:40],
        )
        self.session.add(boletim)
        self.session.flush()

        votos = []
        for eleicao_bu in bu.eleicoes:
            eleicao = self._eleicao(secao.origem, secao.pleito, eleicao_bu.codigo)
            if eleicao is None:
                continue        # eleicao fora do catalogo do pleito (EA11): nao e gravada
            partidos = self._partidos_da(eleicao.id)
            for cargo_bu in eleicao_bu.cargos:
                cargo = self._cargo(cargo_bu.codigo) if cargo_bu.constitucional else None
                if cargo is None:
                    continue    # consulta popular / cargo desconhecido: fora do escopo
                linha = TseBuCargo(
                    boletim_id=boletim.id, eleicao_id=eleicao.id, cargo_id=cargo.id,
                    tipo_cargo=cargo_bu.tipo, eleitores_aptos=eleicao_bu.aptos,
                    comparecimento=cargo_bu.comparecimento, votos_nominais=cargo_bu.nominais,
                    votos_legenda=cargo_bu.legenda, votos_brancos=cargo_bu.brancos,
                    votos_nulos=cargo_bu.nulos)
                self.session.add(linha)
                self.session.flush()
                candidatos = self._candidatos_de(eleicao.id, cargo.id, secao.uf)
                for voto in cargo_bu.votos:
                    if voto.tipo not in (NOMINAL, LEGENDA):
                        continue
                    votos.append({
                        "bu_cargo_id": linha.id, "tipo": voto.tipo, "numero": voto.numero,
                        "partido_numero": voto.partido, "votos": voto.votos,
                        "candidato_id": candidatos.get(voto.numero) if voto.tipo == NOMINAL else None,
                        "partido_id": partidos.get(voto.partido),
                    })
        if votos:
            self.session.execute(TseBuVoto.__table__.insert(), votos)
        self.session.flush()
        return boletim, True

    # ---------------------------------------------------------------- catalogo
    def _eleicao(self, origem: str, pleito: str, codigo: str) -> TseEleicao | None:
        chave = (origem, pleito, codigo)
        if chave not in self._eleicoes:
            self._eleicoes[chave] = self.session.scalars(select(TseEleicao).where(
                TseEleicao.origem == origem, TseEleicao.pleito == pleito,
                TseEleicao.codigo_eleicao == codigo)).first()
        return self._eleicoes[chave]

    def _cargo(self, codigo: str) -> TseCargo | None:
        if codigo not in self._cargos:
            self._cargos[codigo] = self.session.scalars(
                select(TseCargo).where(TseCargo.codigo == codigo)).first()
        return self._cargos[codigo]

    def _partidos_da(self, eleicao_id: int) -> dict[str, int]:
        if eleicao_id not in self._partidos:
            self._partidos[eleicao_id] = {numero: pid for pid, numero in self.session.execute(
                select(TsePartido.id, TsePartido.numero)
                .where(TsePartido.eleicao_id == eleicao_id))}
        return self._partidos[eleicao_id]

    def _candidatos_de(self, eleicao_id: int, cargo_id: int, uf: str) -> dict[str, int]:
        """numero -> candidato, somente quando o numero identifica UM candidato."""
        chave = (eleicao_id, cargo_id, uf)
        if chave not in self._candidatos:
            por_numero: dict[str, list[int]] = {}
            for cid, numero in self.session.execute(
                    select(TseCandidato.id, TseCandidato.numero).where(
                        TseCandidato.eleicao_id == eleicao_id, TseCandidato.cargo_id == cargo_id,
                        TseCandidato.uf == uf)):
                por_numero.setdefault(numero, []).append(cid)
            self._candidatos[chave] = {n: ids[0] for n, ids in por_numero.items() if len(ids) == 1}
        return self._candidatos[chave]

    def religar_votos(self, origem: str, pleito: str, uf: str) -> int:
        """Liga ao cadastro os votos gravados antes de o candidato/partido existir.

        O cadastro vem do EA20; um BU pode chegar antes dele. So preenche
        vinculos nulos -- nunca altera votos.
        """
        pendentes = self.session.execute(
            select(TseBuVoto, TseBuCargo.eleicao_id, TseBuCargo.cargo_id)
            .join(TseBuCargo, TseBuCargo.id == TseBuVoto.bu_cargo_id)
            .join(TseBoletimUrna, TseBoletimUrna.id == TseBuCargo.boletim_id)
            .join(TseSecao, TseSecao.id == TseBoletimUrna.secao_id)
            .where(TseBoletimUrna.origem == origem, TseBoletimUrna.pleito == str(pleito),
                   TseSecao.uf == uf,
                   or_(TseBuVoto.partido_id.is_(None),
                       and_(TseBuVoto.tipo == NOMINAL, TseBuVoto.candidato_id.is_(None))))
            .limit(5000)).all()
        self._candidatos.clear()
        self._partidos.clear()
        ligados = 0
        for voto, eleicao_id, cargo_id in pendentes:
            antes = (voto.candidato_id, voto.partido_id)
            if voto.partido_id is None:
                voto.partido_id = self._partidos_da(eleicao_id).get(voto.partido_numero)
            if voto.tipo == NOMINAL and voto.candidato_id is None:
                voto.candidato_id = self._candidatos_de(eleicao_id, cargo_id, uf).get(voto.numero)
            ligados += antes != (voto.candidato_id, voto.partido_id)
        self.session.flush()
        return ligados

