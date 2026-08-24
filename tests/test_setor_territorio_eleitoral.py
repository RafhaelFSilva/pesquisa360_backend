"""Composicao eleitoral do Setor (Fase 3A.2).

Setor N:N TerritorioEleitoral, declarado pelo usuario. Nesta versao so BAIRRO e
aceito, porque e a unica unidade com eleitorado na Base atual. A exclusividade
vale entre setores ANALITICOS (RELATORIO/AMBOS): o bairro e indivisivel, entao
o mesmo eleitorado nao pode compor dois universos analiticos da mesma onda.

Cenario base (tenant A, company 10):
  projeto 100 -> pesquisa 10 -> setores 30 (RELATORIO), 31 (AMBOS),
                                        32 (OPERACAO), 33 (OPERACAO)
  base 1 (principal): Bairro A 6.000, Bairro B 4.000, Bairro C 2.000
"""

import os
import shutil
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-setor-territorio-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360 import crud, schemas
from pesquisa360.api.endpoints import projetos as rotas
from pesquisa360.core.dependencies import (
    get_current_user,
    get_db,
    require_manager_or_superadmin,
)
from pesquisa360.db import models
from pesquisa360.services import base_eleitoral as base_service
from pesquisa360.services import setor_territorio as service

from tests.test_base_eleitoral_import import run_alembic_upgrade


