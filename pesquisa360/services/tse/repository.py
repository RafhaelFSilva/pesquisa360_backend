"""Persistencia e consultas do dominio TSE.

Regras:
- nenhum filtro por tenant: os dados sao publicos e globais (ADR-076);
- snapshots, totalizacoes e resultados sao append-only (ADR-079);
- gravar duas vezes o mesmo arquivo nao duplica nada.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pesquisa360.db.models_tse import (
    TseAbrangencia, TseArquivoSecao, TseCandidato, TseCargo, TseEleicao, TseFederacao,
    TsePartido, TseResultadoCandidato, TseResultadoPartido, TseSecao, TseSnapshot,
    TseTotalizacao,
)

from .client import TseResponse
from .normalization import (
    BRASILIA, AuxiliarSecao, ConfiguracaoSecoes, Ea20, Municipio, PleitoConfig,
)

TIPO_POR_TPABR = {"br": "BR", "uf": "UF", "mu": "MUNICIPIO", "zona": "ZONA"}


def abrangencia_key(uf: str | None = None, municipio: str | None = None,
                    zona: str | None = None) -> str:
    if uf is None:
        return "br"
    if municipio is None:
        return f"uf:{uf}"
    if zona is None:
        return f"mu:{uf}:{municipio}"
    return f"zona:{uf}:{municipio}:{zona}"


def content_hash(resultado: Ea20) -> str:
    """Hash do conteudo MATERIAL de um EA20.

    Exclui IDG e data de geracao: o TSE pode regerar o arquivo sem que nada
    tenha mudado, e isso nao e uma nova totalizacao.
    """
    material = [
        resultado.andamento, resultado.totalizacao_final,
        resultado.ultima_totalizacao.isoformat() if resultado.ultima_totalizacao else None,
        resultado.secoes, resultado.secoes_totalizadas, resultado.eleitorado,
        resultado.comparecimento, resultado.abstencao, resultado.votos_total,
        resultado.votos_validos, resultado.votos_nominais, resultado.votos_legenda,
        resultado.votos_brancos, resultado.votos_nulos, resultado.votos_anulados,
        resultado.votos_anulados_sub_judice, resultado.quociente_eleitoral,
        sorted([c.sqcand, c.votos, c.situacao, c.eleito, c.destinacao_voto]
               for c in resultado.candidatos),
        sorted([p.numero, p.votos_nominais, p.votos_legenda, p.destinacao_voto]
               for p in resultado.partidos),
    ]
    return hashlib.sha256(json.dumps(material, ensure_ascii=False).encode("utf-8")).hexdigest()


class TseRepository:
    def __init__(self, session: Session):
        self.session = session

    # ------------------------------------------------------------- snapshots
    def latest_snapshot(self, url: str) -> TseSnapshot | None:
        return self.session.scalars(
            select(TseSnapshot).where(TseSnapshot.url == url)
            .order_by(TseSnapshot.id.desc()).limit(1)
        ).first()

    def save_snapshot(self, *, origem: str, tipo: str, response: TseResponse,
                      payload: dict | None = None, idg: str | None = None,
                      gerado_em: datetime | None = None,
                      arquivo_path: str | None = None) -> tuple[TseSnapshot, bool]:
        """Grava o arquivo recebido; devolve (snapshot, criado). Idempotente por (url, sha256)."""
        sha256 = hashlib.sha256(response.content).hexdigest()
        existing = self.session.scalars(
            select(TseSnapshot).where(TseSnapshot.url == response.url,
                                      TseSnapshot.sha256 == sha256)
        ).first()
        if existing:
            return existing, False
        snapshot = TseSnapshot(
            origem=origem, tipo=tipo, url=response.url, http_status=response.status,
            etag=response.etag, last_modified=response.last_modified, idg=idg,
            gerado_em=gerado_em, sha256=sha256, tamanho_bytes=len(response.content),
            payload_json=payload, arquivo_path=arquivo_path,
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot, True

    # -------------------------------------------------------------- catalogo
    def upsert_eleicoes(self, config: PleitoConfig) -> dict[str, TseEleicao]:
        data = datetime.strptime(config.data, "%d/%m/%Y").date() if config.data else None
        result = {}
        for ele in config.eleicoes:
            row = self.session.scalars(select(TseEleicao).where(
                TseEleicao.origem == config.origem, TseEleicao.pleito == config.pleito,
                TseEleicao.codigo_eleicao == ele.codigo)).first()
            if row is None:
                row = TseEleicao(origem=config.origem, pleito=config.pleito,
                                 codigo_eleicao=ele.codigo)
                self.session.add(row)
            row.ambiente, row.ciclo, row.nome = config.ambiente, config.ciclo, ele.nome
            row.turno, row.tipo, row.data_eleicao = ele.turno, ele.tipo, data
            row.codigo_segundo_turno = ele.codigo_segundo_turno
            row.metadata_json = {"cargos": [c.codigo for c in ele.cargos],
                                 "abrangencias": list(ele.abrangencias)}
            for cargo in ele.cargos:
                self.get_cargo(cargo.codigo, cargo.nome)
            result[ele.codigo] = row
        self.session.flush()
        return result

    def get_eleicao(self, origem: str, pleito: str, codigo: str) -> TseEleicao | None:
        return self.session.scalars(select(TseEleicao).where(
            TseEleicao.origem == origem, TseEleicao.pleito == str(pleito),
            TseEleicao.codigo_eleicao == str(codigo))).first()

    def get_cargo(self, codigo: str, nome: str | None = None) -> TseCargo:
        row = self.session.scalars(select(TseCargo).where(TseCargo.codigo == codigo)).first()
        if row is None:
            row = TseCargo(codigo=codigo, nome=nome or codigo)
            self.session.add(row)
            self.session.flush()
        return row

    def upsert_abrangencia(self, eleicao: TseEleicao, *, uf: str | None = None,
                           municipio: Municipio | None = None,
                           zona: str | None = None) -> TseAbrangencia:
        """Cria a abrangencia e seus ancestrais (BR <- UF <- municipio <- zona)."""
        codigo_mun = municipio.codigo if municipio else None
        chave = abrangencia_key(uf, codigo_mun, zona)
        row = self.get_abrangencia(eleicao.id, chave)
        if row is not None:
            return row
        if zona:
            tipo, parent = "ZONA", self.upsert_abrangencia(eleicao, uf=uf, municipio=municipio)
        elif municipio:
            tipo, parent = "MUNICIPIO", self.upsert_abrangencia(eleicao, uf=uf)
        elif uf:
            tipo, parent = "UF", self.upsert_abrangencia(eleicao)
        else:
            tipo, parent = "BR", None
        row = TseAbrangencia(
            eleicao_id=eleicao.id, tipo=tipo, chave=chave, uf=uf, municipio_codigo=codigo_mun,
            municipio_nome=municipio.nome if municipio else None, zona=zona,
            parent_id=parent.id if parent else None,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_abrangencia(self, eleicao_id: int, chave: str) -> TseAbrangencia | None:
        return self.session.scalars(select(TseAbrangencia).where(
            TseAbrangencia.eleicao_id == eleicao_id, TseAbrangencia.chave == chave)).first()

    def children(self, abrangencia_id: int) -> list[TseAbrangencia]:
        return list(self.session.scalars(select(TseAbrangencia).where(
            TseAbrangencia.parent_id == abrangencia_id).order_by(TseAbrangencia.chave)))

    # ------------------------------------------------------------------ EA20
    def latest_totalizacao(self, abrangencia_id: int, cargo_id: int) -> TseTotalizacao | None:
        return self.session.scalars(
            select(TseTotalizacao).where(TseTotalizacao.abrangencia_id == abrangencia_id,
                                         TseTotalizacao.cargo_id == cargo_id)
            .order_by(TseTotalizacao.id.desc()).limit(1)
        ).first()

    def record_ea20(self, eleicao: TseEleicao, abrangencia: TseAbrangencia,
                    snapshot: TseSnapshot, resultado: Ea20,
                    uf: str) -> tuple[TseTotalizacao, bool]:
        """Registra uma totalizacao; devolve (totalizacao, criada).

        So cria linha nova se o conteudo material difere da ultima totalizacao
        da mesma (abrangencia, cargo). Nunca atualiza uma existente.
        """
        if resultado.eleicao != eleicao.codigo_eleicao:
            raise ValueError(
                f"EA20 da eleicao {resultado.eleicao} nao pertence a {eleicao.codigo_eleicao}")
        if TIPO_POR_TPABR.get(resultado.tipo_abrangencia) != abrangencia.tipo:
            raise ValueError(
                f"EA20 tpabr={resultado.tipo_abrangencia} incompativel com {abrangencia.chave}")

        cargo = self.get_cargo(resultado.cargo, resultado.cargo_nome)
        digest = content_hash(resultado)
        latest = self.latest_totalizacao(abrangencia.id, cargo.id)
        if latest is not None and latest.conteudo_hash == digest:
            return latest, False

        candidatos = self._upsert_candidatos(eleicao, cargo, resultado, uf)
        partidos = self._partidos(eleicao.id)
        total = TseTotalizacao(
            eleicao_id=eleicao.id, cargo_id=cargo.id, abrangencia_id=abrangencia.id,
            snapshot_id=snapshot.id, idg=resultado.idg, gerado_em=resultado.gerado_em,
            ultima_totalizacao=resultado.ultima_totalizacao, andamento=resultado.andamento,
            totalizacao_final=resultado.totalizacao_final, vagas=resultado.vagas,
            secoes_total=resultado.secoes, secoes_totalizadas=resultado.secoes_totalizadas,
            eleitores=resultado.eleitorado, comparecimento=resultado.comparecimento,
            abstencoes=resultado.abstencao, votos_total=resultado.votos_total,
            votos_validos=resultado.votos_validos, votos_nominais=resultado.votos_nominais,
            votos_legenda=resultado.votos_legenda, votos_brancos=resultado.votos_brancos,
            votos_nulos=resultado.votos_nulos, votos_anulados=resultado.votos_anulados,
            votos_anulados_sub_judice=resultado.votos_anulados_sub_judice,
            quociente_eleitoral=resultado.quociente_eleitoral, conteudo_hash=digest,
        )
        self.session.add(total)
        self.session.flush()
        self.session.add_all(TseResultadoCandidato(
            totalizacao_id=total.id, candidato_id=candidatos[c.sqcand].id, votos=c.votos or 0,
            percentual=c.percentual, situacao=c.situacao, eleito=c.eleito,
            destinacao_voto=c.destinacao_voto,
        ) for c in resultado.candidatos)
        self.session.add_all(TseResultadoPartido(
            totalizacao_id=total.id, partido_id=partidos[p.numero].id,
            votos_nominais=p.votos_nominais, votos_legenda=p.votos_legenda,
            destinacao_voto=p.destinacao_voto,
        ) for p in resultado.partidos)
        self.session.flush()
        return total, True

    def _partidos(self, eleicao_id: int) -> dict[str, TsePartido]:
        return {p.numero: p for p in self.session.scalars(
            select(TsePartido).where(TsePartido.eleicao_id == eleicao_id))}

    def _upsert_candidatos(self, eleicao: TseEleicao, cargo: TseCargo, resultado: Ea20,
                           uf: str) -> dict[str, TseCandidato]:
        federacoes = {f.numero: f for f in self.session.scalars(
            select(TseFederacao).where(TseFederacao.eleicao_id == eleicao.id))}
        for fed in resultado.federacoes:
            if fed.numero not in federacoes:
                federacoes[fed.numero] = TseFederacao(
                    eleicao_id=eleicao.id, numero=fed.numero, sigla=fed.sigla, nome=fed.nome)
                self.session.add(federacoes[fed.numero])
        self.session.flush()

        partidos = self._partidos(eleicao.id)
        for par in resultado.partidos:
            if par.numero not in partidos:
                fed = federacoes.get(par.federacao_numero) if par.federacao_numero else None
                partidos[par.numero] = TsePartido(
                    eleicao_id=eleicao.id, numero=par.numero, sigla=par.sigla, nome=par.nome,
                    federacao_id=fed.id if fed else None)
                self.session.add(partidos[par.numero])
        self.session.flush()

        sqcands = [c.sqcand for c in resultado.candidatos]
        candidatos = {c.sqcand: c for c in self.session.scalars(
            select(TseCandidato).where(TseCandidato.eleicao_id == eleicao.id,
                                       TseCandidato.sqcand.in_(sqcands)))}
        for cand in resultado.candidatos:
            if cand.sqcand not in candidatos:
                candidatos[cand.sqcand] = TseCandidato(
                    eleicao_id=eleicao.id, cargo_id=cargo.id, uf=uf, sqcand=cand.sqcand,
                    numero=cand.numero, nome=cand.nome, nome_urna=cand.nome_urna,
                    partido_id=partidos[cand.partido_numero].id)
                self.session.add(candidatos[cand.sqcand])
        self.session.flush()
        return candidatos

    # ---------------------------------------------------------------- secoes
    def sync_secoes(self, origem: str, config: ConfiguracaoSecoes) -> dict[str, int]:
        """Espelha o EA16. Secao e configuracao: `auxiliar_em` pode ser atualizado."""
        existing = {(s.municipio_codigo, s.zona, s.secao): s for s in self.session.scalars(
            select(TseSecao).where(TseSecao.origem == origem, TseSecao.pleito == config.pleito,
                                   TseSecao.uf == config.uf))}
        created = updated = 0
        for municipio in config.municipios:
            for sec in municipio.secoes:
                row = existing.get((sec.municipio, sec.zona, sec.secao))
                if row is None:
                    self.session.add(TseSecao(
                        origem=origem, pleito=config.pleito, uf=config.uf,
                        municipio_codigo=sec.municipio, zona=sec.zona, secao=sec.secao,
                        eh_principal=sec.eh_principal, secao_principal=sec.secao_principal,
                        auxiliar_em=sec.auxiliar_em))
                    created += 1
                elif (_iso(row.auxiliar_em), row.secao_principal) != (
                        _iso(sec.auxiliar_em), sec.secao_principal):
                    row.auxiliar_em = sec.auxiliar_em
                    row.eh_principal, row.secao_principal = sec.eh_principal, sec.secao_principal
                    updated += 1
        self.session.flush()
        return {"criadas": created, "atualizadas": updated}

    def secoes(self, origem: str, pleito: str, uf: str, municipio: str | None = None,
               principais: bool | None = None) -> list[TseSecao]:
        query = select(TseSecao).where(TseSecao.origem == origem, TseSecao.pleito == pleito,
                                       TseSecao.uf == uf)
        if municipio:
            query = query.where(TseSecao.municipio_codigo == municipio)
        if principais is not None:
            query = query.where(TseSecao.eh_principal.is_(principais))
        return list(self.session.scalars(
            query.order_by(TseSecao.municipio_codigo, TseSecao.zona, TseSecao.secao)))

    def record_ea18(self, secao: TseSecao, snapshot: TseSnapshot, auxiliar: AuxiliarSecao,
                    url_for) -> int:
        """Registra os arquivos de urna listados; `url_for(hash, nome)` monta a URL."""
        known = {(a.hash, a.nome_arquivo) for a in self.session.scalars(
            select(TseArquivoSecao).where(TseArquivoSecao.secao_id == secao.id))}
        created = 0
        for h in auxiliar.hashes:
            if not h.hash:
                continue
            for arquivo in h.arquivos:
                if (h.hash, arquivo.nome) in known:
                    continue
                self.session.add(TseArquivoSecao(
                    secao_id=secao.id, snapshot_id=snapshot.id, hash=h.hash,
                    situacao=h.situacao, recebido_em=h.recebido_em, tipo_arquivo=arquivo.tipo,
                    nome_arquivo=arquivo.nome, url=url_for(h.hash, arquivo.nome)))
                created += 1
        self.session.flush()
        return created

    # -------------------------------------------------------------- consultas
    def find_candidato(self, eleicao_id: int, *, sqcand: str | None = None,
                       numero: str | None = None, cargo: str | None = None,
                       uf: str | None = None) -> TseCandidato | None:
        """Por `sqcand`; por numero so com cargo e UF (o numero e contextual)."""
        query = select(TseCandidato).where(TseCandidato.eleicao_id == eleicao_id)
        if sqcand:
            return self.session.scalars(query.where(TseCandidato.sqcand == str(sqcand))).first()
        if not (numero and cargo and uf):
            raise ValueError("Informe sqcand, ou numero + cargo + uf")
        found = list(self.session.scalars(
            query.join(TseCargo).where(TseCandidato.numero == str(numero),
                                       TseCargo.codigo == cargo, TseCandidato.uf == uf)))
        if len(found) > 1:
            raise LookupError(f"Numero {numero} ambiguo para {cargo}/{uf}")
        return found[0] if found else None

    def _latest_ids(self):
        """Subconsulta: id da totalizacao corrente de cada (abrangencia, cargo)."""
        return (select(func.max(TseTotalizacao.id))
                .group_by(TseTotalizacao.abrangencia_id, TseTotalizacao.cargo_id))

    def candidate_votes(self, candidato_id: int, tipo: str | None = None,
                        parent_id: int | None = None) -> list[dict]:
        """Voto corrente do candidato por abrangencia (A, B e C da modelagem)."""
        query = (
            select(TseAbrangencia, TseTotalizacao, TseResultadoCandidato)
            .join(TseTotalizacao, TseTotalizacao.abrangencia_id == TseAbrangencia.id)
            .join(TseResultadoCandidato, TseResultadoCandidato.totalizacao_id == TseTotalizacao.id)
            .where(TseResultadoCandidato.candidato_id == candidato_id,
                   TseTotalizacao.id.in_(self._latest_ids()))
            .order_by(TseAbrangencia.chave)
        )
        if tipo:
            query = query.where(TseAbrangencia.tipo == tipo)
        if parent_id is not None:
            query = query.where(TseAbrangencia.parent_id == parent_id)
        return [_voto(abr, tot, res) for abr, tot, res in self.session.execute(query)]

    def candidate_history(self, candidato_id: int, abrangencia_id: int) -> list[dict]:
        """Evolucao temporal: uma linha por totalizacao registrada (E)."""
        query = (
            select(TseAbrangencia, TseTotalizacao, TseResultadoCandidato)
            .join(TseTotalizacao, TseTotalizacao.abrangencia_id == TseAbrangencia.id)
            .join(TseResultadoCandidato, TseResultadoCandidato.totalizacao_id == TseTotalizacao.id)
            .where(TseResultadoCandidato.candidato_id == candidato_id,
                   TseAbrangencia.id == abrangencia_id)
            .order_by(TseTotalizacao.id)
        )
        return [_voto(abr, tot, res) for abr, tot, res in self.session.execute(query)]

    def ranking(self, abrangencia_id: int, cargo_id: int, limit: int | None = None,
                partido_id: int | None = None) -> list[dict]:
        """Candidatos da abrangencia ordenados por voto (G, H, I, J)."""
        total = self.latest_totalizacao(abrangencia_id, cargo_id)
        if total is None:
            return []
        query = (
            select(TseCandidato, TsePartido, TseResultadoCandidato)
            .join(TseResultadoCandidato, TseResultadoCandidato.candidato_id == TseCandidato.id)
            .join(TsePartido, TsePartido.id == TseCandidato.partido_id)
            .where(TseResultadoCandidato.totalizacao_id == total.id)
            .order_by(TseResultadoCandidato.votos.desc(), TseCandidato.sqcand)
        )
        if partido_id is not None:
            query = query.where(TseCandidato.partido_id == partido_id)
        if limit:
            query = query.limit(limit)
        return [{
            "sqcand": cand.sqcand, "numero": cand.numero, "nome_urna": cand.nome_urna,
            "partido": par.sigla, "votos": res.votos,
            "percentual": float(res.percentual) if res.percentual is not None else None,
            "situacao": res.situacao, "destinacao_voto": res.destinacao_voto,
        } for cand, par, res in self.session.execute(query)]

    def party_totals(self, abrangencia_id: int, cargo_id: int) -> list[dict]:
        """Votos por partido, com a federacao, na totalizacao corrente (F)."""
        total = self.latest_totalizacao(abrangencia_id, cargo_id)
        if total is None:
            return []
        query = (
            select(TsePartido, TseFederacao, TseResultadoPartido)
            .join(TseResultadoPartido, TseResultadoPartido.partido_id == TsePartido.id)
            .outerjoin(TseFederacao, TseFederacao.id == TsePartido.federacao_id)
            .where(TseResultadoPartido.totalizacao_id == total.id)
            .order_by(TsePartido.numero)
        )
        return [{
            "numero": par.numero, "sigla": par.sigla, "federacao": fed.sigla if fed else None,
            "votos_nominais": res.votos_nominais, "votos_legenda": res.votos_legenda,
            "destinacao_voto": res.destinacao_voto,
        } for par, fed, res in self.session.execute(query)]


def _voto(abr: TseAbrangencia, tot: TseTotalizacao, res: TseResultadoCandidato) -> dict:
    return {
        "abrangencia_id": abr.id, "tipo": abr.tipo, "chave": abr.chave, "uf": abr.uf,
        "municipio_codigo": abr.municipio_codigo, "municipio_nome": abr.municipio_nome,
        "zona": abr.zona, "votos": res.votos,
        "percentual": float(res.percentual) if res.percentual is not None else None,
        "situacao": res.situacao, "destinacao_voto": res.destinacao_voto,
        "totalizacao_id": tot.id, "idg": tot.idg, "gerado_em": tot.gerado_em,
        "ultima_totalizacao": tot.ultima_totalizacao, "andamento": tot.andamento,
        "secoes_total": tot.secoes_total, "secoes_totalizadas": tot.secoes_totalizadas,
    }


def _iso(value: datetime | None) -> str | None:
    """Instante em UTC. SQLite devolve datetime sem fuso (horario de Brasilia gravado)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=BRASILIA)
    return value.astimezone(timezone.utc).isoformat()
