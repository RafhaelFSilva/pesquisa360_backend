"""Gestao de Liderancas: schema, multitenancy, historico e analise (Fase 6A).

O cenario analitico segue exatamente a fixture controlada do escopo:
  Bairro A 6.000 + Bairro B 4.000 = 10.000 aptos
  comparecimento 0,80 x validos 0,90 -> 7.200 votos validos projetados
  100 entrevistas validas, 50 no alvo -> taxa 50% -> 3.600 votos projetados
"""

import os
import re
import shutil
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-lideranca-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.api.endpoints import liderancas as rotas
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.db import models
from pesquisa360.services import lideranca as service
from pesquisa360.services import lideranca_analytics as analytics
from pesquisa360.services import setor_territorio

from tests.test_base_eleitoral_import import run_alembic_upgrade


class _LiderancaFixture(unittest.TestCase):
    """Tenant A (company 10) com projeto, duas ondas, setores e base eleitoral."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="pesquisa360-lideranca-"))
        cls.db_path = cls.temp_dir / "lideranca.db"
        run_alembic_upgrade(f"sqlite:///{cls.db_path.as_posix()}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def setUp(self):
        self.engine = create_engine(f"sqlite:///{self.db_path.as_posix()}")

        @event.listens_for(self.engine, "connect")
        def _preparar(dbapi_connection, _record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")
            dbapi_connection.create_function("GeomFromEWKT", 1, lambda valor: valor)
            dbapi_connection.create_function("ST_GeomFromEWKT", 1, lambda valor: valor)
            dbapi_connection.create_function("AsEWKB", 1, lambda valor: valor)
            # A classificacao territorial por setor usa AsGeoJSON; ate a Fase
            # 3B.1 nenhum teste daqui exercitava o escopo SETOR, entao a funcao
            # nunca fizera falta. SQLite nao tem PostGIS.
            dbapi_connection.create_function("AsGeoJSON", 1, lambda valor: None)
            dbapi_connection.create_function("ST_AsGeoJSON", 1, lambda valor: None)
            # Classificacao de coleta por setor le as coordenadas do ponto.
            # As coletas desta fixture nao tem localizacao, entao None mantem o
            # comportamento real: coleta sem coordenada nao entra em setor algum.
            dbapi_connection.create_function("ST_X", 1, lambda valor: None)
            dbapi_connection.create_function("ST_Y", 1, lambda valor: None)

        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)
        self._limpar()
        self._semear()

    def _limpar(self):
        for tabela in (
            "setor_territorio_eleitoral",
            "lideranca_territorio_eleitoral",
            "lideranca_pesquisa_config",
            "liderancas_politicas",
            "respostas",
            "coletas",
            "opcoes",
            "perguntas",
            "setores",
            "projeto_base_eleitoral",
            "territorio_eleitoral",
            "importacao_base_eleitoral",
            "base_eleitoral",
            "pesquisas",
            "projetos",
            "usuarios",
            "companies",
            "perfis",
        ):
            self.session.execute(text(f"DELETE FROM {tabela}"))
        self.session.commit()

    def _sql(self, comando):
        self.session.execute(text(comando))

    def _semear(self):
        self._sql(
            "INSERT INTO perfis (id, nome) VALUES (1,'Superadmin'),(2,'Gerente'),(3,'Agente')"
        )
        self._sql(
            "INSERT INTO companies (id, name, is_active) VALUES (10,'A',1),(20,'B',1)"
        )
        self._sql(
            "INSERT INTO usuarios (id,email,nome,senha_hash,ativo,perfil_id,company_id) VALUES"
            " (1,'g.a@a','Gerente A','x',1,2,10),"
            " (2,'g.b@b','Gerente B','x',1,2,20),"
            " (3,'ag.a@a','Agente A','x',1,3,10)"
        )
        self._sql(
            "INSERT INTO projetos (id,nome,status,data_inicio,coordenador_id,company_id) VALUES"
            " (100,'Campanha A','Ativo','2026-01-01',1,10),"
            " (200,'Campanha B','Ativo','2026-01-01',2,20)"
        )
        self._sql(
            "INSERT INTO pesquisas (id,titulo,ativo,projeto_id) VALUES"
            " (10,'Onda 1',1,100),(11,'Onda 2',1,100),(20,'Onda B',1,200)"
        )
        self._sql(
            "INSERT INTO setores (id,nome,meta,tolerancia,finalidade,pesquisa_id) VALUES"
            " (30,'Norte',50,50,'AMBOS',10),(31,'Sul',50,50,'AMBOS',10),"
            " (32,'Norte B',50,50,'AMBOS',11),(40,'Setor B',50,50,'AMBOS',20)"
        )
        self._sql(
            "INSERT INTO perguntas (id,texto_pergunta,tipo_pergunta,ordem,eh_obrigatoria,"
            "eh_resposta_espontanea,papel_analitico,metadados_analiticos,ativo,pesquisa_id) VALUES"
            " (42,'Governador','ESCOLHA_SIMPLES',1,1,0,NULL,'{}',1,10),"
            " (43,'Sexo','ESCOLHA_SIMPLES',2,1,0,NULL,'{}',1,10),"
            " (44,'Lembranca','TEXTO',3,0,1,NULL,'{}',1,10)"
        )
        self.session.commit()

        self.gerente_a = self.session.get(models.Usuario, 1)
        self.gerente_b = self.session.get(models.Usuario, 2)
        self.agente_a = self.session.get(models.Usuario, 3)

        self.base = self._criar_base(company_id=10)
        self.base_b = self._criar_base(company_id=20, versao="b-1")
        self.bairro_a, self.bairro_b = self._criar_territorios(self.base, 6000, 4000)
        (self.bairro_outro_tenant,) = self._criar_territorios(self.base_b, 999)[:1]
        self._vincular_base(100, self.base.id)
        self._vincular_base(200, self.base_b.id)

    def _criar_base(self, company_id, versao="v1", **overrides):
        dados = dict(
            nome="Base",
            ano=2026,
            uf="AP",
            fonte="FIXTURE",
            versao=versao,
            data_referencia=date(2026, 1, 1),
            company_id=company_id,
            criado_por_id=1,
            status="VALIDADA",
            comparecimento_estimado=Decimal("0.8000"),
            percentual_votos_validos=Decimal("0.9000"),
        )
        dados.update(overrides)
        base = models.BaseEleitoral(**dados)
        self.session.add(base)
        self.session.commit()
        return base

    def _criar_territorios(self, base, *aptos):
        estado = models.TerritorioEleitoral(
            base_eleitoral_id=base.id, tipo="ESTADO", nome="Amapa", nome_normalizado="amapa"
        )
        self.session.add(estado)
        self.session.commit()
        municipio = models.TerritorioEleitoral(
            base_eleitoral_id=base.id,
            tipo="MUNICIPIO",
            nome="Macapa",
            nome_normalizado="macapa",
            parent_id=estado.id,
        )
        self.session.add(municipio)
        self.session.commit()
        criados = []
        for indice, valor in enumerate(aptos, start=1):
            bairro = models.TerritorioEleitoral(
                base_eleitoral_id=base.id,
                tipo="BAIRRO",
                nome=f"Bairro {chr(64 + indice)}",
                nome_normalizado=f"bairro {chr(96 + indice)}",
                parent_id=municipio.id,
                municipio_id=municipio.id,
                eleitorado_apto=valor,
            )
            self.session.add(bairro)
            criados.append(bairro)
        self.session.commit()
        self.municipio = municipio
        return criados

    def _vincular_base(self, projeto_id, base_id):
        self.session.add(
            models.ProjetoBaseEleitoral(
                projeto_id=projeto_id, base_eleitoral_id=base_id, principal=True
            )
        )
        self.session.commit()

    def _criar_lideranca(self, nome="Joao", projeto_id=100):
        lideranca = models.LiderancaPolitica(projeto_id=projeto_id, nome=nome)
        self.session.add(lideranca)
        self.session.commit()
        return lideranca

    def _semear_coletas(self, *, total=100, alvo=50, setor_id=None, sexo_f=0, alvo_em_f=0):
        """Cria coletas com resposta alvo e, opcionalmente, recorte por sexo."""
        for indice in range(total):
            coleta = models.Coleta(
                pesquisa_id=10,
                agente_id=1,
                company_id=10,
                client_uuid=f"uuid-{setor_id or 0}-{indice}",
                data_inicio_coleta=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
                status_sincronizacao="sincronizado",
            )
            self.session.add(coleta)
            self.session.flush()
            eh_alvo = indice < alvo
            self.session.add(
                models.Resposta(
                    pergunta_id=42,
                    coleta_id=coleta.id,
                    valor_resposta="Candidato X" if eh_alvo else "Candidato Y",
                )
            )
            if sexo_f:
                # Distribuicao explicita para o recorte do escopo:
                #   F no alvo      -> primeiros `alvo_em_f` do bloco alvo
                #   F fora do alvo -> completa ate `sexo_f` no bloco nao-alvo
                if eh_alvo:
                    sexo = "F" if indice < alvo_em_f else "M"
                else:
                    fora_do_alvo = indice - alvo
                    sexo = "F" if fora_do_alvo < (sexo_f - alvo_em_f) else "M"
                self.session.add(
                    models.Resposta(pergunta_id=43, coleta_id=coleta.id, valor_resposta=sexo)
                )
        self.session.commit()

    def _cliente(self, usuario):
        app = FastAPI()
        app.include_router(rotas.router)

        def _db():
            yield self.session

        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user] = lambda: usuario
        return TestClient(app)

    def _analisar(self, **overrides):
        parametros = dict(
            projeto_id=100,
            pesquisa_id=10,
            pergunta_alvo_id=42,
            alvo_valores=["Candidato X"],
            filtros_respostas=None,
            lideranca_ids=None,
            current_user=self.gerente_a,
        )
        parametros.update(overrides)
        return analytics.analisar_liderancas(self.session, **parametros)


class SchemaTests(_LiderancaFixture):
    def test_lideranca_e_do_projeto_sem_company_id_nem_pesquisa(self):
        colunas = set(models.LiderancaPolitica.__table__.columns.keys())
        self.assertIn("projeto_id", colunas)
        self.assertNotIn("company_id", colunas)
        self.assertNotIn("pesquisa_id", colunas)
        self.assertNotIn("setor_id", colunas)
        self.assertNotIn("cota_votos_validos", colunas)

    def test_config_unica_por_lideranca_e_pesquisa(self):
        lideranca = self._criar_lideranca()
        self.session.add(
            models.LiderancaPesquisaConfig(lideranca_id=lideranca.id, pesquisa_id=10)
        )
        self.session.commit()
        with self.assertRaises(IntegrityError):
            self.session.add(
                models.LiderancaPesquisaConfig(lideranca_id=lideranca.id, pesquisa_id=10)
            )
            self.session.commit()
        self.session.rollback()

    def test_cota_negativa_e_rejeitada_pelo_banco(self):
        lideranca = self._criar_lideranca()
        with self.assertRaises(IntegrityError):
            self.session.add(
                models.LiderancaPesquisaConfig(
                    lideranca_id=lideranca.id, pesquisa_id=10, cota_votos_validos=-1
                )
            )
            self.session.commit()
        self.session.rollback()

    def test_cota_nula_e_zero_sao_distintas(self):
        lideranca = self._criar_lideranca()
        service.definir_config_pesquisa(
            self.session, 100, lideranca.id, 10, self.gerente_a, cota_votos_validos=None
        )
        config = service.listar_configs(self.session, lideranca.id, 10)[0]
        self.assertIsNone(config.cota_votos_validos)
        service.definir_config_pesquisa(
            self.session, 100, lideranca.id, 10, self.gerente_a, cota_votos_validos=0
        )
        config = service.listar_configs(self.session, lideranca.id, 10)[0]
        self.assertEqual(config.cota_votos_validos, 0)

    def test_varias_liderancas_no_mesmo_setor(self):
        primeira = self._criar_lideranca("Joao")
        segunda = self._criar_lideranca("Maria")
        for lideranca in (primeira, segunda):
            service.definir_config_pesquisa(
                self.session, 100, lideranca.id, 10, self.gerente_a, setor_id=30
            )
        total = (
            self.session.query(models.LiderancaPesquisaConfig)
            .filter(models.LiderancaPesquisaConfig.setor_id == 30)
            .count()
        )
        self.assertEqual(total, 2)

    def test_territorios_sao_n_para_n_sem_duplicar(self):
        lideranca = self._criar_lideranca()
        service.definir_territorios(
            self.session, 100, lideranca.id, [self.bairro_a.id, self.bairro_b.id], self.gerente_a
        )
        self.assertEqual(len(service.listar_territorios(self.session, lideranca.id)), 2)
        with self.assertRaises(IntegrityError):
            self.session.add(
                models.LiderancaTerritorioEleitoral(
                    lideranca_id=lideranca.id, territorio_eleitoral_id=self.bairro_a.id
                )
            )
            self.session.commit()
        self.session.rollback()

    def test_soft_delete_preserva_config_e_territorios(self):
        lideranca = self._criar_lideranca()
        service.definir_config_pesquisa(
            self.session, 100, lideranca.id, 10, self.gerente_a, cota_votos_validos=2000
        )
        service.definir_territorios(
            self.session, 100, lideranca.id, [self.bairro_a.id], self.gerente_a
        )
        service.desativar_lideranca(self.session, 100, lideranca.id, self.gerente_a)
        self.session.expire_all()
        recarregada = self.session.get(models.LiderancaPolitica, lideranca.id)
        self.assertFalse(recarregada.ativo)
        self.assertIsNotNone(recarregada)
        self.assertEqual(len(service.listar_configs(self.session, lideranca.id)), 1)
        self.assertEqual(len(service.listar_territorios(self.session, lideranca.id)), 1)

    def test_setor_removido_nao_apaga_a_configuracao(self):
        # Setor tem delete fisico; SET NULL preserva o historico da lideranca.
        lideranca = self._criar_lideranca()
        service.definir_config_pesquisa(
            self.session, 100, lideranca.id, 10, self.gerente_a, setor_id=31, cota_votos_validos=900
        )
        self.session.execute(text("DELETE FROM setores WHERE id = 31"))
        self.session.commit()
        self.session.expire_all()
        config = service.listar_configs(self.session, lideranca.id, 10)[0]
        self.assertIsNone(config.setor_id)
        self.assertEqual(config.cota_votos_validos, 900)


class MultitenancyTests(_LiderancaFixture):
    def test_projeto_de_outro_tenant_retorna_404(self):
        with self.assertRaises(HTTPException) as contexto:
            service.listar_liderancas(self.session, 200, self.gerente_a)
        self.assertEqual(contexto.exception.status_code, 404)

    def test_lideranca_de_outro_tenant_retorna_404(self):
        lideranca = self._criar_lideranca(projeto_id=200)
        with self.assertRaises(HTTPException) as contexto:
            service.obter_lideranca(self.session, 200, lideranca.id, self.gerente_a)
        self.assertEqual(contexto.exception.status_code, 404)
        self.assertNotEqual(contexto.exception.status_code, 403)

    def test_pesquisa_de_outro_projeto_e_rejeitada(self):
        lideranca = self._criar_lideranca()
        with self.assertRaises(HTTPException) as contexto:
            service.definir_config_pesquisa(
                self.session, 100, lideranca.id, 20, self.gerente_a
            )
        self.assertEqual(contexto.exception.status_code, 404)

    def test_setor_de_outra_pesquisa_e_rejeitado(self):
        lideranca = self._criar_lideranca()
        with self.assertRaises(HTTPException) as contexto:
            # Setor 32 pertence a Onda 2, nao a Onda 1.
            service.definir_config_pesquisa(
                self.session, 100, lideranca.id, 10, self.gerente_a, setor_id=32
            )
        self.assertEqual(contexto.exception.status_code, 404)

    def test_bairro_de_outra_base_e_rejeitado(self):
        lideranca = self._criar_lideranca()
        with self.assertRaises(HTTPException) as contexto:
            service.definir_territorios(
                self.session, 100, lideranca.id, [self.bairro_outro_tenant.id], self.gerente_a
            )
        self.assertEqual(contexto.exception.status_code, 404)
        self.assertEqual(len(service.listar_territorios(self.session, lideranca.id)), 0)

    def test_lote_invalido_faz_rollback_completo(self):
        lideranca = self._criar_lideranca()
        service.definir_territorios(
            self.session, 100, lideranca.id, [self.bairro_a.id], self.gerente_a
        )
        with self.assertRaises(HTTPException):
            service.definir_territorios(
                self.session,
                100,
                lideranca.id,
                [self.bairro_b.id, self.bairro_outro_tenant.id],
                self.gerente_a,
            )
        # Conjunto anterior permanece intacto.
        atuais = service.listar_territorios(self.session, lideranca.id)
        self.assertEqual([t.id for t in atuais], [self.bairro_a.id])

    def test_nivel_diferente_de_bairro_e_rejeitado(self):
        lideranca = self._criar_lideranca()
        with self.assertRaises(HTTPException):
            service.definir_territorios(
                self.session, 100, lideranca.id, [self.municipio.id], self.gerente_a
            )

    def test_agente_nao_escreve(self):
        lideranca = self._criar_lideranca()
        for acao in (
            lambda: service.criar_lideranca(self.session, 100, "X", self.agente_a),
            lambda: service.definir_config_pesquisa(
                self.session, 100, lideranca.id, 10, self.agente_a, cota_votos_validos=10
            ),
            lambda: service.definir_territorios(
                self.session, 100, lideranca.id, [self.bairro_a.id], self.agente_a
            ),
            lambda: service.desativar_lideranca(self.session, 100, lideranca.id, self.agente_a),
        ):
            with self.subTest(acao=acao):
                with self.assertRaises(HTTPException) as contexto:
                    acao()
                self.assertEqual(contexto.exception.status_code, 403)


class HistoricoTests(_LiderancaFixture):
    def test_configuracoes_por_onda_sao_independentes(self):
        lideranca = self._criar_lideranca()
        service.definir_config_pesquisa(
            self.session, 100, lideranca.id, 10, self.gerente_a, setor_id=30, cota_votos_validos=1000
        )
        service.definir_config_pesquisa(
            self.session, 100, lideranca.id, 11, self.gerente_a, setor_id=32, cota_votos_validos=1200
        )
        configs = {c.pesquisa_id: c for c in service.listar_configs(self.session, lideranca.id)}
        self.assertEqual(len(configs), 2)
        self.assertEqual((configs[10].setor_id, configs[10].cota_votos_validos), (30, 1000))
        self.assertEqual((configs[11].setor_id, configs[11].cota_votos_validos), (32, 1200))

        # Alterar a Onda 2 nao toca a Onda 1.
        service.definir_config_pesquisa(
            self.session, 100, lideranca.id, 11, self.gerente_a, setor_id=32, cota_votos_validos=1500
        )
        configs = {c.pesquisa_id: c for c in service.listar_configs(self.session, lideranca.id)}
        self.assertEqual(configs[10].cota_votos_validos, 1000)
        self.assertEqual(configs[11].cota_votos_validos, 1500)


class AnaliseTests(_LiderancaFixture):
    def _cenario(self, cota=3000, setor_id=30, territorios=True, total=100, alvo=50, **kwargs):
        lideranca = self._criar_lideranca()
        service.definir_config_pesquisa(
            self.session, 100, lideranca.id, 10, self.gerente_a,
            setor_id=setor_id, cota_votos_validos=cota,
        )
        if territorios:
            service.definir_territorios(
                self.session, 100, lideranca.id,
                [self.bairro_a.id, self.bairro_b.id], self.gerente_a,
            )
        self._semear_coletas(total=total, alvo=alvo, **kwargs)
        return lideranca

    def _item(self, resultado, lideranca):
        return next(item for item in resultado["liderancas"] if item["id"] == lideranca.id)

    def test_plus(self):
        # 10.000 aptos x 0,80 x 0,90 = 7.200; taxa 50% -> 3.600; cota 3.000 -> +600
        lideranca = self._cenario(cota=3000, setor_id=None)
        item = self._item(self._analisar(), lideranca)
        self.assertEqual(item["universo_eleitoral"]["eleitorado_apto"], 10000)
        self.assertEqual(item["universo_eleitoral"]["votos_validos_projetados"], 7200)
        self.assertEqual(item["resultado_principal"]["taxa_alvo"], 0.5)
        self.assertEqual(item["resultado_principal"]["votos_projetados_alvo"], 3600)
        self.assertEqual(item["resultado_principal"]["gap_plus"], 600)
        self.assertEqual(item["resultado_principal"]["status"], "PLUS")
        self.assertEqual(item["resultado_principal"]["atingimento_percentual"], 120.0)
        self.assertIsNone(item["indisponibilidade"])

    def test_gap(self):
        lideranca = self._cenario(cota=4000, setor_id=None)
        item = self._item(self._analisar(), lideranca)
        self.assertEqual(item["resultado_principal"]["votos_projetados_alvo"], 3600)
        self.assertEqual(item["resultado_principal"]["gap_plus"], -400)
        self.assertEqual(item["resultado_principal"]["status"], "GAP")

    def test_meta_atingida(self):
        lideranca = self._cenario(cota=3600, setor_id=None)
        item = self._item(self._analisar(), lideranca)
        self.assertEqual(item["resultado_principal"]["gap_plus"], 0)
        self.assertEqual(item["resultado_principal"]["status"], "META_ATINGIDA")
        self.assertEqual(item["resultado_principal"]["atingimento_percentual"], 100.0)

    def test_sem_parametros_eleitorais_nao_assume_cem_por_cento(self):
        self.base.comparecimento_estimado = None
        self.session.add(self.base)
        self.session.commit()
        lideranca = self._cenario(setor_id=None)
        item = self._item(self._analisar(), lideranca)
        self.assertIsNone(item["universo_eleitoral"]["votos_validos_projetados"])
        self.assertIsNone(item["resultado_principal"]["votos_projetados_alvo"])
        self.assertIsNone(item["resultado_principal"]["gap_plus"])
        self.assertEqual(item["indisponibilidade"], "PARAMETROS_ELEITORAIS_AUSENTES")
        # A taxa amostral continua disponivel.
        self.assertEqual(item["resultado_principal"]["taxa_alvo"], 0.5)

    def test_sem_territorio_eleitoral(self):
        lideranca = self._cenario(territorios=False, setor_id=None)
        item = self._item(self._analisar(), lideranca)
        self.assertIsNone(item["universo_eleitoral"]["eleitorado_apto"])
        self.assertIsNone(item["resultado_principal"]["votos_projetados_alvo"])
        self.assertIsNone(item["resultado_principal"]["gap_plus"])
        self.assertEqual(item["indisponibilidade"], "SEM_TERRITORIO_ELEITORAL")

    def test_sem_cota(self):
        lideranca = self._cenario(cota=None, setor_id=None)
        item = self._item(self._analisar(), lideranca)
        self.assertIsNone(item["resultado_principal"]["gap_plus"])
        self.assertEqual(item["indisponibilidade"], "SEM_COTA")

    def test_base_nao_validada(self):
        self.base.status = "EM_CONFERENCIA"
        self.session.add(self.base)
        self.session.commit()
        lideranca = self._cenario(setor_id=None)
        item = self._item(self._analisar(), lideranca)
        self.assertEqual(item["indisponibilidade"], "BASE_ELEITORAL_NAO_VALIDADA")
        self.assertIsNone(item["resultado_principal"]["gap_plus"])

    def test_escopo_amostral_pesquisa_quando_nao_ha_setor(self):
        lideranca = self._cenario(setor_id=None)
        item = self._item(self._analisar(), lideranca)
        self.assertEqual(item["resultado_principal"]["escopo_amostral"], "PESQUISA")
        self.assertEqual(item["resultado_principal"]["total_entrevistas"], 100)
        self.assertIsNone(item["setor"])

    def test_sem_respostas_validas(self):
        lideranca = self._cenario(setor_id=None, total=0, alvo=0)
        item = self._item(self._analisar(), lideranca)
        self.assertEqual(item["resultado_principal"]["base_valida"], 0)
        self.assertIsNone(item["resultado_principal"]["taxa_alvo"])
        self.assertEqual(item["indisponibilidade"], "SEM_RESPOSTAS_VALIDAS")

    def test_pergunta_alvo_de_outra_pesquisa_e_rejeitada(self):
        self._cenario(setor_id=None)
        with self.assertRaises(HTTPException) as contexto:
            self._analisar(pergunta_alvo_id=999999)
        self.assertEqual(contexto.exception.status_code, 404)


class FiltrosTests(_LiderancaFixture):
    def _cenario_com_sexo(self):
        lideranca = self._criar_lideranca()
        service.definir_config_pesquisa(
            self.session, 100, lideranca.id, 10, self.gerente_a, cota_votos_validos=3000
        )
        service.definir_territorios(
            self.session, 100, lideranca.id, [self.bairro_a.id, self.bairro_b.id], self.gerente_a
        )
        # 100 entrevistas, 50 no alvo. 60 femininas, das quais 36 no alvo.
        self._semear_coletas(total=100, alvo=50, sexo_f=60, alvo_em_f=36)
        return lideranca

    def test_recorte_filtrado_tem_taxa_propria(self):
        lideranca = self._cenario_com_sexo()
        resultado = self._analisar(
            filtros_respostas=[{"pergunta_id": 43, "valores": ["F"]}]
        )
        item = next(i for i in resultado["liderancas"] if i["id"] == lideranca.id)
        self.assertEqual(item["resultado_principal"]["taxa_alvo"], 0.5)
        recorte = item["recorte_filtrado"]
        self.assertEqual(recorte["total_entrevistas"], 60)
        self.assertEqual(recorte["base_valida"], 60)
        self.assertEqual(recorte["respostas_alvo"], 36)
        self.assertEqual(recorte["taxa_alvo"], 0.6)

    def test_recorte_nao_devolve_votos_absolutos_nem_gap(self):
        lideranca = self._cenario_com_sexo()
        resultado = self._analisar(
            filtros_respostas=[{"pergunta_id": 43, "valores": ["F"]}]
        )
        item = next(i for i in resultado["liderancas"] if i["id"] == lideranca.id)
        recorte = item["recorte_filtrado"]
        # Sem calibracao populacional do segmento, projetar seria falso.
        for chave in ("votos_projetados_alvo", "gap_plus", "status", "atingimento_percentual"):
            self.assertNotIn(chave, recorte)

    def test_filtro_nao_reprojeta_o_resultado_principal(self):
        # Regressao da decisao metodologica: o filtro diagnostico nao pode
        # alterar votos projetados nem Gap/Plus principais.
        lideranca = self._cenario_com_sexo()
        sem_filtro = self._analisar()
        com_filtro = self._analisar(
            filtros_respostas=[{"pergunta_id": 43, "valores": ["F"]}]
        )
        a = next(i for i in sem_filtro["liderancas"] if i["id"] == lideranca.id)
        b = next(i for i in com_filtro["liderancas"] if i["id"] == lideranca.id)
        self.assertEqual(a["resultado_principal"], b["resultado_principal"])
        self.assertEqual(b["resultado_principal"]["votos_projetados_alvo"], 3600)
        self.assertEqual(b["resultado_principal"]["gap_plus"], 600)
        self.assertIsNone(a["recorte_filtrado"])
        self.assertIsNotNone(b["recorte_filtrado"])

    def test_sem_filtros_nao_ha_recorte(self):
        lideranca = self._cenario_com_sexo()
        item = next(i for i in self._analisar()["liderancas"] if i["id"] == lideranca.id)
        self.assertIsNone(item["recorte_filtrado"])


class EspontaneaTests(_LiderancaFixture):
    def test_alvo_espontaneo_usa_o_mapeamento_ativo(self):
        # Mesma normalizacao do Relatorio Simples: valores crus distintos que
        # apontam para a mesma categoria entram juntos.
        self._sql(
            "INSERT INTO categorias_resposta_espontanea (id,pesquisa_id,nome,nome_normalizado,"
            "ativo,criado_por_id,atualizado_por_id) VALUES (1,10,'Candidato X','candidato x',1,1,1)"
        )
        self._sql(
            "INSERT INTO mapeamentos_resposta_espontanea (id,pesquisa_id,categoria_id,"
            "chave_normalizada,texto_referencia,ativo,criado_por_id,atualizado_por_id) VALUES"
            " (1,10,1,'candidato x','Candidato X',1,1,1),"
            " (2,10,1,'cand x','Cand X',1,1,1)"
        )
        self.session.commit()

        lideranca = self._criar_lideranca()
        service.definir_config_pesquisa(
            self.session, 100, lideranca.id, 10, self.gerente_a, cota_votos_validos=1000
        )
        service.definir_territorios(
            self.session, 100, lideranca.id, [self.bairro_a.id], self.gerente_a
        )
        for indice, valor in enumerate(["Candidato X", "Cand X", "Outro", "Outro"]):
            coleta = models.Coleta(
                pesquisa_id=10, agente_id=1, company_id=10,
                client_uuid=f"esp-{indice}",
                data_inicio_coleta=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
                status_sincronizacao="sincronizado",
            )
            self.session.add(coleta)
            self.session.flush()
            self.session.add(
                models.Resposta(pergunta_id=44, coleta_id=coleta.id, valor_resposta=valor)
            )
        self.session.commit()

        resultado = self._analisar(pergunta_alvo_id=44, alvo_valores=["Candidato X"])
        item = next(i for i in resultado["liderancas"] if i["id"] == lideranca.id)
        # "Candidato X" e "Cand X" colapsam na mesma categoria reportavel.
        self.assertEqual(item["resultado_principal"]["respostas_alvo"], 2)
        self.assertEqual(item["resultado_principal"]["base_valida"], 4)
        self.assertEqual(item["resultado_principal"]["taxa_alvo"], 0.5)


class ApiTests(_LiderancaFixture):
    def test_crud_completo(self):
        cliente = self._cliente(self.gerente_a)
        criada = cliente.post("/projetos/100/liderancas", json={"nome": "João Silva"})
        self.assertEqual(criada.status_code, 201)
        lideranca_id = criada.json()["id"]

        listagem = cliente.get("/projetos/100/liderancas")
        self.assertEqual(len(listagem.json()), 1)

        editada = cliente.patch(
            f"/projetos/100/liderancas/{lideranca_id}", json={"nome": "João S."}
        )
        self.assertEqual(editada.json()["nome"], "João S.")

        removida = cliente.delete(f"/projetos/100/liderancas/{lideranca_id}")
        self.assertEqual(removida.status_code, 200)
        self.assertFalse(removida.json()["ativo"])
        # Soft delete: some da listagem padrao, mas continua no banco.
        self.assertEqual(len(cliente.get("/projetos/100/liderancas").json()), 0)
        self.assertEqual(
            len(cliente.get("/projetos/100/liderancas?incluir_inativas=true").json()), 1
        )

    def test_config_e_territorios_via_api(self):
        cliente = self._cliente(self.gerente_a)
        lideranca_id = cliente.post("/projetos/100/liderancas", json={"nome": "Maria"}).json()["id"]

        config = cliente.put(
            f"/projetos/100/liderancas/{lideranca_id}/pesquisas/10/config",
            json={"setor_id": 30, "cota_votos_validos": 2000},
        )
        self.assertEqual(config.status_code, 200)
        self.assertEqual(config.json()["cota_votos_validos"], 2000)

        territorios = cliente.put(
            f"/projetos/100/liderancas/{lideranca_id}/territorios",
            json={"territorio_ids": [self.bairro_a.id, self.bairro_b.id]},
        )
        self.assertEqual(territorios.status_code, 200)
        self.assertEqual(len(territorios.json()), 2)

    def test_requests_rejeitam_company_id(self):
        cliente = self._cliente(self.gerente_a)
        respostas = [
            cliente.post("/projetos/100/liderancas", json={"nome": "X", "company_id": 20}),
            cliente.put(
                "/projetos/100/liderancas/1/pesquisas/10/config",
                json={"cota_votos_validos": 1, "company_id": 20},
            ),
            cliente.put(
                "/projetos/100/liderancas/1/territorios",
                json={"territorio_ids": [], "company_id": 20},
            ),
        ]
        for resposta in respostas:
            with self.subTest(url=resposta.request.url):
                self.assertEqual(resposta.status_code, 422)

    def test_analise_via_api(self):
        cliente = self._cliente(self.gerente_a)
        lideranca_id = cliente.post("/projetos/100/liderancas", json={"nome": "João"}).json()["id"]
        cliente.put(
            f"/projetos/100/liderancas/{lideranca_id}/pesquisas/10/config",
            json={"cota_votos_validos": 3000},
        )
        cliente.put(
            f"/projetos/100/liderancas/{lideranca_id}/territorios",
            json={"territorio_ids": [self.bairro_a.id, self.bairro_b.id]},
        )
        self._semear_coletas(total=100, alvo=50)

        resposta = cliente.post(
            "/projetos/100/liderancas/analise",
            json={
                "pesquisa_id": 10,
                "alvo": {"pergunta_id": 42, "valores": ["Candidato X"]},
                "filtros_respostas": [],
            },
        )
        self.assertEqual(resposta.status_code, 200)
        item = resposta.json()["liderancas"][0]
        self.assertEqual(item["resultado_principal"]["votos_projetados_alvo"], 3600)
        self.assertEqual(item["resultado_principal"]["gap_plus"], 600)
        self.assertEqual(item["resultado_principal"]["status"], "PLUS")

    def test_analise_de_outro_tenant_retorna_404(self):
        resposta = self._cliente(self.gerente_a).post(
            "/projetos/200/liderancas/analise",
            json={"pesquisa_id": 20, "alvo": {"pergunta_id": 42, "valores": ["X"]}},
        )
        self.assertEqual(resposta.status_code, 404)

    def test_alvo_nao_pode_ser_filtro(self):
        resposta = self._cliente(self.gerente_a).post(
            "/projetos/100/liderancas/analise",
            json={
                "pesquisa_id": 10,
                "alvo": {"pergunta_id": 42, "valores": ["Candidato X"]},
                "filtros_respostas": [{"pergunta_id": 42, "valores": ["Candidato Y"]}],
            },
        )
        self.assertEqual(resposta.status_code, 422)


class PosicionamentoTests(_LiderancaFixture):
    """BASE / OPOSICAO / INDEFINIDA como atributo da lideranca (Fase 2)."""

    def test_nao_existe_entidade_separada_para_oposicao(self):
        # A decisao de dominio: um unico CRUD descreve os dois campos politicos.
        tabelas = set(models.Base.metadata.tables)
        for proibida in ("liderancas_oposicao", "oposicoes", "liderancas_base"):
            with self.subTest(tabela=proibida):
                self.assertNotIn(proibida, tabelas)

    def test_lideranca_existente_recebe_indefinida(self):
        # Simula a linha que ja existia antes da coluna: o INSERT nao cita
        # posicionamento, entao quem responde e o server_default da migration.
        self._sql(
            "INSERT INTO liderancas_politicas (id,projeto_id,nome,ativo) VALUES"
            " (900,100,'Antiga',1)"
        )
        self.session.commit()
        antiga = self.session.get(models.LiderancaPolitica, 900)
        self.session.refresh(antiga)
        self.assertEqual(antiga.posicionamento, "INDEFINIDA")

    def test_criar_sem_informar_posicionamento_e_indefinida(self):
        cliente = self._cliente(self.gerente_a)
        criada = cliente.post("/projetos/100/liderancas", json={"nome": "Sem campo"})
        self.assertEqual(criada.status_code, 201)
        self.assertEqual(criada.json()["posicionamento"], "INDEFINIDA")

    def test_criar_com_cada_posicionamento_valido(self):
        cliente = self._cliente(self.gerente_a)
        for valor in ("BASE", "OPOSICAO", "INDEFINIDA"):
            with self.subTest(posicionamento=valor):
                criada = cliente.post(
                    "/projetos/100/liderancas",
                    json={"nome": "Lideranca " + valor, "posicionamento": valor},
                )
                self.assertEqual(criada.status_code, 201)
                self.assertEqual(criada.json()["posicionamento"], valor)

    def test_valor_invalido_e_rejeitado_com_422(self):
        cliente = self._cliente(self.gerente_a)
        for invalido in ("ALIADO", "base", "Oposicao", "", None, 1):
            with self.subTest(valor=invalido):
                resposta = cliente.post(
                    "/projetos/100/liderancas",
                    json={"nome": "X", "posicionamento": invalido},
                )
                self.assertEqual(resposta.status_code, 422)

    def test_servico_tambem_rejeita_valor_fora_do_dominio(self):
        # Chamada interna nao passa pelo Pydantic; a barreira precisa existir.
        with self.assertRaises(HTTPException) as erro:
            service.criar_lideranca(
                self.session, 100, "Y", self.gerente_a, posicionamento="ALIADO"
            )
        self.assertEqual(erro.exception.status_code, 422)

    def test_patch_base_para_oposicao_e_de_volta_para_indefinida(self):
        cliente = self._cliente(self.gerente_a)
        lideranca_id = cliente.post(
            "/projetos/100/liderancas", json={"nome": "Vira-casaca", "posicionamento": "BASE"}
        ).json()["id"]

        virou = cliente.patch(
            "/projetos/100/liderancas/" + str(lideranca_id), json={"posicionamento": "OPOSICAO"}
        )
        self.assertEqual(virou.json()["posicionamento"], "OPOSICAO")

        soltou = cliente.patch(
            "/projetos/100/liderancas/" + str(lideranca_id), json={"posicionamento": "INDEFINIDA"}
        )
        self.assertEqual(soltou.json()["posicionamento"], "INDEFINIDA")

    def test_patch_sem_o_campo_nao_altera_o_posicionamento(self):
        cliente = self._cliente(self.gerente_a)
        lideranca_id = cliente.post(
            "/projetos/100/liderancas", json={"nome": "Estavel", "posicionamento": "OPOSICAO"}
        ).json()["id"]

        renomeada = cliente.patch(
            "/projetos/100/liderancas/" + str(lideranca_id), json={"nome": "Estavel II"}
        )
        self.assertEqual(renomeada.json()["nome"], "Estavel II")
        self.assertEqual(renomeada.json()["posicionamento"], "OPOSICAO")

    def test_patch_com_valor_invalido_nao_persiste_nada(self):
        cliente = self._cliente(self.gerente_a)
        lideranca_id = cliente.post(
            "/projetos/100/liderancas", json={"nome": "Intacta", "posicionamento": "BASE"}
        ).json()["id"]

        recusado = cliente.patch(
            "/projetos/100/liderancas/" + str(lideranca_id),
            json={"nome": "Alterada", "posicionamento": "ALIADO"},
        )
        self.assertEqual(recusado.status_code, 422)
        atual = cliente.get("/projetos/100/liderancas/" + str(lideranca_id)).json()
        self.assertEqual(atual["nome"], "Intacta")
        self.assertEqual(atual["posicionamento"], "BASE")

    def _semear_tres(self, cliente):
        semente = (("A-Base", "BASE"), ("B-Oposicao", "OPOSICAO"), ("C-Indef", "INDEFINIDA"))
        for nome, valor in semente:
            cliente.post(
                "/projetos/100/liderancas", json={"nome": nome, "posicionamento": valor}
            )

    def test_listagem_sem_filtro_devolve_todas(self):
        cliente = self._cliente(self.gerente_a)
        self._semear_tres(cliente)
        listagem = cliente.get("/projetos/100/liderancas")
        self.assertEqual(len(listagem.json()), 3)

    def test_listagem_filtra_por_posicionamento(self):
        cliente = self._cliente(self.gerente_a)
        self._semear_tres(cliente)
        casos = (("BASE", "A-Base"), ("OPOSICAO", "B-Oposicao"), ("INDEFINIDA", "C-Indef"))
        for valor, nome_esperado in casos:
            with self.subTest(posicionamento=valor):
                filtrada = cliente.get(
                    "/projetos/100/liderancas?posicionamento=" + valor
                ).json()
                self.assertEqual([item["nome"] for item in filtrada], [nome_esperado])

    def test_filtro_invalido_na_listagem_e_422(self):
        cliente = self._cliente(self.gerente_a)
        self.assertEqual(
            cliente.get("/projetos/100/liderancas?posicionamento=ALIADO").status_code, 422
        )

    def test_filtro_respeita_o_soft_delete(self):
        cliente = self._cliente(self.gerente_a)
        lideranca_id = cliente.post(
            "/projetos/100/liderancas", json={"nome": "Saiu", "posicionamento": "BASE"}
        ).json()["id"]
        cliente.delete("/projetos/100/liderancas/" + str(lideranca_id))

        self.assertEqual(cliente.get("/projetos/100/liderancas?posicionamento=BASE").json(), [])
        incluindo = cliente.get(
            "/projetos/100/liderancas?posicionamento=BASE&incluir_inativas=true"
        ).json()
        self.assertEqual(len(incluindo), 1)

    def test_payload_continua_recusando_company_id(self):
        cliente = self._cliente(self.gerente_a)
        resposta = cliente.post(
            "/projetos/100/liderancas",
            json={"nome": "X", "posicionamento": "BASE", "company_id": 20},
        )
        self.assertEqual(resposta.status_code, 422)


class PosicionamentoMultitenancyTests(_LiderancaFixture):
    """Empresa A x Empresa B: o posicionamento nao abre nenhuma fresta."""

    def setUp(self):
        super().setUp()
        self.cliente_a = self._cliente(self.gerente_a)
        self.cliente_b = self._cliente(self.gerente_b)
        self.lideranca_a = self.cliente_a.post(
            "/projetos/100/liderancas", json={"nome": "Da empresa A", "posicionamento": "BASE"}
        ).json()["id"]
        self.lideranca_b = self.cliente_b.post(
            "/projetos/200/liderancas", json={"nome": "Da empresa B", "posicionamento": "OPOSICAO"}
        ).json()["id"]

    def test_cada_empresa_so_enxerga_a_propria_lideranca(self):
        nomes_a = [item["nome"] for item in self.cliente_a.get("/projetos/100/liderancas").json()]
        nomes_b = [item["nome"] for item in self.cliente_b.get("/projetos/200/liderancas").json()]
        self.assertEqual(nomes_a, ["Da empresa A"])
        self.assertEqual(nomes_b, ["Da empresa B"])

    def test_a_nao_le_lideranca_de_b_e_vice_versa(self):
        casos = (
            (self.cliente_a, 200, self.lideranca_b),
            (self.cliente_b, 100, self.lideranca_a),
        )
        for cliente, projeto_id, lideranca_id in casos:
            with self.subTest(projeto=projeto_id):
                resposta = cliente.get(
                    "/projetos/" + str(projeto_id) + "/liderancas/" + str(lideranca_id)
                )
                # 404, nunca 403: a resposta nao revela que a lideranca existe.
                self.assertEqual(resposta.status_code, 404)

    def test_a_nao_altera_o_posicionamento_de_b_e_vice_versa(self):
        casos = (
            (self.cliente_a, 200, self.lideranca_b),
            (self.cliente_b, 100, self.lideranca_a),
        )
        for cliente, projeto_id, lideranca_id in casos:
            with self.subTest(projeto=projeto_id):
                resposta = cliente.patch(
                    "/projetos/" + str(projeto_id) + "/liderancas/" + str(lideranca_id),
                    json={"posicionamento": "INDEFINIDA"},
                )
                self.assertEqual(resposta.status_code, 404)

        # Nenhum dos dois lados mudou.
        atual_a = self.cliente_a.get(
            "/projetos/100/liderancas/" + str(self.lideranca_a)
        ).json()
        atual_b = self.cliente_b.get(
            "/projetos/200/liderancas/" + str(self.lideranca_b)
        ).json()
        self.assertEqual(atual_a["posicionamento"], "BASE")
        self.assertEqual(atual_b["posicionamento"], "OPOSICAO")

    def test_filtro_nao_vaza_lideranca_do_outro_tenant(self):
        # B tem uma OPOSICAO; A filtrando por OPOSICAO no proprio projeto ve nada.
        self.assertEqual(
            self.cliente_a.get("/projetos/100/liderancas?posicionamento=OPOSICAO").json(), []
        )
        # E A filtrando dentro do projeto de B nem chega a filtrar: 404.
        self.assertEqual(
            self.cliente_a.get("/projetos/200/liderancas?posicionamento=OPOSICAO").status_code,
            404,
        )


class _CoberturaFixture(_LiderancaFixture):
    """Cenario territorial do escopo (Fase 3B.1).

    Setor 30:  B 8.000 + C 5.000 + E 7.000  -> universo 20.000
    Lideranca: A 5.000 + B 8.000 + C 5.000 + D 4.000

    Intersecao B+C = 13.000 -> 65,00%. A e D ficam de fora do denominador.
    """

    def setUp(self):
        super().setUp()
        self.bA = self._bairro("Cob A", 5000)
        self.bB = self._bairro("Cob B", 8000)
        self.bC = self._bairro("Cob C", 5000)
        self.bD = self._bairro("Cob D", 4000)
        self.bE = self._bairro("Cob E", 7000)

    def _municipio_da_base(self, base):
        """MUNICIPIO daquela base.

        `self.municipio` da fixture aponta para a ultima base criada, e a FK
        composta exige pai e filho na MESMA base -- resolver pela base evita
        cruzar as duas sem querer. Cria a arvore se a base ainda nao tiver.
        """
        municipio = (
            self.session.query(models.TerritorioEleitoral)
            .filter(
                models.TerritorioEleitoral.base_eleitoral_id == base.id,
                models.TerritorioEleitoral.tipo == "MUNICIPIO",
            )
            .first()
        )
        if municipio is not None:
            return municipio
        estado = models.TerritorioEleitoral(
            base_eleitoral_id=base.id, tipo="ESTADO", nome="Amapa",
            nome_normalizado="amapa",
        )
        self.session.add(estado)
        self.session.commit()
        municipio = models.TerritorioEleitoral(
            base_eleitoral_id=base.id, tipo="MUNICIPIO", nome="Macapa",
            nome_normalizado="macapa", parent_id=estado.id,
        )
        self.session.add(municipio)
        self.session.commit()
        return municipio

    def _bairro(self, nome, aptos, base=None, municipio=None):
        alvo = base or self.base
        pai = municipio or self._municipio_da_base(alvo)
        territorio = models.TerritorioEleitoral(
            base_eleitoral_id=alvo.id,
            tipo="BAIRRO",
            nome=nome,
            nome_normalizado=nome.lower(),
            parent_id=pai.id,
            municipio_id=pai.id,
            eleitorado_apto=aptos,
        )
        self.session.add(territorio)
        self.session.commit()
        return territorio

    def _compor_setor(self, setor_id, territorios):
        self.session.query(models.SetorTerritorioEleitoral).filter(
            models.SetorTerritorioEleitoral.setor_id == setor_id
        ).delete(synchronize_session=False)
        for territorio in territorios:
            self.session.add(
                models.SetorTerritorioEleitoral(
                    setor_id=setor_id, territorio_eleitoral_id=territorio.id
                )
            )
        self.session.commit()

    def _lideranca_com(self, territorios, *, setor_id=30, cota=3000, nome="Cobertura"):
        lideranca = self._criar_lideranca(nome=nome)
        service.definir_config_pesquisa(
            self.session, 100, lideranca.id, 10, self.gerente_a,
            setor_id=setor_id, cota_votos_validos=cota,
        )
        if territorios:
            self.session.query(models.LiderancaTerritorioEleitoral).filter(
                models.LiderancaTerritorioEleitoral.lideranca_id == lideranca.id
            ).delete(synchronize_session=False)
            for territorio in territorios:
                self.session.add(
                    models.LiderancaTerritorioEleitoral(
                        lideranca_id=lideranca.id, territorio_eleitoral_id=territorio.id
                    )
                )
            self.session.commit()
        return lideranca

    def _cobertura(self, lideranca):
        item = next(
            linha for linha in self._analisar()["liderancas"] if linha["id"] == lideranca.id
        )
        return item["cobertura_eleitoral"]


class CoberturaDisponivelTests(_CoberturaFixture):
    def test_cobertura_parcial_do_escopo(self):
        self._compor_setor(30, [self.bB, self.bC, self.bE])
        lideranca = self._lideranca_com([self.bA, self.bB, self.bC, self.bD])
        self._semear_coletas(total=100, alvo=50)

        cobertura = self._cobertura(lideranca)
        self.assertEqual(cobertura["status"], "DISPONIVEL")
        self.assertIsNone(cobertura["motivo_indisponibilidade"])
        self.assertEqual(cobertura["setor_id"], 30)
        self.assertEqual(cobertura["universo_eleitoral_setor"], 20000)
        self.assertEqual(cobertura["quantidade_territorios_lideranca"], 4)
        self.assertEqual(cobertura["quantidade_territorios_cobertos"], 2)
        self.assertEqual(cobertura["eleitorado_coberto"], 13000)
        self.assertEqual(cobertura["cobertura_percentual"], 65.0)

    def test_cobertura_total_fecha_exatamente_em_cem(self):
        self._compor_setor(30, [self.bA, self.bB])
        lideranca = self._lideranca_com([self.bA, self.bB])
        self._semear_coletas(total=100, alvo=50)

        cobertura = self._cobertura(lideranca)
        # Exatamente 100.0, sem 99.99 nem 100.01 de ponto flutuante.
        self.assertEqual(cobertura["cobertura_percentual"], 100.0)
        self.assertEqual(cobertura["eleitorado_coberto"], 13000)
        self.assertEqual(cobertura["universo_eleitoral_setor"], 13000)

    def test_lideranca_mais_ampla_que_o_setor_nao_passa_de_cem(self):
        self._compor_setor(30, [self.bA, self.bB])
        lideranca = self._lideranca_com([self.bA, self.bB, self.bC, self.bD, self.bE])
        self._semear_coletas(total=100, alvo=50)

        cobertura = self._cobertura(lideranca)
        self.assertEqual(cobertura["cobertura_percentual"], 100.0)
        self.assertLessEqual(cobertura["cobertura_percentual"], 100.0)
        # C, D e E existem na lideranca mas nao no denominador daquele setor.
        self.assertEqual(cobertura["quantidade_territorios_lideranca"], 5)
        self.assertEqual(cobertura["quantidade_territorios_cobertos"], 2)

    def test_zero_verdadeiro_e_disponivel(self):
        self._compor_setor(30, [self.bA, self.bB])
        lideranca = self._lideranca_com([self.bC, self.bD])
        self._semear_coletas(total=100, alvo=50)

        cobertura = self._cobertura(lideranca)
        # A informacao existe: a cobertura naquele setor e efetivamente zero.
        self.assertEqual(cobertura["status"], "DISPONIVEL")
        self.assertIsNone(cobertura["motivo_indisponibilidade"])
        self.assertEqual(cobertura["eleitorado_coberto"], 0)
        self.assertEqual(cobertura["cobertura_percentual"], 0.0)
        self.assertEqual(cobertura["quantidade_territorios_cobertos"], 0)
        # E nao null: zero real nao e ausencia de informacao.
        self.assertIsNotNone(cobertura["eleitorado_coberto"])

    def test_percentual_sempre_entre_zero_e_cem(self):
        casos = (
            ([self.bB, self.bC, self.bE], [self.bA, self.bB, self.bC, self.bD]),
            ([self.bA, self.bB], [self.bA, self.bB]),
            ([self.bA, self.bB], [self.bC, self.bD]),
            ([self.bA], [self.bA, self.bB, self.bC, self.bD, self.bE]),
        )
        self._semear_coletas(total=100, alvo=50)
        for indice, (setor, lider) in enumerate(casos):
            with self.subTest(caso=indice):
                self._compor_setor(30, setor)
                lideranca = self._lideranca_com(lider, nome=f"L{indice}")
                cobertura = self._cobertura(lideranca)
                self.assertEqual(cobertura["status"], "DISPONIVEL")
                self.assertGreaterEqual(cobertura["cobertura_percentual"], 0)
                self.assertLessEqual(cobertura["cobertura_percentual"], 100)

    def test_intersecao_usa_id_e_nao_nome(self):
        # Bairro homonimo em outro municipio: mesmo nome, id diferente.
        outro_municipio = models.TerritorioEleitoral(
            base_eleitoral_id=self.base.id, tipo="MUNICIPIO", nome="Santana",
            nome_normalizado="santana",
            parent_id=self._municipio_da_base(self.base).parent_id,
        )
        self.session.add(outro_municipio)
        self.session.commit()
        homonimo = self._bairro("Cob B", 9999, municipio=outro_municipio)

        self._compor_setor(30, [self.bB])
        lideranca = self._lideranca_com([homonimo])
        self._semear_coletas(total=100, alvo=50)

        cobertura = self._cobertura(lideranca)
        self.assertEqual(homonimo.nome, self.bB.nome)
        # Nomes iguais, ids diferentes: nao ha intersecao.
        self.assertEqual(cobertura["eleitorado_coberto"], 0)
        self.assertEqual(cobertura["cobertura_percentual"], 0.0)

    def test_soma_nao_multiplica_o_mesmo_bairro(self):
        self._compor_setor(30, [self.bB, self.bC])
        lideranca = self._lideranca_com([self.bB])
        self._semear_coletas(total=100, alvo=50)

        cobertura = self._cobertura(lideranca)
        # 8.000 uma vez, nao 16.000.
        self.assertEqual(cobertura["eleitorado_coberto"], 8000)
        self.assertEqual(cobertura["quantidade_territorios_cobertos"], 1)


class CoberturaIndisponivelTests(_CoberturaFixture):
    def test_sem_setor_de_referencia(self):
        self._compor_setor(30, [self.bA, self.bB])
        lideranca = self._lideranca_com([self.bA], setor_id=None)
        self._semear_coletas(total=100, alvo=50)

        cobertura = self._cobertura(lideranca)
        self.assertEqual(cobertura["status"], "INDISPONIVEL")
        self.assertEqual(cobertura["motivo_indisponibilidade"], "SEM_SETOR_REFERENCIA")
        self.assertIsNone(cobertura["setor_id"])
        self.assertIsNone(cobertura["universo_eleitoral_setor"])
        self.assertIsNone(cobertura["eleitorado_coberto"])
        self.assertIsNone(cobertura["cobertura_percentual"])

    def test_sem_setor_nao_usa_denominador_alternativo(self):
        self._compor_setor(30, [self.bA, self.bB])
        lideranca = self._lideranca_com([self.bA], setor_id=None)
        self._semear_coletas(total=100, alvo=50)
        cobertura = self._cobertura(lideranca)
        # Nem municipio, nem projeto, nem a soma dos proprios bairros.
        self.assertIsNone(cobertura["universo_eleitoral_setor"])
        self.assertNotEqual(cobertura["cobertura_percentual"], 100.0)

    def test_lideranca_sem_territorio(self):
        self._compor_setor(30, [self.bA, self.bB])
        lideranca = self._lideranca_com([], setor_id=30)
        self._semear_coletas(total=100, alvo=50)

        cobertura = self._cobertura(lideranca)
        self.assertEqual(
            cobertura["motivo_indisponibilidade"], "SEM_TERRITORIO_ELEITORAL_LIDERANCA"
        )
        self.assertIsNone(cobertura["eleitorado_coberto"])
        self.assertIsNone(cobertura["cobertura_percentual"])
        # Nunca 0%: nao ha informacao para medir.
        self.assertNotEqual(cobertura["cobertura_percentual"], 0.0)
        # O denominador existe e e informado: falta o numerador, nao o setor.
        self.assertEqual(cobertura["universo_eleitoral_setor"], 13000)

    def test_eleitorado_nulo_na_lideranca(self):
        sem_valor = self._bairro("Cob Sem Valor", None)
        self._compor_setor(30, [self.bA, self.bB])
        lideranca = self._lideranca_com([self.bA, sem_valor])
        self._semear_coletas(total=100, alvo=50)

        cobertura = self._cobertura(lideranca)
        self.assertEqual(
            cobertura["motivo_indisponibilidade"],
            "ELEITORADO_TERRITORIO_LIDERANCA_INDISPONIVEL",
        )
        self.assertIsNone(cobertura["eleitorado_coberto"])
        # NULL nao virou zero: nao devolveu os 5.000 de A.
        self.assertNotEqual(cobertura["eleitorado_coberto"], 5000)

    def test_universo_do_setor_zero_nao_divide_por_zero(self):
        zerado_a = self._bairro("Cob Zero A", 0)
        zerado_b = self._bairro("Cob Zero B", 0)
        self._compor_setor(30, [zerado_a, zerado_b])
        lideranca = self._lideranca_com([zerado_a])
        self._semear_coletas(total=100, alvo=50)

        cobertura = self._cobertura(lideranca)
        self.assertEqual(cobertura["motivo_indisponibilidade"], "UNIVERSO_ELEITORAL_ZERO")
        self.assertIsNone(cobertura["cobertura_percentual"])


class CoberturaBaseStaleTests(_CoberturaFixture):
    def _trocar_base_principal(self):
        nova = self._criar_base(company_id=10, versao="a-nova")
        self.session.execute(
            text("UPDATE projeto_base_eleitoral SET principal = 0 WHERE projeto_id = 100")
        )
        self.session.add(
            models.ProjetoBaseEleitoral(
                projeto_id=100, base_eleitoral_id=nova.id, principal=True
            )
        )
        self.session.commit()
        return nova

    def test_territorios_da_lideranca_de_base_anterior(self):
        self._compor_setor(30, [self.bA, self.bB])
        lideranca = self._lideranca_com([self.bA, self.bB])
        self._semear_coletas(total=100, alvo=50)
        self.assertEqual(self._cobertura(lideranca)["status"], "DISPONIVEL")

        self._trocar_base_principal()

        cobertura = self._cobertura(lideranca)
        self.assertEqual(cobertura["status"], "INDISPONIVEL")
        self.assertIsNone(cobertura["eleitorado_coberto"])
        self.assertIsNone(cobertura["cobertura_percentual"])

    def test_vinculos_da_lideranca_sao_preservados(self):
        self._compor_setor(30, [self.bA, self.bB])
        lideranca = self._lideranca_com([self.bA, self.bB])
        self._semear_coletas(total=100, alvo=50)
        self._trocar_base_principal()

        persistidos = self.session.execute(
            text(
                "SELECT count(*) FROM lideranca_territorio_eleitoral WHERE lideranca_id = :i"
            ).bindparams(i=lideranca.id)
        ).scalar()
        self.assertEqual(persistidos, 2)

    def test_lideranca_parcialmente_stale(self):
        nova = self._criar_base(company_id=10, versao="a-parcial")
        atual = self._bairro("Cob Novo", 3000, base=nova)
        # Municipio/parent da base nova para o territorio novo ficar coerente.
        self._compor_setor(30, [self.bA, self.bB])
        lideranca = self._lideranca_com([self.bA])
        self.session.add(
            models.LiderancaTerritorioEleitoral(
                lideranca_id=lideranca.id, territorio_eleitoral_id=atual.id
            )
        )
        self.session.commit()
        self._semear_coletas(total=100, alvo=50)

        cobertura = self._cobertura(lideranca)
        self.assertEqual(
            cobertura["motivo_indisponibilidade"], "TERRITORIO_LIDERANCA_BASE_DESATUALIZADA"
        )
        self.assertIsNone(cobertura["eleitorado_coberto"])
        # Nem os 5.000 do bairro que esta na base atual.
        self.assertNotEqual(cobertura["eleitorado_coberto"], 5000)

    def test_o_vinculo_antigo_nao_migra_sozinho(self):
        self._compor_setor(30, [self.bA, self.bB])
        lideranca = self._lideranca_com([self.bA])
        self._semear_coletas(total=100, alvo=50)
        self._trocar_base_principal()

        # Trocar a base principal desatualiza os DOIS lados de uma vez. Pela
        # precedencia (denominador antes do numerador), quem aparece e o motivo
        # do Setor -- e e o certo: nao adianta reconfigurar a lideranca antes de
        # o setor voltar a ter universo. O motivo proprio da lideranca aparece
        # em test_lideranca_parcialmente_stale, onde so ela esta desatualizada.
        cobertura = self._cobertura(lideranca)
        self.assertEqual(
            cobertura["motivo_indisponibilidade"], "COMPOSICAO_BASE_DESATUALIZADA"
        )
        self.assertIsNone(cobertura["cobertura_percentual"])

        # Nenhum casamento por nome em nenhum dos lados: o vinculo continua
        # apontando para a base anterior ate reconfiguracao explicita.
        persistidos = self.session.execute(
            text(
                "SELECT territorio_eleitoral_id FROM lideranca_territorio_eleitoral"
                " WHERE lideranca_id = :i"
            ).bindparams(i=lideranca.id)
        ).scalars().all()
        self.assertEqual(persistidos, [self.bA.id])


class CoberturaPropagacaoDoSetorTests(_CoberturaFixture):
    """Problema do denominador propaga o motivo do Setor, sem sinonimos."""

    def _preparar(self, territorios_setor=None):
        if territorios_setor is not None:
            self._compor_setor(30, territorios_setor)
        lideranca = self._lideranca_com([self.bA, self.bB])
        self._semear_coletas(total=100, alvo=50)
        return lideranca

    def test_setor_sem_composicao(self):
        lideranca = self._preparar([])
        cobertura = self._cobertura(lideranca)
        self.assertEqual(cobertura["motivo_indisponibilidade"], "SEM_COMPOSICAO_ELEITORAL")
        self.assertIsNone(cobertura["cobertura_percentual"])

    def test_composicao_do_setor_desatualizada(self):
        nova = self._criar_base(company_id=10, versao="a-setor")
        bairro_novo = self._bairro("Setor Novo", 1000, base=nova)
        self._compor_setor(30, [self.bA])
        lideranca = self._lideranca_com([bairro_novo])
        self._semear_coletas(total=100, alvo=50)
        # Base principal passa a ser a nova: a composicao do setor fica velha.
        self.session.execute(
            text("UPDATE projeto_base_eleitoral SET principal = 0 WHERE projeto_id = 100")
        )
        self.session.add(
            models.ProjetoBaseEleitoral(
                projeto_id=100, base_eleitoral_id=nova.id, principal=True
            )
        )
        self.session.commit()

        cobertura = self._cobertura(lideranca)
        self.assertEqual(
            cobertura["motivo_indisponibilidade"], "COMPOSICAO_BASE_DESATUALIZADA"
        )
        self.assertIsNone(cobertura["cobertura_percentual"])

    def test_projeto_sem_base_principal(self):
        lideranca = self._preparar([self.bA, self.bB])
        self.session.execute(
            text("DELETE FROM projeto_base_eleitoral WHERE projeto_id = 100")
        )
        self.session.commit()
        cobertura = self._cobertura(lideranca)
        self.assertEqual(
            cobertura["motivo_indisponibilidade"], "BASE_ELEITORAL_NAO_CONFIGURADA"
        )

    def test_base_principal_nao_validada(self):
        lideranca = self._preparar([self.bA, self.bB])
        self.session.execute(
            text("UPDATE base_eleitoral SET status = 'EM_CONFERENCIA' WHERE id = :i")
            .bindparams(i=self.base.id)
        )
        self.session.commit()
        cobertura = self._cobertura(lideranca)
        self.assertEqual(
            cobertura["motivo_indisponibilidade"], "BASE_ELEITORAL_NAO_VALIDADA"
        )

    def test_eleitorado_ausente_no_setor(self):
        sem_valor = self._bairro("Setor Sem Valor", None)
        lideranca = self._preparar([self.bA, sem_valor])
        cobertura = self._cobertura(lideranca)
        self.assertEqual(
            cobertura["motivo_indisponibilidade"], "ELEITORADO_TERRITORIO_INDISPONIVEL"
        )

    def test_motivos_do_setor_nao_ganham_sinonimo(self):
        from pesquisa360 import schemas as s

        motivos = {m.value for m in s.MotivoCoberturaEleitoral}
        for proibido in ("COBERTURA_SEM_COMPOSICAO", "COBERTURA_BASE_NAO_VALIDADA"):
            self.assertNotIn(proibido, motivos)
        # Todos os estados do universo aparecem tal como o Setor os emite.
        herdados = {m.value for m in s.StatusUniversoEleitoralSetor} - {"DISPONIVEL"}
        self.assertTrue(herdados <= motivos)


class CoberturaPrecedenciaTests(_CoberturaFixture):
    """Ordem determinística: denominador antes do numerador."""

    def test_sem_setor_vence_sem_territorio(self):
        lideranca = self._lideranca_com([], setor_id=None)
        self._semear_coletas(total=100, alvo=50)
        self.assertEqual(
            self._cobertura(lideranca)["motivo_indisponibilidade"], "SEM_SETOR_REFERENCIA"
        )

    def test_universo_indisponivel_vence_lideranca_sem_territorio(self):
        self._compor_setor(30, [])
        lideranca = self._lideranca_com([], setor_id=30)
        self._semear_coletas(total=100, alvo=50)
        # O denominador falta primeiro; adianta pouco reclamar do numerador.
        self.assertEqual(
            self._cobertura(lideranca)["motivo_indisponibilidade"],
            "SEM_COMPOSICAO_ELEITORAL",
        )

    def test_base_stale_da_lideranca_vence_eleitorado_nulo(self):
        nova = self._criar_base(company_id=10, versao="a-prec")
        stale_com_nulo = self._bairro("Cob Stale Nulo", None, base=nova)
        self._compor_setor(30, [self.bA, self.bB])
        lideranca = self._lideranca_com([self.bA])
        self.session.add(
            models.LiderancaTerritorioEleitoral(
                lideranca_id=lideranca.id, territorio_eleitoral_id=stale_com_nulo.id
            )
        )
        self.session.commit()
        self._semear_coletas(total=100, alvo=50)
        self.assertEqual(
            self._cobertura(lideranca)["motivo_indisponibilidade"],
            "TERRITORIO_LIDERANCA_BASE_DESATUALIZADA",
        )


class CoberturaMultitenancyTests(_CoberturaFixture):
    def test_analise_de_outro_tenant_e_404(self):
        with self.assertRaises(HTTPException) as erro:
            analytics.analisar_liderancas(
                self.session,
                projeto_id=100,
                pesquisa_id=10,
                pergunta_alvo_id=42,
                alvo_valores=["Candidato X"],
                filtros_respostas=None,
                lideranca_ids=None,
                current_user=self.gerente_b,
            )
        self.assertEqual(erro.exception.status_code, 404)

    def test_territorio_de_outro_tenant_nao_entra_na_cobertura(self):
        self._compor_setor(30, [self.bA, self.bB])
        lideranca = self._lideranca_com([self.bA])
        # Vinculo cru com bairro de outro tenant, impossivel pela API.
        self.session.add(
            models.LiderancaTerritorioEleitoral(
                lideranca_id=lideranca.id,
                territorio_eleitoral_id=self.bairro_outro_tenant.id,
            )
        )
        self.session.commit()
        self._semear_coletas(total=100, alvo=50)

        cobertura = self._cobertura(lideranca)
        # Base diferente: invalida em vez de somar eleitorado alheio.
        self.assertEqual(
            cobertura["motivo_indisponibilidade"], "TERRITORIO_LIDERANCA_BASE_DESATUALIZADA"
        )
        self.assertIsNone(cobertura["eleitorado_coberto"])

    def test_setor_de_outra_pesquisa_nao_serve_de_denominador(self):
        # Setor 40 pertence a pesquisa 20 (tenant B); config exige a onda certa.
        with self.assertRaises(HTTPException):
            self._lideranca_com([self.bA], setor_id=40)


class CoberturaFiltrosTests(_CoberturaFixture):
    def test_filtro_de_resposta_nao_altera_a_cobertura(self):
        self._compor_setor(30, [self.bB, self.bC, self.bE])
        lideranca = self._lideranca_com([self.bA, self.bB, self.bC, self.bD])
        self._semear_coletas(total=100, alvo=50, sexo_f=40, alvo_em_f=20)

        sem_filtro = self._cobertura(lideranca)
        com_filtro = next(
            linha
            for linha in self._analisar(
                filtros_respostas=[{"pergunta_id": 43, "valores": ["F"]}]
            )["liderancas"]
            if linha["id"] == lideranca.id
        )["cobertura_eleitoral"]

        # Cobertura e territorial: recorte de resposta nao mexe nela.
        self.assertEqual(sem_filtro, com_filtro)
        self.assertEqual(com_filtro["cobertura_percentual"], 65.0)

    def test_cobertura_acompanha_a_selecao_de_liderancas(self):
        self._compor_setor(30, [self.bA, self.bB])
        primeira = self._lideranca_com([self.bA], nome="Primeira")
        segunda = self._lideranca_com([self.bB], nome="Segunda")
        self._semear_coletas(total=100, alvo=50)

        resultado = self._analisar(lideranca_ids=[segunda.id])
        ids = [linha["id"] for linha in resultado["liderancas"]]
        self.assertEqual(ids, [segunda.id])
        self.assertNotIn(primeira.id, ids)
        self.assertIsNotNone(resultado["liderancas"][0]["cobertura_eleitoral"])


class CoberturaPerformanceTests(_CoberturaFixture):
    def _contar_queries(self, executar):
        from sqlalchemy import event as sa_event

        contagem = []
        engine = self.session.get_bind()

        def registrar(*_a, **_k):
            contagem.append(1)

        sa_event.listen(engine, "before_cursor_execute", registrar)
        try:
            executar()
        finally:
            sa_event.remove(engine, "before_cursor_execute", registrar)
        return len(contagem)

    def test_universo_do_setor_nao_multiplica_por_lideranca(self):
        # 3 setores distintos, 10 liderancas espalhadas entre eles.
        self.session.execute(
            text(
                "INSERT INTO setores (id,nome,meta,tolerancia,finalidade,pesquisa_id)"
                " VALUES (51,'S1',50,50,'AMBOS',10),(52,'S2',50,50,'AMBOS',10)"
            )
        )
        self.session.commit()
        self._compor_setor(30, [self.bA, self.bB])
        self._compor_setor(51, [self.bC])
        self._compor_setor(52, [self.bD])

        uma = self._lideranca_com([self.bA], setor_id=30, nome="L0")
        self._semear_coletas(total=100, alvo=50)
        com_uma = self._contar_queries(lambda: self._analisar())

        for indice in range(1, 10):
            setor = (30, 51, 52)[indice % 3]
            self._lideranca_com([self.bA], setor_id=setor, nome=f"L{indice}")
        com_dez = self._contar_queries(lambda: self._analisar())

        resultado = self._analisar()
        self.assertEqual(len(resultado["liderancas"]), 10)
        self.assertIsNotNone(uma)
        # 10 liderancas nao podem custar ~10x: o universo e resolvido por setor.
        self.assertLess(com_dez, com_uma * 2)

    def test_cache_de_universo_e_apenas_da_request(self):
        import inspect

        fonte = inspect.getsource(analytics.analisar_liderancas)
        # Dicionario local, nunca atributo de modulo nem store externo.
        self.assertIn("universos_por_setor", fonte)
        self.assertFalse(hasattr(analytics, "universos_por_setor"))
        self.assertFalse(hasattr(analytics, "_cache_universo"))


class CoberturaEscopoTests(_CoberturaFixture):
    """Guardas contra a fase crescer sozinha."""

    def _codigo_da_cobertura(self):
        """Fonte sem comentarios nem docstring.

        Os comentarios explicam justamente o que NAO se faz ("nao ha clamp",
        "geometria nao decide"), entao casariam com as proprias proibicoes.
        """
        import inspect

        fonte = inspect.getsource(analytics._calcular_cobertura_territorial)
        fonte = re.sub(r'"""[\s\S]*?"""', "", fonte)
        linhas = fonte.splitlines()
        return "\n".join(l for l in linhas if not l.strip().startswith("#"))

    def test_nao_ha_clamp_no_percentual(self):
        fonte = self._codigo_da_cobertura()
        for clamp in ("min(100", "min(Decimal(100)", "max(0,"):
            with self.subTest(clamp=clamp):
                self.assertNotIn(clamp, fonte)

    def test_cobertura_nao_usa_geometria(self):
        fonte = self._codigo_da_cobertura()
        for espacial in ("ST_Intersection", "ST_Contains", "ST_Covers", "ST_Area", "geometria"):
            with self.subTest(funcao=espacial):
                self.assertNotIn(espacial, fonte)

    def test_intersecao_nao_usa_nome(self):
        fonte = self._codigo_da_cobertura()
        for atributo in ("nome_normalizado", ".nome", "municipio_id"):
            with self.subTest(atributo=atributo):
                self.assertNotIn(atributo, fonte)

    def test_indicadores_de_fase_futura_nao_existem(self):
        proibidos = (
            "pressao_cotas",
            "pressao_de_cotas",
            "cotas_excedem_universo",
            "ocorrencias_para_meta",
            "amostra_planejada",
            "territorio_disputado",
            "votos_validos_projetados_setor",
        )
        for nome in proibidos:
            with self.subTest(indicador=nome):
                self.assertFalse(hasattr(analytics, nome))

    def test_contrato_da_cobertura_nao_traz_projecao(self):
        from pesquisa360 import schemas as s

        campos = set(s.CoberturaEleitoralLideranca.model_fields)
        for proibido in ("votos_validos_projetados", "pressao_cotas", "meta_ocorrencias"):
            with self.subTest(campo=proibido):
                self.assertNotIn(proibido, campos)

    def test_cobertura_nao_e_persistida(self):
        colunas = set(models.LiderancaPolitica.__table__.columns.keys())
        for proibida in ("cobertura_percentual", "eleitorado_coberto"):
            with self.subTest(coluna=proibida):
                self.assertNotIn(proibida, colunas)
        config = set(models.LiderancaPesquisaConfig.__table__.columns.keys())
        self.assertNotIn("cobertura_percentual", config)

    def test_universo_do_setor_nao_foi_duplicado(self):
        import inspect

        fonte = inspect.getsource(analytics)
        # A soma dos bairros do setor vive so em setor_territorio.
        self.assertIn("obter_universo_eleitoral_setor", fonte)
        # Mesma funcao, nao uma copia da regra.
        self.assertIs(
            analytics.obter_universo_eleitoral_setor,
            setor_territorio.obter_universo_eleitoral_setor,
        )


class NomenclaturaTests(unittest.TestCase):
    def test_conceito_de_mapa_permanece_intacto(self):
        from pesquisa360 import schemas as s

        self.assertEqual(s.TipoMapaEstrategico.LIDERANCA_SETOR.value, "LIDERANCA_SETOR")
        self.assertEqual(
            s.TipoAnaliseRelatorioExecutivo.MAPA_LIDERANCA_SETOR.value, "MAPA_LIDERANCA_SETOR"
        )
        # A entidade nova nao reaproveita esses identificadores.
        self.assertEqual(models.LiderancaPolitica.__tablename__, "liderancas_politicas")


if __name__ == "__main__":
    unittest.main()