class _ComposicaoFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="pesquisa360-setor-territorio-"))
        cls.db_path = cls.temp_dir / "setor_territorio.db"
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
            # A serializacao de Setor usa AsGeoJSON; SQLite nao tem PostGIS.
            dbapi_connection.create_function("AsGeoJSON", 1, lambda valor: None)
            dbapi_connection.create_function("ST_AsGeoJSON", 1, lambda valor: None)
            dbapi_connection.create_function("ST_GeomFromText", 2, lambda valor, srid: valor)

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
        self._sql("INSERT INTO perfis (id, nome) VALUES (1,'Superadmin'),(2,'Gerente'),(3,'Agente')")
        self._sql("INSERT INTO companies (id, name, is_active) VALUES (10,'A',1),(20,'B',1)")
        self._sql(
            "INSERT INTO usuarios (id,email,nome,senha_hash,ativo,perfil_id,company_id) VALUES"
            " (1,'g.a@a','Gerente A','x',1,2,10),"
            " (2,'g.b@b','Gerente B','x',1,2,20)"
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
        # Malha analitica (30,31) e malha operacional (32,33) na MESMA onda.
        self._sql(
            "INSERT INTO setores (id,nome,meta,tolerancia,finalidade,pesquisa_id) VALUES"
            " (30,'Analitico Norte',50,50,'RELATORIO',10),"
            " (31,'Analitico Sul',50,50,'AMBOS',10),"
            " (32,'Operacional Leste',50,50,'OPERACAO',10),"
            " (33,'Operacional Oeste',50,50,'OPERACAO',10),"
            " (34,'Outra Onda',50,50,'RELATORIO',11),"
            " (40,'Setor de B',50,50,'RELATORIO',20)"
        )
        self.session.commit()

        self.base = self._criar_base(company_id=10, versao="a-1")
        self.base_secundaria = self._criar_base(company_id=10, versao="a-2")
        self.base_b = self._criar_base(company_id=20, versao="b-1")

        self.bairro_a, self.bairro_b, self.bairro_c = self._criar_territorios(
            self.base, 6000, 4000, 2000
        )
        (self.bairro_outra_base,) = self._criar_territorios(self.base_secundaria, 500)
        (self.bairro_outro_tenant,) = self._criar_territorios(self.base_b, 999)

        self._vincular_base(100, self.base.id)
        self._vincular_base(200, self.base_b.id)

    def _criar_base(self, company_id, versao):
        base = models.BaseEleitoral(
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
            eleitorado_apto=sum(aptos),
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
        if base.id == getattr(getattr(self, "base", None), "id", None):
            self.estado = estado
            self.municipio = municipio
        return criados

    def _criar_niveis_inferiores(self):
        """LOCAL_VOTACAO e SECAO nao existem na Base DEV: fixture controlada.

        Precisam existir para provar que a regra os recusa, e nao que eles
        apenas nao foram encontrados.
        """
        local = models.TerritorioEleitoral(
            base_eleitoral_id=self.base.id,
            tipo="LOCAL_VOTACAO",
            nome="Escola Municipal",
            nome_normalizado="escola municipal",
            parent_id=self.bairro_a.id,
            municipio_id=self.municipio.id,
            eleitorado_apto=1200,
        )
        self.session.add(local)
        self.session.commit()
        secao = models.TerritorioEleitoral(
            base_eleitoral_id=self.base.id,
            tipo="SECAO",
            nome="Secao 101",
            nome_normalizado="secao 101",
            parent_id=local.id,
            municipio_id=self.municipio.id,
            numero_secao=101,
            eleitorado_apto=400,
        )
        self.session.add(secao)
        self.session.commit()
        return local, secao

    def _vincular_base(self, projeto_id, base_id):
        self.session.add(
            models.ProjetoBaseEleitoral(
                projeto_id=projeto_id, base_eleitoral_id=base_id, principal=True
            )
        )
        self.session.commit()

    def _cliente(self, usuario):
        app = FastAPI()
        app.include_router(rotas.router)

        def _db():
            yield self.session

        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user] = lambda: usuario
        app.dependency_overrides[require_manager_or_superadmin] = lambda: usuario
        return TestClient(app)

    @property
    def gerente_a(self):
        return self.session.get(models.Usuario, 1)

    @property
    def gerente_b(self):
        return self.session.get(models.Usuario, 2)

    def _definir(self, setor_id, ids, projeto_id=100, pesquisa_id=10, usuario=None):
        return service.definir_territorios(
            self.session,
            projeto_id,
            pesquisa_id,
            setor_id,
            ids,
            usuario or self.gerente_a,
        )

    def _ids(self, territorios):
        return [territorio.id for territorio in territorios]


class SemanticaFinalidadeTests(_ComposicaoFixture):
    """A malha analitica e RELATORIO+AMBOS, espelhando crud.FINALIDADES_ANALITICAS."""

    def test_malha_analitica_e_relatorio_e_ambos(self):
        self.assertEqual(set(crud.FINALIDADES_ANALITICAS), {"RELATORIO", "AMBOS"})
        self.assertTrue(service.eh_analitico("RELATORIO"))
        self.assertTrue(service.eh_analitico("AMBOS"))
        self.assertFalse(service.eh_analitico("OPERACAO"))
        self.assertFalse(service.eh_analitico(None))


class ModelagemTests(_ComposicaoFixture):
    def test_vincular_um_bairro(self):
        resultado = self._definir(30, [self.bairro_a.id])
        self.assertEqual(self._ids(resultado), [self.bairro_a.id])

    def test_vincular_varios_bairros(self):
        resultado = self._definir(30, [self.bairro_a.id, self.bairro_b.id])
        self.assertEqual(
            sorted(self._ids(resultado)), sorted([self.bairro_a.id, self.bairro_b.id])
        )

    def test_substituicao_troca_o_conjunto_inteiro(self):
        self._definir(30, [self.bairro_a.id, self.bairro_b.id])
        resultado = self._definir(30, [self.bairro_b.id, self.bairro_c.id])
        # Substituicao, nao acumulo: A saiu.
        self.assertEqual(
            sorted(self._ids(resultado)), sorted([self.bairro_b.id, self.bairro_c.id])
        )

    def test_lista_vazia_limpa_a_composicao(self):
        self._definir(30, [self.bairro_a.id, self.bairro_b.id])
        self.assertEqual(self._definir(30, []), [])

    def test_put_repetido_e_idempotente(self):
        primeiro = self._definir(30, [self.bairro_a.id, self.bairro_b.id])
        segundo = self._definir(30, [self.bairro_a.id, self.bairro_b.id])
        self.assertEqual(self._ids(primeiro), self._ids(segundo))
        self.assertEqual(len(segundo), 2)

    def test_ids_repetidos_no_payload_nao_duplicam(self):
        resultado = self._definir(30, [self.bairro_a.id, self.bairro_a.id, self.bairro_b.id])
        self.assertEqual(len(resultado), 2)

    def test_banco_recusa_duplicata_no_mesmo_setor(self):
        self._definir(30, [self.bairro_a.id])
        self.session.add(
            models.SetorTerritorioEleitoral(
                setor_id=30, territorio_eleitoral_id=self.bairro_a.id
            )
        )
        with self.assertRaises(IntegrityError):
            self.session.commit()
        self.session.rollback()

    def test_setores_diferentes_tem_composicoes_independentes(self):
        self._definir(30, [self.bairro_a.id])
        self._definir(31, [self.bairro_b.id])
        self.assertEqual(self._ids(service.listar_territorios(self.session, 30)), [self.bairro_a.id])
        self.assertEqual(self._ids(service.listar_territorios(self.session, 31)), [self.bairro_b.id])

    def test_excluir_setor_remove_os_vinculos(self):
        self._definir(30, [self.bairro_a.id, self.bairro_b.id])
        # Setor tem delete fisico; o CASCADE evita FK violation e lixo orfao.
        self.session.execute(text("DELETE FROM setores WHERE id = 30"))
        self.session.commit()
        restantes = self.session.execute(
            text("SELECT count(*) FROM setor_territorio_eleitoral WHERE setor_id = 30")
        ).scalar()
        self.assertEqual(restantes, 0)

    def test_bairro_liberado_fica_disponivel_para_outro_setor(self):
        self._definir(30, [self.bairro_a.id])
        self._definir(30, [])
        # Sem estado reservado: o bairro volta ao pool imediatamente.
        resultado = self._definir(31, [self.bairro_a.id])
        self.assertEqual(self._ids(resultado), [self.bairro_a.id])


class RegraDeTipoTests(_ComposicaoFixture):
    def test_bairro_e_permitido(self):
        self.assertEqual(len(self._definir(30, [self.bairro_a.id])), 1)

    def test_municipio_e_estado_sao_rejeitados(self):
        for territorio in (self.municipio, self.estado):
            with self.subTest(tipo=territorio.tipo):
                with self.assertRaises(HTTPException) as erro:
                    self._definir(30, [territorio.id])
                self.assertEqual(erro.exception.status_code, 404)

    def test_local_votacao_e_secao_sao_rejeitados(self):
        local, secao = self._criar_niveis_inferiores()
        for territorio in (local, secao):
            with self.subTest(tipo=territorio.tipo):
                with self.assertRaises(HTTPException) as erro:
                    self._definir(30, [territorio.id])
                self.assertEqual(erro.exception.status_code, 404)

    def test_niveis_inferiores_existem_na_fixture(self):
        # Garante que o teste acima recusa por TIPO, nao por inexistencia.
        local, secao = self._criar_niveis_inferiores()
        self.assertEqual(local.tipo, "LOCAL_VOTACAO")
        self.assertEqual(secao.tipo, "SECAO")
        self.assertEqual(service.TIPO_TERRITORIO_ACEITO, "BAIRRO")


class RegraDeBaseTests(_ComposicaoFixture):
    def test_bairro_da_base_principal_e_permitido(self):
        self.assertEqual(len(self._definir(30, [self.bairro_a.id])), 1)

    def test_bairro_de_outra_base_do_mesmo_tenant_e_rejeitado(self):
        with self.assertRaises(HTTPException) as erro:
            self._definir(30, [self.bairro_outra_base.id])
        self.assertEqual(erro.exception.status_code, 404)

    def test_bairro_de_outro_tenant_e_rejeitado_sem_revelar(self):
        with self.assertRaises(HTTPException) as erro:
            self._definir(30, [self.bairro_outro_tenant.id])
        self.assertEqual(erro.exception.status_code, 404)
        self.assertNotIn("tenant", erro.exception.detail.lower())

    def test_territorio_inexistente_e_404(self):
        with self.assertRaises(HTTPException) as erro:
            self._definir(30, [999999])
        self.assertEqual(erro.exception.status_code, 404)

    def test_projeto_sem_base_principal_e_erro_explicito(self):
        self.session.execute(text("DELETE FROM projeto_base_eleitoral WHERE projeto_id = 100"))
        self.session.commit()
        with self.assertRaises(HTTPException) as erro:
            self._definir(30, [self.bairro_a.id])
        self.assertEqual(erro.exception.status_code, 422)
        self.assertIn("Base Eleitoral principal", erro.exception.detail)

    def test_sem_base_principal_lista_vazia_ainda_limpa(self):
        # Limpar nao depende de base: nao ha o que validar.
        self._definir(30, [self.bairro_a.id])
        self.session.execute(text("DELETE FROM projeto_base_eleitoral WHERE projeto_id = 100"))
        self.session.commit()
        self.assertEqual(self._definir(30, []), [])


class ExclusividadeTests(_ComposicaoFixture):
    """Bairro e indivisivel: nao pode compor dois universos analiticos."""

    def test_relatorio_x_relatorio_e_proibido(self):
        self._definir(30, [self.bairro_a.id])
        self.session.execute(text("UPDATE setores SET finalidade='RELATORIO' WHERE id=31"))
        self.session.commit()
        with self.assertRaises(HTTPException) as erro:
            self._definir(31, [self.bairro_a.id])
        self.assertEqual(erro.exception.status_code, 409)

    def test_relatorio_x_ambos_e_proibido(self):
        self._definir(30, [self.bairro_a.id])
        with self.assertRaises(HTTPException) as erro:
            self._definir(31, [self.bairro_a.id])
        self.assertEqual(erro.exception.status_code, 409)

    def test_ambos_x_ambos_e_proibido(self):
        self.session.execute(text("UPDATE setores SET finalidade='AMBOS' WHERE id=30"))
        self.session.commit()
        self._definir(30, [self.bairro_a.id])
        with self.assertRaises(HTTPException) as erro:
            self._definir(31, [self.bairro_a.id])
        self.assertEqual(erro.exception.status_code, 409)

    def test_operacao_x_relatorio_e_permitido(self):
        # OPERACAO nao alcanca indicador eleitoral algum: `_validar_setores_analiticos`
        # filtra por FINALIDADES_ANALITICAS antes de classificar coletas. A malha
        # operacional pode, portanto, recortar o territorio de outro jeito.
        self._definir(32, [self.bairro_a.id])
        resultado = self._definir(30, [self.bairro_a.id])
        self.assertEqual(self._ids(resultado), [self.bairro_a.id])

    def test_relatorio_x_operacao_e_permitido(self):
        self._definir(30, [self.bairro_a.id])
        resultado = self._definir(32, [self.bairro_a.id])
        self.assertEqual(self._ids(resultado), [self.bairro_a.id])

    def test_operacao_x_operacao_e_permitido(self):
        self._definir(32, [self.bairro_a.id])
        resultado = self._definir(33, [self.bairro_a.id])
        self.assertEqual(self._ids(resultado), [self.bairro_a.id])

    def test_mesmo_bairro_em_ondas_diferentes_e_permitido(self):
        # A exclusividade e por PESQUISA: outra onda tem universo proprio.
        self._definir(30, [self.bairro_a.id])
        resultado = self._definir(34, [self.bairro_a.id], pesquisa_id=11)
        self.assertEqual(self._ids(resultado), [self.bairro_a.id])

    def test_reenviar_o_proprio_bairro_nao_conflita_consigo(self):
        self._definir(30, [self.bairro_a.id])
        resultado = self._definir(30, [self.bairro_a.id, self.bairro_b.id])
        self.assertEqual(len(resultado), 2)

    def test_conflito_informa_qual_setor_ocupa(self):
        self._definir(30, [self.bairro_a.id])
        with self.assertRaises(HTTPException) as erro:
            self._definir(31, [self.bairro_a.id])
        self.assertIn("Analitico Norte", erro.exception.detail)
        self.assertIn("Bairro A", erro.exception.detail)


class AtomicidadeTests(_ComposicaoFixture):
    def test_falha_preserva_a_composicao_anterior(self):
        self._definir(30, [self.bairro_a.id, self.bairro_b.id])
        with self.assertRaises(HTTPException):
            # Bairro C valido + territorio de outra base: o lote inteiro cai.
            self._definir(30, [self.bairro_c.id, self.bairro_outra_base.id])
        atual = self._ids(service.listar_territorios(self.session, 30))
        self.assertEqual(sorted(atual), sorted([self.bairro_a.id, self.bairro_b.id]))

    def test_conflito_preserva_a_composicao_anterior(self):
        self._definir(30, [self.bairro_a.id])
        self._definir(31, [self.bairro_b.id])
        with self.assertRaises(HTTPException):
            self._definir(31, [self.bairro_c.id, self.bairro_a.id])
        atual = self._ids(service.listar_territorios(self.session, 31))
        self.assertEqual(atual, [self.bairro_b.id])

    def test_tipo_invalido_no_meio_do_lote_derruba_tudo(self):
        self._definir(30, [self.bairro_a.id])
        with self.assertRaises(HTTPException):
            self._definir(30, [self.bairro_b.id, self.municipio.id])
        self.assertEqual(
            self._ids(service.listar_territorios(self.session, 30)), [self.bairro_a.id]
        )


class MudancaDeFinalidadeTests(_ComposicaoFixture):
    """Porta lateral: promover setor operacional nao pode criar dupla contagem."""

    def _patch_finalidade(self, setor_id, finalidade, usuario=None):
        cliente = self._cliente(usuario or self.gerente_a)
        return cliente.patch(
            f"/projetos/100/pesquisas/10/setores/{setor_id}",
            json={"finalidade": finalidade},
        )

    def test_promover_operacional_em_conflito_e_rejeitado(self):
        self._definir(32, [self.bairro_a.id])
        self._definir(30, [self.bairro_a.id])
        resposta = self._patch_finalidade(32, "RELATORIO")
        self.assertEqual(resposta.status_code, 409)

    def test_promocao_rejeitada_nao_persiste_nada(self):
        self._definir(32, [self.bairro_a.id])
        self._definir(30, [self.bairro_a.id])
        self._patch_finalidade(32, "AMBOS")
        self.session.expire_all()
        setor = self.session.get(models.Setor, 32)
        self.assertEqual(setor.finalidade, "OPERACAO")

    def test_promover_operacional_sem_conflito_funciona(self):
        self._definir(32, [self.bairro_c.id])
        self._definir(30, [self.bairro_a.id])
        resposta = self._patch_finalidade(32, "RELATORIO")
        self.assertEqual(resposta.status_code, 200)
        self.session.expire_all()
        self.assertEqual(self.session.get(models.Setor, 32).finalidade, "RELATORIO")

    def test_rebaixar_analitico_para_operacao_sempre_pode(self):
        self._definir(30, [self.bairro_a.id])
        resposta = self._patch_finalidade(30, "OPERACAO")
        self.assertEqual(resposta.status_code, 200)

    def test_relatorio_para_ambos_nao_e_barrado(self):
        # Ja era analitico: o conflito, se existisse, teria sido barrado antes.
        self._definir(30, [self.bairro_a.id])
        resposta = self._patch_finalidade(30, "AMBOS")
        self.assertEqual(resposta.status_code, 200)

    def test_setor_sem_composicao_muda_livremente(self):
        resposta = self._patch_finalidade(32, "RELATORIO")
        self.assertEqual(resposta.status_code, 200)


class ConcorrenciaTests(_ComposicaoFixture):
    def test_conflito_e_detectado_apos_commit_concorrente(self):
        """Prova da protecao transacional.

        A suite roda em SQLite, onde `FOR UPDATE` nao e emitido e nao ha
        paralelismo real. O que se prova aqui e a segunda metade da protecao:
        depois que a primeira transacao comita, a segunda LE o vinculo e recusa
        -- ou seja, a checagem consulta o estado do banco, nao um cache do
        processo. Em PostgreSQL, `_bloquear_territorios` trava as linhas de
        territorio (ORDER BY id, evitando deadlock) antes desta checagem, de
        modo que a segunda transacao so a executa depois do commit da primeira.
        """
        self._definir(30, [self.bairro_a.id])

        outra_sessao = self.Session()
        self.addCleanup(outra_sessao.close)
        with self.assertRaises(HTTPException) as erro:
            service.definir_territorios(
                outra_sessao, 100, 10, 31, [self.bairro_a.id],
                outra_sessao.get(models.Usuario, 1),
            )
        self.assertEqual(erro.exception.status_code, 409)

    def test_lock_ordena_por_id(self):
        # Ordem estavel de aquisicao evita deadlock entre conjuntos que se cruzam.
        import inspect

        fonte = inspect.getsource(service._bloquear_territorios)
        self.assertIn("order_by", fonte)
        self.assertIn("with_for_update", fonte)


class ApiTests(_ComposicaoFixture):
    def test_get_devolve_composicao_com_municipio_e_eleitorado(self):
        self._definir(30, [self.bairro_a.id])
        cliente = self._cliente(self.gerente_a)
        resposta = cliente.get("/projetos/100/pesquisas/10/setores/30/territorios")
        self.assertEqual(resposta.status_code, 200)
        item = resposta.json()[0]
        self.assertEqual(item["id"], self.bairro_a.id)
        self.assertEqual(item["nome"], "Bairro A")
        self.assertEqual(item["eleitorado_apto"], 6000)
        self.assertEqual(item["municipio_id"], self.municipio.id)

    def test_put_define_a_composicao(self):
        cliente = self._cliente(self.gerente_a)
        resposta = cliente.put(
            "/projetos/100/pesquisas/10/setores/30/territorios",
            json={"territorio_eleitoral_ids": [self.bairro_a.id, self.bairro_b.id]},
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(len(resposta.json()), 2)

    def test_put_conflitante_responde_409(self):
        cliente = self._cliente(self.gerente_a)
        cliente.put(
            "/projetos/100/pesquisas/10/setores/30/territorios",
            json={"territorio_eleitoral_ids": [self.bairro_a.id]},
        )
        resposta = cliente.put(
            "/projetos/100/pesquisas/10/setores/31/territorios",
            json={"territorio_eleitoral_ids": [self.bairro_a.id]},
        )
        self.assertEqual(resposta.status_code, 409)

    def test_payload_recusa_company_id(self):
        cliente = self._cliente(self.gerente_a)
        resposta = cliente.put(
            "/projetos/100/pesquisas/10/setores/30/territorios",
            json={"territorio_eleitoral_ids": [], "company_id": 20},
        )
        self.assertEqual(resposta.status_code, 422)

    def test_payload_recusa_base_eleitoral_id(self):
        cliente = self._cliente(self.gerente_a)
        resposta = cliente.put(
            "/projetos/100/pesquisas/10/setores/30/territorios",
            json={"territorio_eleitoral_ids": [], "base_eleitoral_id": 1},
        )
        self.assertEqual(resposta.status_code, 422)

    def test_setor_de_outra_pesquisa_no_caminho_e_404(self):
        cliente = self._cliente(self.gerente_a)
        resposta = cliente.get("/projetos/100/pesquisas/11/setores/30/territorios")
        self.assertEqual(resposta.status_code, 404)


class MultitenancyTests(_ComposicaoFixture):
    def test_a_le_a_propria_composicao(self):
        self._definir(30, [self.bairro_a.id])
        cliente = self._cliente(self.gerente_a)
        resposta = cliente.get("/projetos/100/pesquisas/10/setores/30/territorios")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(len(resposta.json()), 1)

    def test_a_nao_le_composicao_de_b(self):
        cliente = self._cliente(self.gerente_a)
        resposta = cliente.get("/projetos/200/pesquisas/20/setores/40/territorios")
        self.assertEqual(resposta.status_code, 404)

    def test_b_nao_le_composicao_de_a(self):
        self._definir(30, [self.bairro_a.id])
        cliente = self._cliente(self.gerente_b)
        resposta = cliente.get("/projetos/100/pesquisas/10/setores/30/territorios")
        self.assertEqual(resposta.status_code, 404)

    def test_a_nao_vincula_territorio_de_b(self):
        with self.assertRaises(HTTPException) as erro:
            self._definir(30, [self.bairro_outro_tenant.id])
        self.assertEqual(erro.exception.status_code, 404)

    def test_b_nao_altera_setor_de_a(self):
        cliente = self._cliente(self.gerente_b)
        resposta = cliente.put(
            "/projetos/100/pesquisas/10/setores/30/territorios",
            json={"territorio_eleitoral_ids": [self.bairro_a.id]},
        )
        self.assertEqual(resposta.status_code, 404)

    def test_composicao_de_a_permanece_intacta_apos_tentativa_de_b(self):
        self._definir(30, [self.bairro_a.id])
        cliente = self._cliente(self.gerente_b)
        cliente.put(
            "/projetos/100/pesquisas/10/setores/30/territorios",
            json={"territorio_eleitoral_ids": []},
        )
        self.assertEqual(
            self._ids(service.listar_territorios(self.session, 30)), [self.bairro_a.id]
        )


class EscopoTests(_ComposicaoFixture):
    """Esta fase estabelece composicao, nao indicadores."""

    def test_servico_nao_calcula_universo_nem_cobertura(self):
        proibidos = (
            "universo_eleitoral",
            "eleitorado_coberto",
            "cobertura_percentual",
            "votos_validos_projetados",
            "pressao_cotas",
            "ocorrencias_para_meta",
            "amostra_planejada",
        )
        for nome in proibidos:
            with self.subTest(indicador=nome):
                self.assertFalse(hasattr(service, nome))

    def test_servico_nao_usa_geometria(self):
        # Chamada espacial, nao a palavra: o docstring do modulo cita geometria
        # justamente para dizer que nao a usa.
        import inspect

        fonte = inspect.getsource(service)
        for espacial in (
            "ST_Intersection",
            "ST_Contains",
            "ST_Covers",
            "ST_Area",
            "ST_Within",
            ".geometria",
            "Setor.geometria",
        ):
            with self.subTest(funcao=espacial):
                self.assertNotIn(espacial, fonte)

    def test_tabela_nao_tem_peso_nem_percentual(self):
        # Nao existe rateio parcial de bairro: o vinculo e por unidade inteira.
        colunas = set(models.SetorTerritorioEleitoral.__table__.columns.keys())
        for fracionario in ("peso", "percentual", "fracao", "proporcao"):
            with self.subTest(coluna=fracionario):
                self.assertNotIn(fracionario, colunas)

    def test_schema_de_resposta_nao_expoe_indicador(self):
        campos = set(schemas.SetorTerritorioItem.model_fields)
        self.assertEqual(campos, {"id", "nome", "municipio_id", "eleitorado_apto"})


class UniversoEleitoralTests(_ComposicaoFixture):
    """Universo eleitoral do Setor (Fase 3A.3): soma auditavel, nunca zero por ausencia."""

    def _universo(self, setor_id=30, projeto_id=100, pesquisa_id=10, usuario=None):
        return service.obter_universo_eleitoral_setor(
            self.session, projeto_id, pesquisa_id, setor_id, usuario or self.gerente_a
        )

    # --- DISPONIVEL ----------------------------------------------------------

    def test_soma_o_eleitorado_dos_bairros_vinculados(self):
        # Bairro A 6.000 + Bairro B 4.000 + Bairro C 2.000
        self._definir(30, [self.bairro_a.id, self.bairro_b.id, self.bairro_c.id])
        universo = self._universo()
        self.assertEqual(universo["status"], "DISPONIVEL")
        self.assertIsNone(universo["motivo_indisponibilidade"])
        self.assertEqual(universo["quantidade_territorios"], 3)
        self.assertEqual(universo["eleitorado_apto"], 12000)
        self.assertEqual(universo["base_eleitoral_id"], self.base.id)

    def test_um_unico_bairro(self):
        self._definir(30, [self.bairro_a.id])
        universo = self._universo()
        self.assertEqual(universo["eleitorado_apto"], 6000)
        self.assertEqual(universo["quantidade_territorios"], 1)

    def test_universo_acompanha_a_recomposicao(self):
        self._definir(30, [self.bairro_a.id, self.bairro_b.id])
        self.assertEqual(self._universo()["eleitorado_apto"], 10000)
        # Calculado em leitura: trocar a composicao muda o numero na hora.
        self._definir(30, [self.bairro_c.id])
        self.assertEqual(self._universo()["eleitorado_apto"], 2000)

    # --- SEM_COMPOSICAO_ELEITORAL --------------------------------------------

    def test_setor_sem_composicao_nao_tem_universo(self):
        universo = self._universo()
        self.assertEqual(universo["status"], "SEM_COMPOSICAO_ELEITORAL")
        self.assertEqual(universo["motivo_indisponibilidade"], "SEM_COMPOSICAO_ELEITORAL")
        self.assertEqual(universo["quantidade_territorios"], 0)
        self.assertIsNone(universo["eleitorado_apto"])

    def test_ausencia_de_composicao_nunca_vira_zero(self):
        universo = self._universo()
        # Zero eleitores e um resultado; "nao configurado" e outra coisa.
        self.assertIsNot(universo["eleitorado_apto"], 0)
        self.assertNotEqual(universo["eleitorado_apto"], 0)
        self.assertIsNone(universo["eleitorado_apto"])

    def test_limpar_a_composicao_volta_a_indisponivel(self):
        self._definir(30, [self.bairro_a.id])
        self.assertEqual(self._universo()["status"], "DISPONIVEL")
        self._definir(30, [])
        universo = self._universo()
        self.assertEqual(universo["status"], "SEM_COMPOSICAO_ELEITORAL")
        self.assertIsNone(universo["eleitorado_apto"])

    # --- COMPOSICAO_BASE_DESATUALIZADA ---------------------------------------

    def _tornar_principal(self, base_id):
        """Troca a base principal sem apagar vinculo algum."""
        self.session.execute(
            text("UPDATE projeto_base_eleitoral SET principal = false WHERE projeto_id = 100")
        )
        self.session.add(
            models.ProjetoBaseEleitoral(
                projeto_id=100, base_eleitoral_id=base_id, principal=True
            )
        )
        self.session.commit()

    def test_troca_de_base_torna_o_universo_indisponivel(self):
        self._definir(30, [self.bairro_a.id, self.bairro_b.id])
        self.assertEqual(self._universo()["eleitorado_apto"], 10000)

        self._tornar_principal(self.base_secundaria.id)

        universo = self._universo()
        self.assertEqual(universo["status"], "COMPOSICAO_BASE_DESATUALIZADA")
        self.assertEqual(universo["motivo_indisponibilidade"], "COMPOSICAO_BASE_DESATUALIZADA")
        self.assertIsNone(universo["eleitorado_apto"])
        # base_eleitoral_id e a principal ATUAL, nao a dos vinculos.
        self.assertEqual(universo["base_eleitoral_id"], self.base_secundaria.id)

    def test_troca_de_base_preserva_os_vinculos(self):
        self._definir(30, [self.bairro_a.id, self.bairro_b.id])
        self._tornar_principal(self.base_secundaria.id)

        # Auditabilidade: os vinculos antigos continuam no banco.
        persistidos = self.session.execute(
            text("SELECT count(*) FROM setor_territorio_eleitoral WHERE setor_id = 30")
        ).scalar()
        self.assertEqual(persistidos, 2)
        self.assertEqual(self._universo()["quantidade_territorios"], 2)

    def test_quantidade_distingue_desatualizado_de_nao_configurado(self):
        self._definir(30, [self.bairro_a.id, self.bairro_b.id])
        self._tornar_principal(self.base_secundaria.id)
        desatualizado = self._universo()
        nao_configurado = self._universo(setor_id=31)

        self.assertEqual(desatualizado["quantidade_territorios"], 2)
        self.assertEqual(nao_configurado["quantidade_territorios"], 0)
        # Ambos sem universo, por motivos diferentes -- e a UI consegue separar.
        self.assertIsNone(desatualizado["eleitorado_apto"])
        self.assertIsNone(nao_configurado["eleitorado_apto"])
        self.assertNotEqual(desatualizado["status"], nao_configurado["status"])

    def test_composicao_parcialmente_desatualizada_nao_soma_o_pedaco_atual(self):
        """Estado nao produzivel pela API, montado direto no banco.

        1 unidade da base atual + 1 da anterior. Somar so a atual devolveria um
        universo parcial com cara de completo.
        """
        self._definir(30, [self.bairro_a.id])
        self.session.add(
            models.SetorTerritorioEleitoral(
                setor_id=30, territorio_eleitoral_id=self.bairro_outra_base.id
            )
        )
        self.session.commit()

        universo = self._universo()
        self.assertEqual(universo["status"], "COMPOSICAO_BASE_DESATUALIZADA")
        self.assertIsNone(universo["eleitorado_apto"])
        self.assertEqual(universo["quantidade_territorios"], 2)
        # Nem o valor do bairro atual (6.000) nem a soma dos dois (6.500).
        self.assertNotEqual(universo["eleitorado_apto"], 6000)
        self.assertNotEqual(universo["eleitorado_apto"], 6500)

    def test_reconfigurar_para_a_base_atual_restaura_o_universo(self):
        self._definir(30, [self.bairro_a.id])
        self._tornar_principal(self.base_secundaria.id)
        self.assertEqual(self._universo()["status"], "COMPOSICAO_BASE_DESATUALIZADA")

        # Reconfiguracao explicita pelo usuario: nada foi remapeado sozinho.
        self._definir(30, [self.bairro_outra_base.id])
        universo = self._universo()
        self.assertEqual(universo["status"], "DISPONIVEL")
        self.assertEqual(universo["eleitorado_apto"], 500)

    def test_nao_ha_remapeamento_por_nome_entre_bases(self):
        # As duas bases tem "Bairro A"; o vinculo e por id e nao migra sozinho.
        self._definir(30, [self.bairro_a.id])
        self._tornar_principal(self.base_secundaria.id)
        self.assertEqual(self.bairro_a.nome, self.bairro_outra_base.nome)
        self.assertEqual(self._universo()["status"], "COMPOSICAO_BASE_DESATUALIZADA")

    # --- BASE_ELEITORAL_NAO_CONFIGURADA --------------------------------------

    def test_projeto_sem_base_principal(self):
        self._definir(30, [self.bairro_a.id])
        self.session.execute(text("DELETE FROM projeto_base_eleitoral WHERE projeto_id = 100"))
        self.session.commit()

        universo = self._universo()
        self.assertEqual(universo["status"], "BASE_ELEITORAL_NAO_CONFIGURADA")
        self.assertIsNone(universo["eleitorado_apto"])
        # Sem fallback: nao escolhe "a primeira" nem "a mais recente".
        self.assertIsNone(universo["base_eleitoral_id"])

    # --- BASE_ELEITORAL_NAO_VALIDADA -----------------------------------------

    def test_base_principal_nao_validada(self):
        self._definir(30, [self.bairro_a.id])
        for status_base in ("IMPORTADA", "EM_CONFERENCIA", "SUBSTITUIDA"):
            with self.subTest(status=status_base):
                self.session.execute(
                    text("UPDATE base_eleitoral SET status = :s WHERE id = :i").bindparams(
                        s=status_base, i=self.base.id
                    )
                )
                self.session.commit()
                universo = self._universo()
                self.assertEqual(universo["status"], "BASE_ELEITORAL_NAO_VALIDADA")
                self.assertIsNone(universo["eleitorado_apto"])
                self.assertEqual(universo["base_eleitoral_id"], self.base.id)

    def test_validade_segue_a_definicao_do_dominio(self):
        # Mesmo conceito que lideranca_analytics usa; nada foi reinventado.
        self.assertEqual(base_service.STATUS_VALIDADA, "VALIDADA")

    # --- ELEITORADO_TERRITORIO_INDISPONIVEL ----------------------------------

    def test_bairro_sem_eleitorado_torna_o_universo_indisponivel(self):
        # eleitorado_apto e nullable no schema: cenario possivel em base futura.
        sem_valor = models.TerritorioEleitoral(
            base_eleitoral_id=self.base.id,
            tipo="BAIRRO",
            nome="Bairro Sem Valor",
            nome_normalizado="bairro sem valor",
            parent_id=self.municipio.id,
            municipio_id=self.municipio.id,
            eleitorado_apto=None,
        )
        self.session.add(sem_valor)
        self.session.commit()

        self._definir(30, [self.bairro_a.id, sem_valor.id])
        universo = self._universo()
        self.assertEqual(universo["status"], "ELEITORADO_TERRITORIO_INDISPONIVEL")
        self.assertIsNone(universo["eleitorado_apto"])
        # NULL nao virou zero: nao devolveu os 6.000 do bairro que tem valor.
        self.assertNotEqual(universo["eleitorado_apto"], 6000)

    def test_eleitorado_zero_e_valor_valido_e_nao_indisponibilidade(self):
        zerado = models.TerritorioEleitoral(
            base_eleitoral_id=self.base.id,
            tipo="BAIRRO",
            nome="Bairro Zerado",
            nome_normalizado="bairro zerado",
            parent_id=self.municipio.id,
            municipio_id=self.municipio.id,
            eleitorado_apto=0,
        )
        self.session.add(zerado)
        self.session.commit()

        self._definir(30, [self.bairro_a.id, zerado.id])
        universo = self._universo()
        # Zero declarado e um resultado; soma normalmente.
        self.assertEqual(universo["status"], "DISPONIVEL")
        self.assertEqual(universo["eleitorado_apto"], 6000)

    # --- deduplicacao defensiva ----------------------------------------------

    def test_soma_nao_multiplica_por_join(self):
        self._definir(30, [self.bairro_a.id])
        # Um bairro de 6.000 continua 6.000; um join que duplicasse a linha
        # devolveria 12.000 sem nenhum erro aparente.
        self.assertEqual(self._universo()["eleitorado_apto"], 6000)
        self.assertEqual(self._universo()["quantidade_territorios"], 1)

    def test_dedup_explicita_no_helper(self):
        import inspect

        fonte = inspect.getsource(service._territorios_da_composicao)
        # A soma nao pode depender so do UNIQUE do banco.
        self.assertIn("unicos", fonte)

    # --- finalidade ----------------------------------------------------------

    def test_universo_individual_existe_para_qualquer_finalidade(self):
        esperado = {30: "RELATORIO", 31: "AMBOS", 32: "OPERACAO"}
        for setor_id, finalidade in esperado.items():
            self.assertEqual(
                self.session.get(models.Setor, setor_id).finalidade, finalidade
            )

        self._definir(30, [self.bairro_a.id])
        self._definir(31, [self.bairro_b.id])
        # OPERACAO pode repetir o bairro do analitico: nao disputa universo.
        self._definir(32, [self.bairro_a.id])

        for setor_id, aptos in ((30, 6000), (31, 4000), (32, 6000)):
            with self.subTest(finalidade=esperado[setor_id]):
                universo = self._universo(setor_id)
                self.assertEqual(universo["status"], "DISPONIVEL")
                self.assertEqual(universo["eleitorado_apto"], aptos)

    def test_nao_existe_universo_consolidado(self):
        for proibido in (
            "obter_universo_eleitoral_pesquisa",
            "consolidar_universo",
            "universo_consolidado",
            "somar_universos",
        ):
            with self.subTest(funcao=proibido):
                self.assertFalse(hasattr(service, proibido))

    # --- multitenancy --------------------------------------------------------

    def test_tenant_b_nao_le_universo_de_a(self):
        self._definir(30, [self.bairro_a.id])
        with self.assertRaises(HTTPException) as erro:
            self._universo(30, usuario=self.gerente_b)
        self.assertEqual(erro.exception.status_code, 404)

    def test_tenant_a_nao_le_universo_de_b(self):
        with self.assertRaises(HTTPException) as erro:
            self._universo(40, projeto_id=200, pesquisa_id=20)
        self.assertEqual(erro.exception.status_code, 404)

    def test_base_do_outro_tenant_nao_influencia_o_calculo(self):
        self._definir(30, [self.bairro_a.id])
        universo = self._universo()
        # A base usada e a principal do projeto de A, jamais a de B.
        self.assertEqual(universo["base_eleitoral_id"], self.base.id)
        self.assertNotEqual(universo["base_eleitoral_id"], self.base_b.id)

    # --- performance ---------------------------------------------------------

    def test_quantidade_de_queries_nao_cresce_com_os_bairros(self):
        from sqlalchemy import event as sa_event

        def contar(setor_id, ids):
            self._definir(setor_id, ids)
            contagem = []
            engine = self.session.get_bind()

            def _registrar(*_args, **_kwargs):
                contagem.append(1)

            sa_event.listen(engine, "before_cursor_execute", _registrar)
            try:
                self.session.expire_all()
                self._universo(setor_id)
            finally:
                sa_event.remove(engine, "before_cursor_execute", _registrar)
            return len(contagem)

        # Setores OPERACAO: podem repetir bairro sem disparar a exclusividade,
        # entao a unica variavel entre as duas medicoes e a quantidade.
        com_um = contar(32, [self.bairro_a.id])
        com_tres = contar(33, [self.bairro_a.id, self.bairro_b.id, self.bairro_c.id])
        # Constante: sem N+1 por territorio.
        self.assertEqual(com_um, com_tres)
        self.assertLessEqual(com_tres, 5)


class UniversoEleitoralApiTests(_ComposicaoFixture):
    ROTA = "/projetos/100/pesquisas/10/setores/30/universo-eleitoral"

    def test_contrato_disponivel(self):
        self._definir(30, [self.bairro_a.id, self.bairro_b.id])
        resposta = self._cliente(self.gerente_a).get(self.ROTA)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(
            resposta.json(),
            {
                "setor_id": 30,
                "status": "DISPONIVEL",
                "motivo_indisponibilidade": None,
                "base_eleitoral_id": self.base.id,
                "base_eleitoral_nome": "Base",
                "quantidade_territorios": 2,
                "eleitorado_apto": 10000,
            },
        )

    def test_contrato_sem_composicao(self):
        corpo = self._cliente(self.gerente_a).get(self.ROTA).json()
        self.assertEqual(corpo["status"], "SEM_COMPOSICAO_ELEITORAL")
        self.assertIsNone(corpo["eleitorado_apto"])
        self.assertEqual(corpo["quantidade_territorios"], 0)

    def test_setor_de_outro_tenant_responde_404(self):
        resposta = self._cliente(self.gerente_b).get(self.ROTA)
        self.assertEqual(resposta.status_code, 404)

    def test_rota_nao_aceita_base_nem_company_do_cliente(self):
        self._definir(30, [self.bairro_a.id])
        cliente = self._cliente(self.gerente_a)
        # Query string estranha nao muda a base usada no calculo.
        corpo = cliente.get(
            self.ROTA + f"?base_eleitoral_id={self.base_b.id}&company_id=20"
        ).json()
        self.assertEqual(corpo["base_eleitoral_id"], self.base.id)

    def test_contrato_nao_expoe_indicador_de_fase_futura(self):
        self._definir(30, [self.bairro_a.id])
        corpo = self._cliente(self.gerente_a).get(self.ROTA).json()
        self.assertEqual(
            set(corpo),
            {
                "setor_id",
                "status",
                "motivo_indisponibilidade",
                "base_eleitoral_id",
                "base_eleitoral_nome",
                "quantidade_territorios",
                "eleitorado_apto",
            },
        )


class EscopoUniversoTests(_ComposicaoFixture):
    """Guardas contra a fase crescer sozinha."""

    def test_servico_nao_calcula_indicador_de_fase_futura(self):
        proibidos = (
            "cobertura_percentual",
            "percentual_universo",
            "eleitores_cobertos",
            "eleitorado_coberto",
            "votos_validos_projetados",
            "pressao_cotas",
            "ocorrencias_meta",
            "ocorrencias_para_meta",
            "amostra_planejada",
        )
        import inspect

        fonte = inspect.getsource(service)
        for nome in proibidos:
            with self.subTest(indicador=nome):
                self.assertFalse(hasattr(service, nome))
                self.assertNotIn(nome, fonte)

    def test_nao_aplica_projecao_de_votos(self):
        import inspect

        fonte = inspect.getsource(service)
        # comparecimento x votos validos e transformacao de fase posterior.
        for termo in ("comparecimento_estimado", "percentual_votos_validos"):
            with self.subTest(termo=termo):
                self.assertNotIn(termo, fonte)

    def test_universo_nao_e_persistido(self):
        colunas = set(models.Setor.__table__.columns.keys())
        for proibida in ("eleitorado_apto", "universo_eleitoral", "universo"):
            with self.subTest(coluna=proibida):
                self.assertNotIn(proibida, colunas)
        tabelas = set(models.Base.metadata.tables)
        self.assertNotIn("setor_universo_eleitoral", tabelas)
        self.assertNotIn("universo_eleitoral_setor", tabelas)

    def test_schema_do_universo_nao_traz_projecao(self):
        campos = set(schemas.UniversoEleitoralSetorResponse.model_fields)
        for proibido in (
            "cobertura_percentual",
            "votos_validos_projetados",
            "eleitores_cobertos",
            "pressao_cotas",
        ):
            with self.subTest(campo=proibido):
                self.assertNotIn(proibido, campos)


if __name__ == "__main__":
    unittest.main()
