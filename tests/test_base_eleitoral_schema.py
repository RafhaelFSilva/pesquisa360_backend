"""Testes estruturais da Base Eleitoral versionada (Fase 2).

Nao existem endpoints nesta fase: o schema e exercitado direto no banco, via
Alembic sobre SQLite, e via schemas Pydantic. O objetivo e fixar constraints,
indices parciais e o contrato de multitenancy antes de qualquer CRUD.
"""

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-base-eleitoral-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360 import schemas
from pesquisa360.db import models


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_alembic_upgrade(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "alembic upgrade head falhou:\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )


class _EleitoralFixture(unittest.TestCase):
    """Banco SQLite migrado + tenants A/B. Sem testes proprios."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="pesquisa360-base-eleitoral-"))
        cls.db_path = cls.temp_dir / "base_eleitoral.db"
        _run_alembic_upgrade(f"sqlite:///{cls.db_path.as_posix()}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def setUp(self):
        # SQLite ignora FK por padrao; sem o PRAGMA as FKs compostas nao seriam testadas.
        self.engine = create_engine(f"sqlite:///{self.db_path.as_posix()}")

        @event.listens_for(self.engine, "connect")
        def _preparar_conexao(dbapi_connection, _record):
            # SQLite ignora FK por padrao; sem isso as FKs compostas nao seriam testadas.
            dbapi_connection.execute("PRAGMA foreign_keys=ON")
            # GeoAlchemy2 embrulha a coluna em GeomFromEWKT() mesmo com valor NULL.
            # Mesmo padrao de stub ja usado por tests/test_configuracoes_relatorio_executivo.
            dbapi_connection.create_function("GeomFromEWKT", 1, lambda valor: valor)
            dbapi_connection.create_function("ST_GeomFromEWKT", 1, lambda valor: valor)
            dbapi_connection.create_function("AsEWKB", 1, lambda valor: valor)

        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)
        self._limpar()
        self._semear_tenants()

    def _limpar(self):
        for tabela in (
            "importacao_base_eleitoral",
            "projeto_base_eleitoral",
            "territorio_eleitoral",
            "base_eleitoral",
            "pesquisas",
            "projetos",
            "usuarios",
            "companies",
            "perfis",
        ):
            self.session.execute(text(f"DELETE FROM {tabela}"))
        self.session.commit()

    def _semear_tenants(self):
        """Company A (10) e Company B (20), cada uma com usuario e projeto."""
        self.session.execute(
            text("INSERT INTO perfis (id, nome, descricao) VALUES (1, 'Gerente', NULL)")
        )
        self.session.execute(
            text(
                "INSERT INTO companies (id, name, is_active) VALUES (10, 'Empresa A', 1),"
                " (20, 'Empresa B', 1)"
            )
        )
        self.session.execute(
            text(
                "INSERT INTO usuarios (id, email, nome, senha_hash, ativo, perfil_id, company_id)"
                " VALUES (1, 'a@a', 'A', 'x', 1, 1, 10), (2, 'b@b', 'B', 'x', 1, 1, 20)"
            )
        )
        self.session.execute(
            text(
                "INSERT INTO projetos (id, nome, status, data_inicio, coordenador_id, company_id)"
                " VALUES (100, 'Campanha A', 'Ativo', '2026-01-01', 1, 10),"
                " (200, 'Campanha B', 'Ativo', '2026-01-01', 2, 20)"
            )
        )
        self.session.commit()

    # -- helpers ---------------------------------------------------------------

    def _base(self, **overrides):
        dados = dict(
            nome="TSE Amapa 2026",
            ano=2026,
            uf="AP",
            fonte="TSE",
            versao="2026.1",
            data_referencia=date(2026, 1, 1),
            criado_por_id=1,
        )
        dados.update(overrides)
        return models.BaseEleitoral(**dados)

    def _territorio(self, base, **overrides):
        dados = dict(
            base_eleitoral_id=base.id,
            tipo="ESTADO",
            nome="Amapa",
            nome_normalizado="amapa",
        )
        dados.update(overrides)
        return models.TerritorioEleitoral(**dados)

    def _persistir(self, *objetos):
        for objeto in objetos:
            self.session.add(objeto)
        self.session.commit()
        return objetos[0] if len(objetos) == 1 else objetos

    def _esperar_integrity_error(self, *objetos):
        with self.assertRaises(IntegrityError):
            self._persistir(*objetos)
        self.session.rollback()


class BaseEleitoralSchemaTests(_EleitoralFixture):
    """Exercita o schema real produzido pela migration, nao por create_all."""

    # -- A. import -------------------------------------------------------------

    def test_models_e_schemas_importam(self):
        for nome in (
            "BaseEleitoral",
            "TerritorioEleitoral",
            "ProjetoBaseEleitoral",
            "ImportacaoBaseEleitoral",
        ):
            self.assertTrue(hasattr(models, nome), nome)
        for nome in (
            "StatusBaseEleitoral",
            "TipoTerritorioEleitoral",
            "BaseEleitoralCreate",
            "TerritorioEleitoralCreate",
            "ProjetoBaseEleitoralCreate",
            "ImportacaoBaseEleitoralResponse",
        ):
            self.assertTrue(hasattr(schemas, nome), nome)

    # -- B. metadados nao colide com `metadata` --------------------------------

    def test_metadados_nao_usa_atributo_reservado_metadata(self):
        # `metadata` pertence ao declarative_base; o campo do dominio e `metadados`.
        self.assertIn("metadados", models.TerritorioEleitoral.__table__.columns)
        self.assertNotIn("metadata", models.TerritorioEleitoral.__table__.columns)

        base = self._persistir(self._base())
        territorio = self._persistir(
            self._territorio(base, metadados={"pagina_fonte": 47, "valor_original": "30.507"})
        )
        self.session.expire_all()
        recarregado = self.session.get(models.TerritorioEleitoral, territorio.id)
        self.assertEqual(recarregado.metadados["pagina_fonte"], 47)

    # -- C/D. status e percentuais --------------------------------------------

    def test_status_invalido_e_rejeitado_pelo_banco(self):
        self._esperar_integrity_error(self._base(status="QUALQUER"))

    def test_status_invalido_e_rejeitado_pelo_schema(self):
        with self.assertRaises(ValidationError):
            schemas.BaseEleitoralCreate(
                nome="X",
                ano=2026,
                uf="AP",
                fonte="TSE",
                versao="1",
                data_referencia=date(2026, 1, 1),
                status="QUALQUER",
            )

    def test_percentuais_fora_do_intervalo_sao_rejeitados_pelo_banco(self):
        self._esperar_integrity_error(self._base(comparecimento_estimado=1.5))
        self._esperar_integrity_error(
            self._base(versao="2026.2", percentual_votos_validos=-0.1)
        )

    def test_percentuais_fora_do_intervalo_sao_rejeitados_pelo_schema(self):
        for campo in ("comparecimento_estimado", "percentual_votos_validos"):
            for valor in (-0.01, 1.01):
                with self.subTest(campo=campo, valor=valor):
                    with self.assertRaises(ValidationError):
                        schemas.BaseEleitoralCreate(
                            nome="X",
                            ano=2026,
                            uf="AP",
                            fonte="TSE",
                            versao="1",
                            data_referencia=date(2026, 1, 1),
                            **{campo: valor},
                        )

    def test_parametros_de_projecao_sao_persistidos_sem_calculo(self):
        base = self._persistir(
            self._base(comparecimento_estimado=0.785, percentual_votos_validos=0.92)
        )
        self.session.expire_all()
        recarregado = self.session.get(models.BaseEleitoral, base.id)
        self.assertEqual(float(recarregado.comparecimento_estimado), 0.785)
        self.assertEqual(float(recarregado.percentual_votos_validos), 0.92)
        # Nenhuma coluna de produto/projecao existe nesta fase.
        colunas = set(models.BaseEleitoral.__table__.columns.keys())
        self.assertNotIn("votos_validos_projetados", colunas)

    # -- E/F/G. regras da arvore ----------------------------------------------

    def test_estado_sem_parent_e_valido(self):
        base = self._persistir(self._base())
        territorio = self._persistir(self._territorio(base))
        self.assertIsNone(territorio.parent_id)

    def test_municipio_sem_parent_e_invalido(self):
        base = self._persistir(self._base())
        self._esperar_integrity_error(
            self._territorio(base, tipo="MUNICIPIO", nome="Macapa", nome_normalizado="macapa")
        )

    def test_municipio_sem_parent_e_invalido_no_schema(self):
        with self.assertRaises(ValidationError):
            schemas.TerritorioEleitoralCreate(
                base_eleitoral_id=1, tipo="MUNICIPIO", nome="Macapa"
            )

    def test_secao_sem_numero_e_invalida(self):
        base = self._persistir(self._base())
        estado = self._persistir(self._territorio(base))
        self._esperar_integrity_error(
            self._territorio(
                base,
                tipo="SECAO",
                nome="Secao 98",
                nome_normalizado="secao 98",
                parent_id=estado.id,
            )
        )

    def test_secao_sem_numero_e_invalida_no_schema(self):
        with self.assertRaises(ValidationError):
            schemas.TerritorioEleitoralCreate(
                base_eleitoral_id=1, tipo="SECAO", nome="Secao 98", parent_id=1
            )

    def test_tipo_territorial_invalido_e_rejeitado(self):
        base = self._persistir(self._base())
        self._esperar_integrity_error(self._territorio(base, tipo="REGIAO"))

    def test_eleitorado_negativo_e_invalido(self):
        base = self._persistir(self._base())
        self._esperar_integrity_error(self._territorio(base, eleitorado_apto=-1))
        self._esperar_integrity_error(self._territorio(base, eleitorado_apto_origem=-1))

    def test_eleitorado_divergente_preserva_valor_de_origem(self):
        # A fonte pode divergir entre resumo e detalhe: registramos, nao reconciliamos.
        base = self._persistir(self._base())
        territorio = self._persistir(
            self._territorio(
                base,
                eleitorado_apto=322066,
                eleitorado_apto_origem=322070,
                eleitorado_apto_divergente=True,
                status_validacao="EM_CONFERENCIA",
            )
        )
        self.session.expire_all()
        recarregado = self.session.get(models.TerritorioEleitoral, territorio.id)
        self.assertTrue(recarregado.eleitorado_apto_divergente)
        self.assertNotEqual(
            recarregado.eleitorado_apto, recarregado.eleitorado_apto_origem
        )

    # -- H. unicidade parcial de codigo ---------------------------------------

    def test_codigo_duplicado_na_mesma_base_e_tipo_e_invalido(self):
        base = self._persistir(self._base())
        self._persistir(self._territorio(base, codigo="16"))
        self._esperar_integrity_error(
            self._territorio(base, codigo="16", nome="Outro", nome_normalizado="outro")
        )

    def test_codigo_nulo_pode_repetir(self):
        base = self._persistir(self._base())
        self._persistir(self._territorio(base, codigo=None))
        self._persistir(
            self._territorio(base, codigo=None, nome="Outro", nome_normalizado="outro")
        )
        total = self.session.scalar(
            select(text("count(*)")).select_from(models.TerritorioEleitoral.__table__)
        )
        self.assertEqual(total, 2)

    def test_mesmo_codigo_em_bases_diferentes_e_valido(self):
        base_2026 = self._persistir(self._base())
        base_2028 = self._persistir(self._base(ano=2028, versao="2028.1"))
        self._persistir(self._territorio(base_2026, codigo="16"))
        self._persistir(self._territorio(base_2028, codigo="16"))

    # -- I. vinculo Projeto <-> Base ------------------------------------------

    def test_vinculo_projeto_base_duplicado_e_invalido(self):
        base = self._persistir(self._base())
        self._persistir(
            models.ProjetoBaseEleitoral(projeto_id=100, base_eleitoral_id=base.id)
        )
        self._esperar_integrity_error(
            models.ProjetoBaseEleitoral(
                projeto_id=100, base_eleitoral_id=base.id, principal=False
            )
        )

    def test_duas_bases_principais_no_mesmo_projeto_sao_invalidas(self):
        base_a = self._persistir(self._base())
        base_b = self._persistir(self._base(ano=2028, versao="2028.1"))
        self._persistir(
            models.ProjetoBaseEleitoral(
                projeto_id=100, base_eleitoral_id=base_a.id, principal=True
            )
        )
        self._esperar_integrity_error(
            models.ProjetoBaseEleitoral(
                projeto_id=100, base_eleitoral_id=base_b.id, principal=True
            )
        )

    def test_uma_principal_mais_historico_nao_principal_e_valido(self):
        base_a = self._persistir(self._base())
        base_b = self._persistir(self._base(ano=2028, versao="2028.1"))
        self._persistir(
            models.ProjetoBaseEleitoral(
                projeto_id=100, base_eleitoral_id=base_a.id, principal=True
            )
        )
        self._persistir(
            models.ProjetoBaseEleitoral(
                projeto_id=100, base_eleitoral_id=base_b.id, principal=False
            )
        )

    def test_pesquisa_nao_possui_vinculo_direto_com_base_eleitoral(self):
        # Projeto = campanha, Pesquisa = onda. O vinculo vive no Projeto.
        colunas_vinculo = set(models.ProjetoBaseEleitoral.__table__.columns.keys())
        self.assertNotIn("pesquisa_id", colunas_vinculo)
        colunas_pesquisa = set(models.Pesquisa.__table__.columns.keys())
        self.assertNotIn("base_eleitoral_id", colunas_pesquisa)

    # -- J/K/L/M. multitenancy da base ----------------------------------------

    def test_base_oficial_aceita_company_id_nulo(self):
        base = self._persistir(self._base(company_id=None))
        self.assertIsNone(base.company_id)

    def test_base_privada_aceita_company_id(self):
        base = self._persistir(self._base(company_id=10))
        self.assertEqual(base.company_id, 10)

    def test_duas_bases_oficiais_identicas_sao_invalidas(self):
        self._persistir(self._base(company_id=None))
        self._esperar_integrity_error(self._base(company_id=None, nome="Duplicada"))

    def test_bases_privadas_de_tenants_distintos_podem_coincidir(self):
        self._persistir(self._base(company_id=10))
        self._persistir(self._base(company_id=20))

    def test_mesma_company_nao_duplica_versao_privada(self):
        self._persistir(self._base(company_id=10))
        self._esperar_integrity_error(self._base(company_id=10, nome="Duplicada"))

    def test_oficial_e_privada_com_mesma_versao_coexistem(self):
        self._persistir(self._base(company_id=None))
        self._persistir(self._base(company_id=10))

    # -- N. integridade da arvore entre bases ---------------------------------

    def test_parent_de_outra_base_e_rejeitado(self):
        base_a = self._persistir(self._base())
        base_b = self._persistir(self._base(ano=2028, versao="2028.1"))
        estado_a = self._persistir(self._territorio(base_a))
        self._esperar_integrity_error(
            self._territorio(
                base_b,
                tipo="MUNICIPIO",
                nome="Macapa",
                nome_normalizado="macapa",
                parent_id=estado_a.id,
            )
        )

    def test_municipio_de_outra_base_e_rejeitado(self):
        base_a = self._persistir(self._base())
        base_b = self._persistir(self._base(ano=2028, versao="2028.1"))
        estado_a = self._persistir(self._territorio(base_a))
        municipio_a = self._persistir(
            self._territorio(
                base_a,
                tipo="MUNICIPIO",
                nome="Macapa",
                nome_normalizado="macapa",
                parent_id=estado_a.id,
            )
        )
        estado_b = self._persistir(self._territorio(base_b))
        self._esperar_integrity_error(
            self._territorio(
                base_b,
                tipo="BAIRRO",
                nome="Buritizal",
                nome_normalizado="buritizal",
                parent_id=estado_b.id,
                municipio_id=municipio_a.id,
            )
        )

    def test_arvore_dentro_da_mesma_base_e_valida(self):
        base = self._persistir(self._base())
        estado = self._persistir(self._territorio(base))
        municipio = self._persistir(
            self._territorio(
                base,
                tipo="MUNICIPIO",
                nome="Macapa",
                nome_normalizado="macapa",
                parent_id=estado.id,
            )
        )
        bairro = self._persistir(
            self._territorio(
                base,
                tipo="BAIRRO",
                nome="Buritizal",
                nome_normalizado="buritizal",
                parent_id=municipio.id,
                municipio_id=municipio.id,
                eleitorado_apto=30507,
            )
        )
        self.session.expire_all()
        recarregado = self.session.get(models.TerritorioEleitoral, bairro.id)
        self.assertEqual(recarregado.parent.id, municipio.id)
        self.assertEqual(recarregado.municipio.id, municipio.id)
        self.assertEqual(
            [filho.id for filho in self.session.get(models.TerritorioEleitoral, estado.id).children],
            [municipio.id],
        )

    # -- O. defaults JSON independentes ---------------------------------------

    def test_metadados_default_nao_e_compartilhado_entre_instancias(self):
        base = self._persistir(self._base())
        primeiro = self._persistir(self._territorio(base))
        segundo = self._persistir(
            self._territorio(base, nome="Outro", nome_normalizado="outro")
        )
        primeiro.metadados = {"pagina_fonte": 1}
        self.session.commit()
        self.session.expire_all()
        self.assertEqual(self.session.get(models.TerritorioEleitoral, segundo.id).metadados, {})

    def test_divergencias_default_nao_e_compartilhado_entre_instancias(self):
        base = self._persistir(self._base())
        primeira = self._persistir(
            models.ImportacaoBaseEleitoral(
                base_eleitoral_id=base.id,
                arquivo_origem="a.pdf",
                total_linhas=0,
                total_importadas=0,
                total_divergencias=0,
                executado_por_id=1,
            )
        )
        segunda = self._persistir(
            models.ImportacaoBaseEleitoral(
                base_eleitoral_id=base.id,
                arquivo_origem="b.pdf",
                total_linhas=0,
                total_importadas=0,
                total_divergencias=0,
                executado_por_id=1,
            )
        )
        primeira.divergencias = [{"pagina": 47}]
        self.session.commit()
        self.session.expire_all()
        self.assertEqual(
            self.session.get(models.ImportacaoBaseEleitoral, segunda.id).divergencias, []
        )

    def test_totais_negativos_na_importacao_sao_invalidos(self):
        base = self._persistir(self._base())
        for campo in ("total_linhas", "total_importadas", "total_divergencias"):
            with self.subTest(campo=campo):
                valores = dict(total_linhas=0, total_importadas=0, total_divergencias=0)
                valores[campo] = -1
                self._esperar_integrity_error(
                    models.ImportacaoBaseEleitoral(
                        base_eleitoral_id=base.id,
                        arquivo_origem="a.pdf",
                        executado_por_id=1,
                        **valores,
                    )
                )

    # -- P. timestamps timezone-aware -----------------------------------------

    def test_timestamps_sao_timezone_aware(self):
        for tabela, colunas in (
            (models.BaseEleitoral, ("criado_em", "atualizado_em")),
            (models.TerritorioEleitoral, ("criado_em", "atualizado_em")),
            (models.ProjetoBaseEleitoral, ("vinculado_em",)),
            (models.ImportacaoBaseEleitoral, ("executado_em",)),
        ):
            for coluna in colunas:
                with self.subTest(tabela=tabela.__tablename__, coluna=coluna):
                    self.assertTrue(tabela.__table__.columns[coluna].type.timezone)

    # -- cascata da versao -----------------------------------------------------

    def test_remover_base_remove_territorios_e_importacoes(self):
        base = self._persistir(self._base())
        self._persistir(self._territorio(base))
        self._persistir(
            models.ImportacaoBaseEleitoral(
                base_eleitoral_id=base.id,
                arquivo_origem="a.pdf",
                total_linhas=1,
                total_importadas=1,
                total_divergencias=0,
                executado_por_id=1,
            )
        )
        self.session.execute(
            text("DELETE FROM base_eleitoral WHERE id = :id"), {"id": base.id}
        )
        self.session.commit()
        for tabela in ("territorio_eleitoral", "importacao_base_eleitoral"):
            with self.subTest(tabela=tabela):
                restantes = self.session.execute(
                    text(f"SELECT count(*) FROM {tabela}")
                ).scalar()
                self.assertEqual(restantes, 0)


class VisibilidadeBaseEleitoralTests(_EleitoralFixture):
    """Contrato A x B da futura camada de leitura (Fase 3), sem endpoint."""

    @staticmethod
    def _filtro_visibilidade(company_id: int):
        # Expressao canonica: base oficial (NULL) mais a base privada do proprio
        # tenant. Fixa o contrato para impedir um futuro `SELECT * base_eleitoral`.
        return (
            models.BaseEleitoral.company_id.is_(None)
            | (models.BaseEleitoral.company_id == company_id)
        )

    def _bases_visiveis(self, projeto_id: int):
        company_id = self.session.execute(
            text("SELECT company_id FROM projetos WHERE id = :id"), {"id": projeto_id}
        ).scalar()
        return set(
            self.session.scalars(
                select(models.BaseEleitoral.id).where(self._filtro_visibilidade(company_id))
            ).all()
        )

    def _cenario_tres_bases(self):
        oficial = self._persistir(self._base(company_id=None, nome="Oficial"))
        privada_a = self._persistir(self._base(company_id=10, nome="Privada A"))
        privada_b = self._persistir(self._base(company_id=20, nome="Privada B"))
        return oficial, privada_a, privada_b

    def test_projeto_a_enxerga_oficial_e_privada_a(self):
        oficial, privada_a, privada_b = self._cenario_tres_bases()
        visiveis = self._bases_visiveis(100)
        self.assertIn(oficial.id, visiveis)
        self.assertIn(privada_a.id, visiveis)
        self.assertNotIn(privada_b.id, visiveis)

    def test_projeto_b_enxerga_oficial_e_privada_b(self):
        oficial, privada_a, privada_b = self._cenario_tres_bases()
        visiveis = self._bases_visiveis(200)
        self.assertIn(oficial.id, visiveis)
        self.assertIn(privada_b.id, visiveis)
        self.assertNotIn(privada_a.id, visiveis)

    def test_select_sem_filtro_vazaria_entre_tenants(self):
        # Prova por contraste: sem o filtro, a base privada de B aparece para A.
        _oficial, _privada_a, privada_b = self._cenario_tres_bases()
        todas = set(self.session.scalars(select(models.BaseEleitoral.id)).all())
        self.assertIn(privada_b.id, todas)
        self.assertNotIn(privada_b.id, self._bases_visiveis(100))

    def test_schema_do_cliente_nao_aceita_company_id(self):
        # A regra de ouro: o tenant nunca vem do payload.
        self.assertNotIn("company_id", schemas.BaseEleitoralCreate.model_fields)
        self.assertNotIn("company_id", schemas.BaseEleitoralResponse.model_fields)
        with self.assertRaises(ValidationError):
            schemas.BaseEleitoralCreate(
                nome="X",
                ano=2026,
                uf="AP",
                fonte="TSE",
                versao="1",
                data_referencia=date(2026, 1, 1),
                company_id=20,
            )
        # O schema interno, usado por servico/importador, aceita explicitamente.
        interno = schemas.BaseEleitoralCreateInterno(
            nome="X",
            ano=2026,
            uf="ap",
            fonte="TSE",
            versao="1",
            data_referencia=date(2026, 1, 1),
            company_id=20,
            criado_por_id=1,
        )
        self.assertEqual(interno.company_id, 20)
        self.assertEqual(interno.uf, "AP")

    def test_projeto_base_create_nao_aceita_projeto_id_do_cliente(self):
        self.assertNotIn("projeto_id", schemas.ProjetoBaseEleitoralCreate.model_fields)


class RegressaoEscopoFase2Tests(unittest.TestCase):
    """Garante que a Fase 2 nao invadiu o territorio das fases seguintes."""

    def test_nivel_territorial_dos_cruzamentos_permanece_apenas_setor(self):
        self.assertEqual(
            [item.value for item in schemas.NivelTerritorial], ["SETOR"]
        )

    def test_composicao_eleitoral_do_setor_e_associativa(self):
        # A tabela chegou na Fase 3A.2, depois de o diagnostico provar que
        # BAIRRO e a menor unidade confiavel. Continua NAO sendo rateio: o
        # vinculo e por unidade inteira, declarado pelo usuario.
        self.assertTrue(hasattr(models, "SetorTerritorioEleitoral"))
        self.assertIn("setor_territorio_eleitoral", set(models.Base.metadata.tables))
        colunas = set(models.SetorTerritorioEleitoral.__table__.columns.keys())
        # Sem company_id (tenant vem do setor) e sem percentual/peso: nao ha
        # fracao de bairro.
        self.assertEqual(
            colunas, {"id", "setor_id", "territorio_eleitoral_id", "criado_em"}
        )

    def test_setor_continua_sem_vinculo_eleitoral_automatico(self):
        # A associacao Setor <-> Bairro permanece administrada pelo usuario e
        # vive na tabela associativa; nao ha inferencia espacial nem coluna
        # eleitoral dentro de Setor.
        colunas = set(models.Setor.__table__.columns.keys())
        self.assertNotIn("territorio_eleitoral_id", colunas)
        self.assertNotIn("base_eleitoral_id", colunas)

    def test_legado_eleitoral_permanece_intacto(self):
        bairros = models.Bairro.__table__.columns.keys()
        self.assertEqual(
            set(bairros),
            {"id", "nome", "area_ha", "populacao", "eleitores", "geometria", "company_id"},
        )
        locais = models.LocalVotacao.__table__.columns.keys()
        self.assertEqual(
            set(locais),
            {
                "id",
                "nome",
                "zona",
                "secoes",
                "municipio",
                "bairro",
                "endereco",
                "localizacao",
                "company_id",
            },
        )

    def test_setor_nao_ganhou_vinculo_eleitoral(self):
        colunas = set(models.Setor.__table__.columns.keys())
        self.assertNotIn("territorio_eleitoral_id", colunas)
        self.assertNotIn("base_eleitoral_id", colunas)


class MigrationBaseEleitoralTests(unittest.TestCase):
    """Verifica o que a migration realmente produziu em SQLite."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="pesquisa360-base-eleitoral-mig-"))
        cls.db_path = cls.temp_dir / "migration.db"
        _run_alembic_upgrade(f"sqlite:///{cls.db_path.as_posix()}")
        connection = sqlite3.connect(cls.db_path)
        try:
            cls.ddl = {
                row[0]: row[1]
                for row in connection.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type = 'table'"
                )
            }
            cls.indexes = {
                row[0]: row[1]
                for row in connection.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type = 'index'"
                )
            }
        finally:
            connection.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_tabelas_da_base_eleitoral_existem(self):
        for tabela in (
            "base_eleitoral",
            "territorio_eleitoral",
            "projeto_base_eleitoral",
            "importacao_base_eleitoral",
        ):
            self.assertIn(tabela, self.ddl)

    def test_tabelas_legadas_permanecem(self):
        for tabela in ("bairros", "locais_votacao", "setores", "coletas"):
            self.assertIn(tabela, self.ddl)

    def test_check_geometrico_postgis_nao_e_criado_em_sqlite(self):
        # GeometryType() nao existe em SQLite: o CHECK e exclusivo do PostgreSQL.
        self.assertNotIn("GeometryType", self.ddl["territorio_eleitoral"])

    def test_checks_portaveis_estao_presentes(self):
        territorio = self.ddl["territorio_eleitoral"]
        for check in (
            "ck_territorio_tipo",
            "ck_territorio_status_validacao",
            "ck_territorio_raiz",
            "ck_territorio_secao",
            "ck_territorio_eleitorado_apto",
            "ck_territorio_eleitorado_origem",
        ):
            self.assertIn(check, territorio)
        base = self.ddl["base_eleitoral"]
        for check in (
            "ck_base_eleitoral_status",
            "ck_base_eleitoral_comparecimento",
            "ck_base_eleitoral_votos_validos",
        ):
            self.assertIn(check, base)
        importacao = self.ddl["importacao_base_eleitoral"]
        for check in (
            "ck_importacao_total_linhas",
            "ck_importacao_total_importadas",
            "ck_importacao_total_divergencias",
        ):
            self.assertIn(check, importacao)

    def test_fks_compostas_amarram_a_mesma_base(self):
        territorio = self.ddl["territorio_eleitoral"]
        self.assertIn("fk_territorio_parent_mesma_base", territorio)
        self.assertIn("fk_territorio_municipio_mesma_base", territorio)
        self.assertIn("uq_territorio_eleitoral_id_base", territorio)

    def test_indices_esperados_existem(self):
        for indice in (
            "uq_base_eleitoral_oficial",
            "uq_base_eleitoral_privada",
            "ix_base_eleitoral_uf_ano",
            "ix_base_eleitoral_company_id",
            "ix_base_eleitoral_status",
            "ix_territorio_base_tipo",
            "ix_territorio_parent",
            "ix_territorio_municipio",
            "ix_territorio_nome_norm",
            "ix_territorio_zona",
            "uq_territorio_base_tipo_codigo",
            "ix_projeto_base_projeto",
            "uq_projeto_base_principal",
        ):
            self.assertIn(indice, self.indexes)

    def test_indices_parciais_carregam_a_clausula_where(self):
        self.assertIn("company_id IS NULL", self.indexes["uq_base_eleitoral_oficial"])
        self.assertIn("company_id IS NOT NULL", self.indexes["uq_base_eleitoral_privada"])
        self.assertIn("codigo IS NOT NULL", self.indexes["uq_territorio_base_tipo_codigo"])
        self.assertIn("principal", self.indexes["uq_projeto_base_principal"])

    def test_metadados_e_divergencias_usam_json_em_sqlite(self):
        self.assertIn("metadados JSON", self.ddl["territorio_eleitoral"])
        self.assertIn("divergencias JSON", self.ddl["importacao_base_eleitoral"])

    def test_geometria_e_nullable(self):
        self.assertIn("geometria", self.ddl["territorio_eleitoral"])
        self.assertNotIn("geometria BLOB NOT NULL", self.ddl["territorio_eleitoral"])


if __name__ == "__main__":
    unittest.main()
