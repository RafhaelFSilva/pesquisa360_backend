"""Locais identificados pelo BU corrente; agregados analiticos, nunca EA20.

Identidade: origem/pleito/UF/municipio/zona/NumeroLocal (ASN.1 oficial).
EA16 vincula as agregadas ao local da urna principal. Nao inventa nomes,
enderecos nem locais para secoes cujo BU ainda nao foi ingerido.
"""
from sqlalchemy import func, select

from pesquisa360.db.models_tse import (
    TseBoletimUrna, TseBuCargo, TseBuControle, TseBuVoto, TseCandidato,
    TseCargo, TseFederacao, TseLocalVotacao, TsePartido, TseSecao,
)

FONTE_LOCAL = 'BU_AGREGADO_LOCAL'


def locais_das_secoes(secoes):
    locais = {}
    for s in secoes:
        code = s['local_votacao']
        if code is None:
            continue
        item = locais.setdefault(code, {'codigo': code, 'nome': None, 'endereco': None,
            'bairro': None, 'latitude': None, 'longitude': None,
            'secoes': 0, 'principais': 0, 'agregadas': 0, 'bus': 0,
            'vinculo': 'LOCAL_DA_URNA_DO_GRUPO', 'fonte': 'BU_EA16'})
        item['secoes'] += 1
        item['principais' if s['principal'] else 'agregadas'] += 1
        item['bus'] += int(s['principal'] and s['resultado_disponivel'])
    return [locais[k] for k in sorted(locais)]


def rotulo_do_local(codigo, nome=None):
    """"NOME — Local 2720" ou, sem metadado, "Local 2720". Nunca vazio."""
    return f"{nome} — Local {codigo}" if nome else f"Local {codigo}"


