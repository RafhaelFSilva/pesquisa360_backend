"""Ingestao incremental de Boletins de Urna: EA18 -> BU -> decoder -> banco.

O EA16 (ja ingerido) diz quais secoes principais tem arquivo de urna e quando
ele mudou (`auxiliar_em`). Cada execucao pega um LOTE de secoes ainda nao
verificadas para o carimbo atual, consulta o EA18 de cada uma, baixa o BU do
hash 'Totalizado' se ele ainda nao e conhecido, decodifica com a
especificacao oficial e grava.

- incremental: secao verificada so volta a fila quando o EA16 muda (a que
  ainda aguarda o BU e reconsultada de tempos em tempos);
- sem download repetido: hash do EA18 ja baixado nao e buscado de novo;
- falha isolada: cada secao e uma transacao; um BU invalido nao derruba o
  lote, vira `ERRO` (com nova tentativa limitada) e o lote segue;
- o BU e entrada externa: nome e hash sao validados antes de virar URL, o
  tamanho e limitado e nada do arquivo e executado ou usado como caminho.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from pesquisa360.db.models_tse import TseArquivoSecao, TseSecao

from . import discovery, sections
from .bu_decoder import MAX_BU_BYTES, BuInvalido, decode_bu
from .bu_store import (
    AGUARDANDO, ERRO, MAX_TENTATIVAS_PADRAO, PROCESSADO, REVERIFICAR_AGUARDANDO_SEGUNDOS, BuStore,
)
from .client import TseClient, TseHttpError, TseTransientError
from .config import TseSettings
from .normalization import origem_from_fase, to_datetime
from .repository import TseRepository

logger = logging.getLogger("pesquisa360.tse.bu")

LOTE_PADRAO = 50
LOTE_MAXIMO = 500
_NOME_ARQUIVO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_HASH = re.compile(r"^[0-9A-Fa-f]{8,200}$")
# Erros de UM boletim: sao registrados na secao e o lote continua. Qualquer
# outra excecao (inclusive o pedido de parada do worker) interrompe o lote.
ISOLAVEIS = (BuInvalido, TseHttpError, TseTransientError, ValueError, LookupError,
             UnicodeDecodeError, SQLAlchemyError)


@dataclass(frozen=True)
class BuScope:
    origem: str
    pleito: str
    uf: str
    batch_size: int = LOTE_PADRAO
    # Restringe a municipios (codigos TSE); () = toda a UF.
    municipios: tuple[str, ...] = ()

    def __post_init__(self):
        if not 1 <= self.batch_size <= LOTE_MAXIMO:
            raise ValueError(f"Lote de BU deve ficar entre 1 e {LOTE_MAXIMO}")


@dataclass
class BuReport:
    bu_discovered: int = 0       # secoes do lote (pendentes encontradas)
    bu_downloaded: int = 0
    bu_already_known: int = 0    # hash do EA18 ja baixado: nenhum download
    bu_decoded: int = 0
    bu_persisted: int = 0        # boletins NOVOS gravados
    bu_failed: int = 0
    bu_pending: int = 0          # secao sem BU 'Totalizado' no EA18
    restantes: int = 0           # pendentes que ficaram para os proximos lotes
    erros: list[dict] = field(default_factory=list)
    # (municipio, zona) com boletim novo neste lote: alvo da conferencia BU x EA20.
    zonas: set = field(default_factory=set)

    def contadores(self) -> dict:
        return {k: getattr(self, k) for k in (
            "bu_discovered", "bu_downloaded", "bu_already_known", "bu_decoded", "bu_persisted",
            "bu_failed", "bu_pending", "restantes")}


class BuIngestion:
    def __init__(self, session: Session, client: TseClient, settings: TseSettings, *,
                 archive_dir: Path | None = None,
                 max_tentativas: int = MAX_TENTATIVAS_PADRAO,
                 reverificar_segundos: int = REVERIFICAR_AGUARDANDO_SEGUNDOS):
        self.session = session
        self.client = client
        self.settings = settings
        self.repo = TseRepository(session)
        self.store = BuStore(session)
        self.archive_dir = Path(archive_dir) if archive_dir else None
        self.max_tentativas = max_tentativas
        self.reverificar_segundos = reverificar_segundos

    def run(self, scope: BuScope) -> BuReport:
        report = BuReport()
        uf = scope.uf.lower()
        config = self._config(scope)
        self.store.religar_votos(scope.origem, scope.pleito, uf)
        self.session.commit()
        fila = self.store.pendentes(scope.origem, scope.pleito, uf, scope.batch_size,
                                    scope.municipios, self.max_tentativas,
                                    self.reverificar_segundos)
        report.bu_discovered = len(fila)
        for secao_id in [s.id for s in fila]:
            secao = self.session.get(TseSecao, secao_id)
            chave = f"{secao.municipio_codigo}/{secao.zona}/{secao.secao}"
            try:
                self._secao(scope, config, uf, secao, report)
                self.session.commit()
            except ISOLAVEIS as exc:
                self.session.rollback()
                erro = f"{type(exc).__name__}: {exc}"[:300]
                secao = self.session.get(TseSecao, secao_id)
                self.store.marcar(secao, ERRO, erro=erro)
                self.session.commit()
                report.bu_failed += 1
                report.erros.append({"secao": chave, "erro": erro})
                # Nunca o binario: so a identificacao da secao e o motivo.
                logger.warning("bu_failed %s", json.dumps({"secao": chave, "erro": erro},
                                                          ensure_ascii=False))
        report.restantes = self.store.situacao(
            scope.origem, scope.pleito, uf, self.max_tentativas, scope.municipios,
            self.reverificar_segundos)["pendentes"]
        return report

    # ------------------------------------------------------------------ secao
    def _secao(self, scope: BuScope, config, uf: str, secao: TseSecao, report: BuReport) -> None:
        args = (config, uf, secao.municipio_codigo, secao.zona, secao.secao)
        auxiliar, snapshot = self._ea18(scope, discovery.ea18_url(*args))
        if auxiliar is None:
            self.store.marcar(secao, AGUARDANDO)
            report.bu_pending += 1
            return
        self.repo.record_ea18(secao, snapshot, auxiliar,
                              lambda hash_, nome, a=args: discovery.urna_file_url(*a, hash_, nome))
        corrente = sections.current_bu(auxiliar)
        if corrente is None:
            # Recebido mas ainda nao totalizado, ou EA18 sem arquivo: nao ha BU oficial.
            self.store.marcar(secao, AGUARDANDO)
            report.bu_pending += 1
            return
        hash_ea18, arquivo = corrente
        if not _HASH.match(hash_ea18.hash) or not _NOME_ARQUIVO.match(arquivo.nome):
            raise BuInvalido("EA18 com hash ou nome de arquivo fora do padrão esperado.")

        registro = self.session.scalars(select(TseArquivoSecao).where(
            TseArquivoSecao.secao_id == secao.id, TseArquivoSecao.hash == hash_ea18.hash,
            TseArquivoSecao.nome_arquivo == arquivo.nome)).first()
        if registro is not None and registro.sha256:
            conhecido = self.store.boletim(secao.id, registro.sha256)
            if conhecido is not None:
                # Arquivo ja baixado e gravado (inclui A -> B -> A): nada a buscar,
                # e o ponteiro do corrente nao volta para um boletim antigo.
                controle = self.store.controle(secao.id)
                primeiro = controle is None or controle.boletim_id is None
                self.store.marcar(secao, PROCESSADO, boletim=conhecido if primeiro else None)
                report.bu_already_known += 1
                return

        url = discovery.urna_file_url(*args, hash_ea18.hash, arquivo.nome)
        resposta = self.client.get(url)
        if resposta.not_found:
            self.store.marcar(secao, AGUARDANDO)
            report.bu_pending += 1
            return
        conteudo = resposta.content or b""
        if len(conteudo) > MAX_BU_BYTES:
            raise BuInvalido(f"Arquivo de BU com {len(conteudo)} bytes excede o limite.")
        report.bu_downloaded += 1
        bu = decode_bu(conteudo)
        report.bu_decoded += 1

        sha256 = hashlib.sha256(conteudo).hexdigest()
        snapshot_bu, _novo = self.repo.save_snapshot(
            origem=scope.origem, tipo="BU", response=resposta,
            arquivo_path=self._arquivar(scope.origem, sha256, conteudo))
        boletim, criado = self.store.record_bu(
            secao, snapshot_bu, bu, sha256=sha256, tamanho_bytes=len(conteudo),
            hash_ea18=hash_ea18.hash, situacao_ea18=hash_ea18.situacao,
            recebido_em=hash_ea18.recebido_em)
        if registro is not None:
            registro.sha256 = sha256
        controle = self.store.controle(secao.id)
        avancar = criado or controle is None or controle.boletim_id is None
        self.store.marcar(secao, PROCESSADO, boletim=boletim if avancar else None)
        if criado:
            report.bu_persisted += 1
            report.zonas.add((secao.municipio_codigo, secao.zona))

    def _ea18(self, scope: BuScope, url: str):
        """GET condicional do EA18. Devolve (auxiliar, snapshot) ou (None, None) em 404."""
        anterior = self.repo.latest_snapshot(url)
        resposta = self.client.get(
            url, etag=anterior.etag if anterior else None,
            last_modified=anterior.last_modified if anterior else None)
        if resposta.not_found:
            return None, None
        if resposta.not_modified:
            return sections.parse_ea18(anterior.payload_json), anterior
        doc = json.loads(resposta.content.decode("utf-8"))
        origem = origem_from_fase(doc.get("f"))
        if origem != scope.origem:
            raise ValueError(f"EA18 do ambiente {origem}, mas o escopo pede {scope.origem}")
        snapshot, _novo = self.repo.save_snapshot(
            origem=origem, tipo="EA18", response=resposta, payload=doc, idg=doc.get("idg"),
            gerado_em=to_datetime(doc.get("dg"), doc.get("hg")))
        return sections.parse_ea18(doc), snapshot

    # ---------------------------------------------------------------- suporte
    def _config(self, scope: BuScope):
        """Templates de URL do EA11 ja ingerido: a ingestao de BU nao busca o EA11."""
        ea11 = self.repo.latest_snapshot(
            discovery.ea11_url(self.settings.base_url, self.settings.ambiente))
        if ea11 is None or not ea11.payload_json:
            raise RuntimeError("EA11 ainda não ingerido: rode a ingestão do TSE antes dos BUs.")
        return discovery.parse_ea11(ea11.payload_json, base_url=self.settings.base_url,
                                    ambiente=self.settings.ambiente, pleito=scope.pleito)

    def _arquivar(self, origem: str, sha256: str, conteudo: bytes) -> str | None:
        """Guarda o arquivo original (opcional). O nome e o SHA-256, nunca o nome remoto."""
        if self.archive_dir is None:
            return None
        destino = self.archive_dir / origem.lower() / f"{sha256}.bu"
        if not destino.exists():
            destino.parent.mkdir(parents=True, exist_ok=True)
            temporario = destino.with_suffix(".tmp")
            temporario.write_bytes(conteudo)
            temporario.replace(destino)
        return str(destino)
