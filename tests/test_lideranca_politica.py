"""Gestao de Liderancas: schema, multitenancy, historico e analise (Fase 6A).

O cenario analitico segue exatamente a fixture controlada do escopo:
  Bairro A 6.000 + Bairro B 4.000 = 10.000 aptos
  comparecimento 0,80 x validos 0,90 -> 7.200 votos validos projetados
  100 entrevistas validas, 50 no alvo -> taxa 50% -> 3.600 votos projetados
"""

import os
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

        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)
        self._limpar()
        self._semear()

    def _limpar(self):
        for tabela in (
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