class LocalAnalytics:
    def __init__(self, base):
        self.base, self.session = base, base.session

    def listar(self, eleicao, abr, secoes):
        """Locais da zona (codigo do BU) com nome e endereco oficiais, quando importados.

        Uma consulta por zona. O metadado e so apresentacao: local sem registro
        em `tse_locais_votacao` continua valendo, identificado pelo codigo.
        """
        locais = locais_das_secoes(secoes)
        meta = {m.codigo_local: m for m in self.session.scalars(
            select(TseLocalVotacao).where(
                TseLocalVotacao.origem == eleicao.origem, TseLocalVotacao.pleito == eleicao.pleito,
                TseLocalVotacao.uf == abr.uf, TseLocalVotacao.municipio_codigo == abr.municipio_codigo,
                TseLocalVotacao.zona == abr.zona))} if locais else {}
        for local in locais:
            m = meta.get(local['codigo'])
            local.update({
                'nome': m.nome if m else None, 'endereco': m.endereco if m else None,
                'bairro': m.bairro if m else None,
                'rotulo': rotulo_do_local(local['codigo'], m.nome if m else None),
                'quantidade_secoes': local['secoes'],
                # Secoes deste local que votaram em outro endereco neste pleito.
                'secoes_realocadas': m.secoes_realocadas if m else None,
                'metadados': {'fonte': m.fonte, 'gerada_em': m.fonte_gerada_em.isoformat()
                              if m.fonte_gerada_em else None} if m else None,
            })
        return locais

    def contexto(self, eleicao, uf, municipio, zona, codigo):
        from .analytics import TseNotFound
        if not municipio or not zona:
            raise ValueError('Local de votação exige município e zona.')
        abr = self.base._abrangencia(eleicao, uf, municipio, zona)
        secoes = self.base.secoes.opcoes(eleicao, abr)
        local = next((l for l in self.listar(eleicao, abr, secoes) if l['codigo'] == codigo), None)
        if local is None:
            raise TseNotFound('Local de votação não encontrado nesta zona.')
        return abr, local, [s for s in secoes if s['local_votacao'] == codigo]

    def validar_secao(self, eleicao, uf, municipio, zona, codigo, secao):
        from .analytics import TseNotFound
        _abr, _local, secoes = self.contexto(eleicao, uf, municipio, zona, codigo)
        if not any(s['secao'] == secao for s in secoes):
            raise TseNotFound('Seção não pertence a este local de votação.')

    def _filtros(self, eleicao, abr, codigo):
        return (TseSecao.origem == eleicao.origem, TseSecao.pleito == eleicao.pleito,
            TseSecao.uf == abr.uf, TseSecao.municipio_codigo == abr.municipio_codigo,
            TseSecao.zona == abr.zona, TseSecao.eh_principal.is_(True),
            TseBoletimUrna.local_votacao == int(codigo))

    def _join(self, query):
        return query.select_from(TseSecao).join(TseBuControle, TseBuControle.secao_id == TseSecao.id).join(
            TseBoletimUrna, TseBoletimUrna.id == TseBuControle.boletim_id).join(
            TseBuCargo, TseBuCargo.boletim_id == TseBoletimUrna.id)

    def _totais(self, eleicao, abr, codigo):
        from .analytics import _dt
        fields = ('eleitores_aptos', 'comparecimento', 'votos_nominais', 'votos_legenda', 'votos_brancos', 'votos_nulos')
        q = self._join(select(TseBuCargo.cargo_id, func.count(),
            *[func.sum(getattr(TseBuCargo, f)) for f in fields],
            func.max(TseBoletimUrna.recebido_em), func.max(TseBoletimUrna.processado_em))).where(
                *self._filtros(eleicao, abr, codigo), TseBuCargo.eleicao_id == eleicao.id).group_by(TseBuCargo.cargo_id)
        totals = {}
        for cargo_id, bus, aptos, comp, nom, leg, branco, nulo, recebido, processado in self.session.execute(q):
            totals[cargo_id] = {'idg': None, 'gerado_em': _dt(recebido),
                'ultima_totalizacao': _dt(recebido), 'capturado_em': _dt(processado),
                'andamento': None, 'totalizacao_final': False, 'secoes_total': None,
                'secoes_totalizadas': None, 'percentual_secoes': None, 'eleitores': int(aptos),
                'comparecimento': int(comp), 'abstencoes': int(aptos-comp),
                'votos_validos': int(nom+leg), 'votos_nominais': int(nom), 'votos_legenda': int(leg),
                'votos_brancos': int(branco), 'votos_nulos': int(nulo), 'votos_anulados_sub_judice': None,
                'bus': bus}
        return totals

    def _cabecalho(self, eleicao, abr, local, total):
        from .analytics import _abrangencia, _eleicao, _pct
        available = total['bus'] if total else 0
        return {'eleicao': _eleicao(eleicao), 'origem': eleicao.origem,
            'abrangencia': {**_abrangencia(abr), 'tipo': 'LOCAL_VOTACAO', 'local_votacao': local['codigo']},
            'fonte': FONTE_LOCAL, 'local': {**local, 'municipio_codigo': abr.municipio_codigo,
                'zona': abr.zona, 'bus': available}, 'totalizacao': total,
            'cobertura': {'partes': local['principais'], 'com_resultado': available,
                'percentual': _pct(available, local['principais']), 'completa': available == local['principais']}}

    def _linhas(self, eleicao, cargo, abr, codigo, validos):
        from .analytics import _pct
        # Group actual independent urns in SQL. No query per section/candidate/local.
        aggregate = self._join(select(TseBuVoto.candidato_id.label('cand'),
            TseBuVoto.partido_id.label('par'), TseBuVoto.partido_numero.label('partido'),
            TseBuVoto.numero.label('numero'), func.sum(TseBuVoto.votos).label('votos'))).join(
                TseBuVoto, TseBuVoto.bu_cargo_id == TseBuCargo.id).where(
            *self._filtros(eleicao, abr, codigo), TseBuCargo.eleicao_id == eleicao.id,
            TseBuCargo.cargo_id == cargo.id, TseBuVoto.tipo == 'NOMINAL').group_by(
                TseBuVoto.candidato_id, TseBuVoto.partido_id, TseBuVoto.partido_numero, TseBuVoto.numero).subquery()
        rows = self.session.execute(select(aggregate, TseCandidato, TsePartido, TseFederacao)
            .outerjoin(TseCandidato, TseCandidato.id == aggregate.c.cand)
            .outerjoin(TsePartido, TsePartido.id == aggregate.c.par)
            .outerjoin(TseFederacao, TseFederacao.id == TsePartido.federacao_id)
            .order_by(aggregate.c.votos.desc(), TseCandidato.nome_urna, aggregate.c.numero))
        return [{'posicao': i, 'sqcand': cand.sqcand if cand else None, 'numero': numero,
            'nome': cand.nome if cand else None,
            'nome_urna': cand.nome_urna if cand else f'Nº {numero} (fora da lista de candidatos)',
            'partido': {'numero': partido, 'sigla': par.sigla if par else None, 'nome': par.nome if par else None},
            'federacao': {'numero': fed.numero, 'sigla': fed.sigla, 'nome': fed.nome} if fed else None,
            'votos': int(votos), 'percentual': _pct(votos, validos), 'situacao': None,
            'eleito': None, 'destinacao_voto': None, 'voto_valido': True}
            for i, (_cid, _pid, partido, numero, votos, cand, par, fed) in enumerate(rows, 1)]

    def resultado(self, eleicao, cargo, uf, municipio, zona, codigo, limite=None):
        abr, local, _secoes = self.contexto(eleicao, uf, municipio, zona, codigo)
        total = self._totais(eleicao, abr, codigo).get(cargo.id)
        linhas = self._linhas(eleicao, cargo, abr, codigo, total['votos_validos']) if total else []
        return {**self._cabecalho(eleicao, abr, local, total),
            'cargo': {'codigo': cargo.codigo, 'nome': cargo.nome, 'vagas': None},
            'total_candidatos': len(linhas), 'candidatos': linhas[:limite] if limite else linhas}

    def resumo(self, eleicao, uf, municipio, zona, codigo):
        abr, local, _secoes = self.contexto(eleicao, uf, municipio, zona, codigo)
        totals = self._totais(eleicao, abr, codigo)
        cargos = [{'codigo': c.codigo, 'nome': c.nome, 'vagas': None, 'totalizacao': totals[c.id]}
            for c in self.session.scalars(select(TseCargo).where(TseCargo.id.in_(totals)).order_by(TseCargo.codigo))]
        total = max(totals.values(), key=lambda t: t['gerado_em'] or '', default=None)
        return {**self._cabecalho(eleicao, abr, local, total), 'uf': abr.uf, 'cargos': cargos,
            'ultima_atualizacao': total['gerado_em'] if total else None}

    def nominatas(self, eleicao, cargo, uf, municipio, zona, codigo):
        result = self.resultado(eleicao, cargo, uf, municipio, zona, codigo)
        abr = self.base._abrangencia(eleicao, uf, municipio, zona)
        grupos = {}
        def grupo(par, fed):
            entity = fed or par
            key = ('FEDERACAO' if fed else 'PARTIDO', entity['numero'])
            item = grupos.setdefault(key, {'tipo': key[0], 'numero': key[1],
                'nome': entity['nome'], 'sigla': entity['sigla'], 'partidos': {},
                'candidatos': [], 'votos_legenda': 0})
            item['partidos'][par['numero']] = par
            return item
        for linha in result['candidatos']:
            grupo(linha['partido'], linha['federacao'])['candidatos'].append(linha)
        q = self._join(select(TseBuVoto.partido_numero, func.sum(TseBuVoto.votos).label('votos'),
            TsePartido, TseFederacao)).join(TseBuVoto, TseBuVoto.bu_cargo_id == TseBuCargo.id).outerjoin(
            TsePartido, TsePartido.id == TseBuVoto.partido_id).outerjoin(
            TseFederacao, TseFederacao.id == TsePartido.federacao_id).where(
                *self._filtros(eleicao, abr, codigo), TseBuCargo.eleicao_id == eleicao.id,
                TseBuCargo.cargo_id == cargo.id, TseBuVoto.tipo == 'LEGENDA').group_by(
                    TseBuVoto.partido_numero, *TsePartido.__table__.c, *TseFederacao.__table__.c)
        for numero, votos, par, fed in self.session.execute(q):
            p = {'numero': numero, 'sigla': par.sigla if par else None, 'nome': par.nome if par else None}
            f = {'numero': fed.numero, 'sigla': fed.sigla, 'nome': fed.nome} if fed else None
            grupo(p, f)['votos_legenda'] += int(votos)
        nominatas = []
        for item in grupos.values():
            rows = [{**l, 'posicao_geral': l['posicao'], 'posicao': i} for i, l in enumerate(item['candidatos'], 1)]
            nom = sum(l['votos'] for l in rows)
            nominatas.append({**item, 'partidos': sorted(item['partidos'].values(), key=lambda p: p['numero']),
                'candidatos': rows, 'votos_nominais': nom, 'votos_nominais_validos': nom,
                'total': nom + item['votos_legenda']})
        nominatas.sort(key=lambda n: (-n['total'], n['sigla'] or n['numero']))
        result.pop('candidatos');result.pop('total_candidatos')
        return {**result, 'nominatas': nominatas}

    def distribuicao(self, eleicao, cargo, uf, municipio, zona, codigo, cands, grupos,
                     descrever_candidato, descrever_grupo, oficial):
        from .analytics import _abrangencia, _eleicao, _pct, _soma
        abr = self.base._abrangencia(eleicao, uf, municipio, zona)
        secoes = self.base.secoes.opcoes(eleicao, abr)
        locais = self.listar(eleicao, abr, secoes)
        by_section = {s['secao']: s['local_votacao'] for s in secoes if s['principal']}
        if codigo:
            _abr, local, _selected = self.contexto(eleicao, uf, municipio, zona, codigo)
        keys = [c.id for c in cands] + list(grupos)
        selected_totals = {}
        if codigo:
            urnas = self.base.secoes.partes_da_zona(eleicao, cargo, abr, [c.id for c in cands],
                {key: g['partido_ids'] for key, g in grupos.items()})
        else:
            # Reuse the existing batched section query and official zone denominators once.
            selected = self.base.distribuicao(eleicao.id, cargo.codigo, uf,
                [c.sqcand for c in cands], [g['partido'].numero for g in grupos.values() if g['partido']],
                [g['fed'].numero for g in grupos.values() if g['fed']], municipio, zona, eleicao.origem)
            selected_totals = {i['id']: i for i in selected['itens']}
            urnas = {'partes': selected['partes'], 'votos': {}, 'validos': {}}
            for key, item in zip(keys, selected['itens']):
                for p in item['partes']:
                    if p['votos'] is not None:
                        urnas['votos'][(p['codigo'],key)] = p['votos']
            urnas['validos'] = {p['codigo']:p['totalizacao']['votos_validos']
                for p in selected['partes'] if p['totalizacao']}
        known = [p for p in urnas['partes'] if by_section.get(p['codigo']) and
            (not codigo or by_section[p['codigo']] == codigo)]
        parts, votes, validos = [], {}, {}
        if codigo:
            parts = known
            votes, validos = urnas['votos'], urnas['validos']
            total = self._totais(eleicao, abr, codigo).get(cargo.id)
            header = self._cabecalho(eleicao, abr, local, total)
        else:
            for local in locais:
                member = [p for p in known if by_section[p['codigo']] == local['codigo']]
                loaded = [p for p in member if p['totalizacao'] is not None]
                # Local has an explicit coverage count; totals sum only published BU cargos.
                total = None
                if loaded:
                    total = dict(loaded[0]['totalizacao'])
                    for field in ('eleitores','comparecimento','abstencoes','votos_validos','votos_nominais','votos_legenda','votos_brancos','votos_nulos'):
                        total[field] = sum(p['totalizacao'][field] for p in loaded)
                    for field in ('gerado_em','ultima_totalizacao','capturado_em'):
                        total[field] = max((p['totalizacao'][field] for p in loaded if p['totalizacao'][field]), default=None)
                    validos[local['codigo']] = total['votos_validos']
                    for key in keys:
                        votes[(local['codigo'], key)] = sum(urnas['votos'][(p['codigo'], key)] for p in loaded)
                parts.append({'codigo': local['codigo'], 'nome': local['nome'] or f"Local {local['codigo']}",
                    'local_votacao': local['codigo'], 'local': local, 'municipio_codigo': municipio,
                    'zona': zona, 'secao': None, 'totalizacao': total, 'fonte': FONTE_LOCAL,
                    'cobertura': {'partes': len(member), 'com_resultado': len(loaded),
                        'completa': len(member) == len(loaded)}})
            header = {'eleicao': _eleicao(eleicao), 'origem': eleicao.origem,
                'abrangencia': _abrangencia(abr), 'totalizacao': oficial, 'fonte': 'EA20'}
        descriptions = [descrever_candidato(c) for c in cands] + [descrever_grupo(k,g) for k,g in grupos.items()]
        # Upper zone totals stay EA20; a selected local's denominator is its own BU aggregate.
        items = []
        for desc, key in zip(descriptions, keys):
            soma = _soma(votes.get((p['codigo'], key)) for p in parts)
            upper = selected_totals.get(desc['id'])
            total_votes = upper['total_votos'] if upper else soma
            item_parts = [{'codigo': p['codigo'], 'votos': votes.get((p['codigo'], key)),
                'percentual_item': _pct(votes[(p['codigo'],key)], total_votes) if (p['codigo'],key) in votes else None,
                'percentual_parte': _pct(votes[(p['codigo'],key)], validos[p['codigo']]) if (p['codigo'],key) in votes else None} for p in parts]
            items.append({**desc, 'total_votos': total_votes, 'soma_das_partes': soma,
                'percentual': upper['percentual'] if upper else _pct(total_votes, header['totalizacao']['votos_validos']) if header['totalizacao'] else None,
                'posicao': upper['posicao'] if upper else None, 'partes': item_parts})
        return {**header, 'cargo': {'codigo': cargo.codigo, 'nome': cargo.nome, 'vagas': None},
            'nivel': 'secoes' if codigo else 'locais_votacao', 'partes': parts, 'itens': items,
            'partes_podem_divergir': not bool(codigo), 'secao_disponivel': True,
            'secoes_sem_local': sum(s['local_votacao'] is None for s in secoes),
            'cobertura': {'partes': len(known), 'com_resultado': sum(p['totalizacao'] is not None for p in known)}}
