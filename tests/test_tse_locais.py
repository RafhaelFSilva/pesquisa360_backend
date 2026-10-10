"""Local analytics over isolated synthetic BU fixtures; no DEV/QA writes."""
from sqlalchemy import event
from tests.test_tse_bu import _SecaoApiFixture, SQ_5801, AGREGADAS_DA_0055, bu_da_secao, votos_padrao


class LocalApiTest(_SecaoApiFixture):
    def setUp(self):
        super().setUp()
        for secao, votos, local in (('0055',100,1),('0056',20,1),('0057',50,2)):
            self.publicar(secao, bu_da_secao(secao, votos_padrao(n5801=votos), local=local))
        self.lote()

    def test_discovery_official_code_and_aggregates_group_local(self):
        d = self.ok('territorio')
        self.assertEqual([l['codigo'] for l in d['locais']], ['0001','0002'])
        self.assertTrue(all(l['nome'] is None and l['endereco'] is None for l in d['locais']))
        self.assertEqual(d['locais'][0]['secoes'], 2+len(AGREGADAS_DA_0055))
        only = self.ok('territorio',local_votacao='0002')
        self.assertEqual([s['secao'] for s in only['itens']], ['0057'])

    def test_local_sum_ranking_and_no_aggregate_duplication(self):
        d = self.ok('cargos/0006',local_votacao='0001')
        a = next(c for c in d['candidatos'] if c['sqcand']==SQ_5801)
        self.assertEqual((d['fonte'],d['abrangencia']['tipo']), ('BU_AGREGADO_LOCAL','LOCAL_VOTACAO'))
        self.assertEqual(a['votos'],120)
        self.assertEqual(d['totalizacao']['votos_nominais'],200)
        self.assertEqual(d['totalizacao']['votos_legenda'],20)
        self.assertEqual(a['percentual'],round(12000/220,2))
        self.assertEqual(d['cobertura']['com_resultado'],2)
        self.assertIsNone(d['totalizacao']['idg'])
        one=self.ok('cargos/0006',local_votacao='0002')
        self.assertEqual(one['cobertura']['partes'],1)

    def test_nominata_uses_official_legend_votes(self):
        d=self.ok('cargos/0006/nominatas',local_votacao='0001')
        n=next(n for n in d['nominatas'] if n['numero']=='58')
        self.assertEqual((n['votos_nominais'],n['votos_legenda'],n['total']),(200,14,214))

    def test_summary_local_and_original_section(self):
        d=self.ok('resumo',local_votacao='0001')
        self.assertEqual(d['fonte'],'BU_AGREGADO_LOCAL')
        self.assertEqual(d['cargos'][0]['totalizacao']['votos_validos'],220)
        s=self.ok('resumo',local_votacao='0001',secao='0055')
        self.assertEqual(s['fonte'],'BU')

    def test_local_not_found_wrong_zone_or_wrong_section(self):
        for args in ({'local_votacao':'9999'}, {'local_votacao':'0001','zona':'9999'},
                     {'local_votacao':'0002','secao':'0055'}):
            for endpoint in ('resumo','territorio','cargos/0006','cargos/0006/nominatas'):
                if endpoint=='territorio' and args.get('secao'): continue
                self.assertEqual(self.get(endpoint,**args).status_code,404)
        self.assertEqual(self.get('resumo',local_votacao='0001',zona=None).status_code,422)
        self.assertEqual(self.get('resumo',local_votacao='x').status_code,422)

    def test_distribution_local_and_section_percentages_and_multiple_items(self):
        d=self.ok('cargos/0006/distribuicao',nivel='locais_votacao',candidato=SQ_5801,partido='58')
        self.assertEqual(d['nivel'],'locais_votacao')
        self.assertEqual([p['codigo'] for p in d['partes']],['0001','0002'])
        self.assertEqual(d['itens'][0]['soma_das_partes'],170)
        self.assertEqual(d['itens'][1]['soma_das_partes'],311)
        self.assertEqual(d['itens'][0]['partes'][0]['percentual_parte'],round(12000/220,2))
        section=self.ok('cargos/0006/distribuicao',local_votacao='0001',candidato=SQ_5801,partido='58')
        self.assertEqual([p['codigo'] for p in section['partes']],['0055','0056'])
        self.assertEqual(section['itens'][0]['total_votos'],120)
        self.assertEqual(section['itens'][0]['partes'][0]['percentual_item'],round(10000/120,2))

    def test_query_count_constant_with_items_and_locals(self):
        def count(**args):
            calls=[]
            listener=lambda *_a,**_kw:calls.append(1)
            event.listen(self.engine,'before_cursor_execute',listener)
            try:self.ok('cargos/0006/distribuicao',nivel='locais_votacao',**args)
            finally:event.remove(self.engine,'before_cursor_execute',listener)
            return len(calls)
        self.ok('cargos/0006/distribuicao',nivel='locais_votacao',candidato=SQ_5801,partido='58')
        first=count(candidato=SQ_5801,partido='58')
        multiple=count(candidato=[SQ_5801,'41609381','41609378'],partido=['58','78'])
        self.assertEqual(first,multiple)
        self.assertLess(first,45)

    def test_read_only_and_backward_compatibility(self):
        from sqlalchemy import select, func
        from pesquisa360.db.models_tse import TseBoletimUrna,TseBuCargo,TseBuVoto
        before=[self.session.scalar(select(func.count()).select_from(m)) for m in (TseBoletimUrna,TseBuCargo,TseBuVoto)]
        old=self.ok('cargos/0006/distribuicao',candidato=SQ_5801)
        self.assertEqual(old['nivel'],'secoes')
        self.ok('cargos/0006',local_votacao='0001')
        self.ok('cargos/0006/nominatas',local_votacao='0001')
        after=[self.session.scalar(select(func.count()).select_from(m)) for m in (TseBoletimUrna,TseBuCargo,TseBuVoto)]
        self.assertEqual(before,after)

    def test_current_version_changes_local_without_overwriting_previous_bu(self):
        before=self.corrente('0056')
        old_id=before.id
        old_local=before.local_votacao
        self.assertEqual(self.lote().bu_persisted,0)  # A -> A
        self.publicar('0056',bu_da_secao('0056',votos_padrao(n5801=20),local=3))
        self.carimbar('0056','18:00:00')
        self.assertEqual(self.lote().bu_persisted,1)  # A -> B
        current=self.corrente('0056')
        self.assertNotEqual(current.id,old_id)
        self.assertEqual(current.local_votacao,3)
        from pesquisa360.db.models_tse import TseBoletimUrna
        self.assertEqual(self.session.get(TseBoletimUrna,old_id).local_votacao,old_local)
        self.assertEqual(self.ok('territorio',local_votacao='0003')['itens'][0]['secao'],'0056')

    def test_missing_bu_does_not_invent_a_local(self):
        ctl=self.controle('0057');ctl.boletim_id=None;ctl.status='AGUARDANDO'
        self.session.commit()
        d=self.ok('territorio')
        missing=next(s for s in d['itens'] if s['secao']=='0057')
        self.assertIsNone(missing['local_votacao'])
        self.assertFalse(missing['resultado_disponivel'])
        self.assertEqual(self.get('cargos/0006',local_votacao='0002').status_code,404)
