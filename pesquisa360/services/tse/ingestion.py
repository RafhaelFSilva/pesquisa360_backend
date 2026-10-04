"""Orquestracao da ingestao TSE: EA11 -> EA12 -> EA14 -> EA15 -> EA20 -> EA16 -> EA18.

Orientada por mudanca (ADR-080): o EA14 diz se a UF mudou, o EA15 diz quais
municipios mudaram, e so entao os EA20 correspondentes sao buscados. Toda
requisicao e condicional (ETag / Last-Modified do ultimo snapshot).

Execucao manual (CLI). Nao ha daemon, worker nem polling continuo.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from . import acompanhamento, discovery, ea20, sections
from .client import TseClient
from .config import TseSettings
from .normalization import OFICIAL, origem_from_fase, to_datetime
from .repository import TseRepository, abrangencia_key

TODOS = "*"


class TseOrigemError(RuntimeError):
    """Arquivo de um ambiente (oficial/simulado) diferente do escopo pedido."""


@dataclass(frozen=True)
class IngestScope:
    # None = descoberto no EA11 (`discovery.discover_pleito`).
    pleito: str | None
    uf: str
    cargos: tuple[str, ...]
    # Codigos TSE dos municipios com EA20 municipal; (TODOS,) = todos da UF.
    municipios: tuple[str, ...] = ()
    # Municipios cujas zonas tambem sao ingeridas; (TODOS,) = todos da UF.
    zonas_de: tuple[str, ...] = ()
    # Quantas secoes principais consultar no EA18 (prova de fluxo, nao carga).
    secoes_ea18: int = 0
    origem: str = OFICIAL
    # Ignora os gatilhos EA14/EA15 e reconsulta todos os EA20 do escopo.
    force: bool = False


@dataclass
class IngestReport:
    pleito: str | None = None
    requisicoes: int = 0
    # Requisicoes de rede por status HTTP (0 = falha de transporte).
    status_http: dict = field(default_factory=dict)
    nao_modificados: int = 0
    nao_encontrados: list[str] = field(default_factory=list)
    snapshots_novos: int = 0
    snapshots_repetidos: int = 0
    totalizacoes_novas: int = 0
    totalizacoes_inalteradas: int = 0
    ea20_ignorados_sem_mudanca: int = 0
    secoes: dict = field(default_factory=dict)
    arquivos_secao_novos: int = 0
    abrangencias_alteradas: list[str] = field(default_factory=list)
    eventos: list[str] = field(default_factory=list)


class TseIngestion:
    def __init__(self, session: Session, client: TseClient, settings: TseSettings):
        self.session = session
        self.client = client
        self.settings = settings
        self.repo = TseRepository(session)
        self.error_context = dict.fromkeys((
            "origem", "pleito", "eleicao", "cargo", "uf", "municipio", "zona",
            "url", "snapshot_id", "abrangencia_id", "cargo_id", "phase"))

    def run(self, scope: IngestScope) -> IngestReport:
        report = IngestReport()
        uf = scope.uf.lower()
        self.error_context.update(origem=scope.origem, pleito=scope.pleito, uf=uf)
        inicio = len(self.client.requests)

        ea11_doc, _snap, _new = self._fetch(
            scope, report, "EA11",
            discovery.ea11_url(self.settings.base_url, self.settings.ambiente))
        if ea11_doc is None:
            raise RuntimeError("EA11 indisponivel: nao ha como descobrir a eleicao")
        report.pleito = scope.pleito or discovery.discover_pleito(ea11_doc, scope.cargos)
        self.error_context.update(pleito=report.pleito, phase="parse_ea11")
        config = discovery.parse_ea11(ea11_doc, base_url=self.settings.base_url,
                                      ambiente=self.settings.ambiente, pleito=report.pleito)
        self.error_context["phase"] = "upsert_catalog"
        eleicoes = self.repo.upsert_eleicoes(config)

        por_eleicao: dict[str, list[str]] = {}
        for cargo in scope.cargos:
            codigo = discovery.election_for_cargo(config, cargo).codigo
            por_eleicao.setdefault(codigo, []).append(f"{int(cargo):04d}")

        for codigo, cargos in por_eleicao.items():
            self._ingest_election(scope, report, config, eleicoes[codigo], cargos, uf)

        self._ingest_sections(scope, report, config, uf)
        self.error_context["phase"] = "commit"
        self.session.commit()
        feitas = self.client.requests[inicio:]
        report.requisicoes = len(feitas)
        report.status_http = dict(Counter(str(status) for _t, _url, status in feitas))
        return report

    # ---------------------------------------------------------------- eleicao
    def _ingest_election(self, scope, report, config, eleicao, cargos, uf):
        codigo = eleicao.codigo_eleicao
        self.error_context.update(eleicao=codigo, cargo=None, municipio=None, zona=None,
                                  cargo_id=None, abrangencia_id=None)
        ea12_doc, _s, _n = self._fetch(scope, report, "EA12", discovery.ea12_url(config, codigo))
        self.error_context["phase"] = "parse_ea12"
        municipios = {m.codigo: m for m in discovery.parse_ea12(ea12_doc, uf)}
        self.error_context["phase"] = "upsert_abrangencia"
        abr_uf = self.repo.upsert_abrangencia(eleicao, uf=uf)
        for municipio in municipios.values():
            self.repo.upsert_abrangencia(eleicao, uf=uf, municipio=municipio)

        alvo_municipios = (sorted(municipios) if TODOS in scope.municipios
                           else [f"{int(m):05d}" for m in scope.municipios])
        alvo_zonas = (sorted(municipios) if TODOS in scope.zonas_de
                      else [f"{int(m):05d}" for m in scope.zonas_de])
        for cod in set(alvo_municipios) | set(alvo_zonas):
            if cod not in municipios:
                raise LookupError(f"Municipio {cod} nao pertence a UF {uf} no EA12")

        mudou_uf, linhas_uf = self._changed(scope, report, "EA14",
                                            discovery.ea14_url(config, codigo))
        linha_uf = linhas_uf.get(f"uf:{uf}")
        cargo_rows = {cargo: self.repo.get_cargo(cargo) for cargo in cargos}

        def precisa(abrangencia, linha, mudou):
            return scope.force or mudou or any(
                self._stale(abrangencia, row, linha) for row in cargo_rows.values())

        abr_municipios = {cod: self.repo.get_abrangencia(eleicao.id, abrangencia_key(uf, cod))
                          for cod in set(alvo_municipios) | set(alvo_zonas)}
        uf_precisa = precisa(abr_uf, linha_uf, f"uf:{uf}" in mudou_uf)
        # O EA15 so e consultado se a UF mudou ou se falta dado municipal no escopo.
        mudou_mun, linhas_mun = set(), {}
        if uf_precisa or any(precisa(abr, None, False) for abr in abr_municipios.values()):
            mudou_mun, linhas_mun = self._changed(scope, report, "EA15",
                                                  discovery.ea15_url(config, codigo, uf))
        report.abrangencias_alteradas += sorted(
            (mudou_uf & {f"uf:{uf}"}) | {k for k in mudou_mun if k.startswith("mun:")})

        for cargo, cargo_row in cargo_rows.items():
            self._ea20(scope, report, config, eleicao, cargo, cargo_row, uf, abr_uf,
                       linha=linha_uf, mudou=f"uf:{uf}" in mudou_uf)
            for cod in alvo_municipios:
                self._ea20(scope, report, config, eleicao, cargo, cargo_row, uf,
                           abr_municipios[cod], municipio=cod,
                           linha=linhas_mun.get(f"mun:{cod}"), mudou=f"mun:{cod}" in mudou_mun)
            for cod in alvo_zonas:
                for zona in municipios[cod].zonas:
                    abr = self.repo.upsert_abrangencia(eleicao, uf=uf, municipio=municipios[cod],
                                                       zona=zona)
                    # O acompanhamento nao desce a zona: vale o gatilho do municipio.
                    self._ea20(scope, report, config, eleicao, cargo, cargo_row, uf, abr,
                               municipio=cod, zona=zona, linha=None,
                               mudou=f"mun:{cod}" in mudou_mun)

    def _changed(self, scope, report, tipo, url):
        """(chaves que mudaram desde o snapshot anterior, linhas atuais por chave)."""
        previous = self.repo.latest_snapshot(url)
        doc, _snapshot, new = self._fetch(scope, report, tipo, url)
        if doc is None:
            return set(), {}
        self.error_context["phase"] = f"parse_{tipo.lower()}"
        atual = acompanhamento.parse_acompanhamento(doc)
        current = atual.fingerprints()
        if previous is None:
            changed = set(current)
        elif not new:
            changed = set()
        else:
            before = acompanhamento.parse_acompanhamento(previous.payload_json).fingerprints()
            changed = set(acompanhamento.changed_keys(before, current))
        return changed, {linha.chave: linha for linha in atual.linhas}

    def _stale(self, abrangencia, cargo_row, linha) -> bool:
        """Sem totalizacao gravada, ou gravada atras do que o acompanhamento informa."""
        total = self.repo.latest_totalizacao(abrangencia.id, cargo_row.id)
        if total is None:
            return True
        if linha is None:
            return False
        return (total.secoes_totalizadas, total.andamento) != (
            linha.secoes_totalizadas, linha.andamento)

    def _ea20(self, scope, report, config, eleicao, cargo, cargo_row, uf, abrangencia, *,
              municipio=None, zona=None, linha=None, mudou: bool):
        if not (scope.force or mudou or self._stale(abrangencia, cargo_row, linha)):
            report.ea20_ignorados_sem_mudanca += 1
            return
        url = discovery.ea20_url(config, eleicao.codigo_eleicao, uf, cargo, municipio, zona)
        self.error_context.update(eleicao=eleicao.codigo_eleicao, cargo=cargo,
                                  cargo_id=cargo_row.id, abrangencia_id=abrangencia.id,
                                  municipio=municipio, zona=zona)
        doc, snapshot, _new = self._fetch(scope, report, "EA20", url)
        if doc is None:
            return
        self.error_context["phase"] = "parse_ea20"
        resultado = ea20.parse_ea20(doc)
        self.error_context["phase"] = "record_ea20"
        _total, created = self.repo.record_ea20(eleicao, abrangencia, snapshot, resultado, uf)
        if created:
            report.totalizacoes_novas += 1
            report.eventos.append(
                f"nova totalizacao {abrangencia.chave} cargo {cargo} idg {resultado.idg} "
                f"secoes {resultado.secoes_totalizadas}/{resultado.secoes}")
        else:
            report.totalizacoes_inalteradas += 1

    # ----------------------------------------------------------------- secoes
    def _ingest_sections(self, scope, report, config, uf):
        self.error_context.update(eleicao=None, cargo=None, cargo_id=None, abrangencia_id=None,
                                  municipio=None, zona=None)
        doc, _snapshot, _new = self._fetch(scope, report, "EA16", discovery.ea16_url(config, uf))
        if doc is None:
            return
        self.error_context["phase"] = "parse_ea16"
        parsed = sections.parse_ea16(doc, uf)
        self.error_context["phase"] = "sync_secoes"
        report.secoes = self.repo.sync_secoes(scope.origem, parsed)
        if not scope.secoes_ea18:
            return
        municipio = f"{int(scope.zonas_de[0]):05d}" if scope.zonas_de else None
        # So secoes principais ja totalizadas tem arquivo auxiliar.
        candidatas = [s for s in self.repo.secoes(scope.origem, config.pleito, uf, municipio,
                                                  principais=True) if s.auxiliar_em is not None]
        for secao in candidatas[: scope.secoes_ea18]:
            args = (config, uf, secao.municipio_codigo, secao.zona, secao.secao)
            self.error_context.update(municipio=secao.municipio_codigo, zona=secao.zona)
            aux_doc, snapshot, _n = self._fetch(scope, report, "EA18",
                                                discovery.ea18_url(*args))
            if aux_doc is None:
                continue
            report.arquivos_secao_novos += self.repo.record_ea18(
                secao, snapshot, sections.parse_ea18(aux_doc),
                lambda hash_, nome, a=args: discovery.urna_file_url(*a, hash_, nome))

    # ------------------------------------------------------------------ fetch
    def _fetch(self, scope, report, tipo, url):
        """GET condicional + snapshot. Devolve (documento, snapshot, snapshot_novo)."""
        self.error_context.update(url=url, snapshot_id=None, phase="latest_snapshot")
        previous = self.repo.latest_snapshot(url)
        self.error_context["phase"] = "http_get"
        response = self.client.get(
            url, etag=previous.etag if previous else None,
            last_modified=previous.last_modified if previous else None)
        if response.not_found:
            report.nao_encontrados.append(url)
            return None, None, False
        if response.not_modified:
            self.error_context.update(snapshot_id=previous.id, phase="cached_snapshot")
            report.nao_modificados += 1
            return previous.payload_json, previous, False

        self.error_context["phase"] = "decode_json"
        doc = json.loads(response.content.decode("utf-8"))
        origem = origem_from_fase(doc.get("f"))
        if origem != scope.origem:
            raise TseOrigemError(
                f"{tipo} {url} e do ambiente {origem}, mas o escopo pede {scope.origem}")
        self.error_context["phase"] = "save_snapshot"
        snapshot, created = self.repo.save_snapshot(
            origem=origem, tipo=tipo, response=response, payload=doc, idg=doc.get("idg"),
            gerado_em=to_datetime(doc.get("dg"), doc.get("hg")))
        self.error_context["snapshot_id"] = snapshot.id
        if created:
            report.snapshots_novos += 1
        else:
            report.snapshots_repetidos += 1
        return doc, snapshot, created
