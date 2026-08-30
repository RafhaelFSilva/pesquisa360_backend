"""Base Eleitoral x ADR-034: o tenant pertence ao PROJETO, nao a quem olha.

Reproduz o bug real: Superadmin (empresa principal = Admin) abrindo um Projeto
da Empresa A via 'Nenhuma Base vinculada' e modal vazio, porque o servico
filtrava a Base por `current_user.company_id`. A regra fixada aqui:

    ACL do usuario   -> decide se ele acessa o Projeto
    Projeto.company  -> decide quais Bases servem ao Projeto

Reaproveita o setup de `BaseEleitoralApiTests` (SQLite migrado) e acrescenta
Superadmin de outra empresa e ACL cruzada; os testes herdados sao desligados.
"""
import unittest

from sqlalchemy import text

from pesquisa360.db import models
from tests.test_base_eleitoral_api import BaseEleitoralApiTests

EMPRESA_A, EMPRESA_B, EMPRESA_ADMIN = 10, 20, 30
GERENTE_A, GERENTE_B, SUPER, GERENTE_MULTI = 1, 2, 3, 4
PROJETO_A1, PROJETO_B, PROJETO_A2 = 100, 200, 101


class BaseEleitoralMultiempresaTests(BaseEleitoralApiTests):
    def _semear(self):
        super()._semear()
        self.session.execute(text(
            "INSERT INTO companies (id, name, is_active) VALUES (30, 'Pesquisa360 Admin', 1)"))
        self.session.execute(text(
            "INSERT INTO usuarios (id, email, nome, senha_hash, ativo, perfil_id, company_id) VALUES"
            " (3, 'root@admin', 'Superadmin', 'x', 1, 1, 30),"
            " (4, 'multi@a', 'Gerente Multi', 'x', 1, 2, 10)"))
        self.session.execute(text(
            "INSERT INTO projetos (id, nome, status, data_inicio, coordenador_id, company_id)"
            " VALUES (101, 'Campanha A2', 'Ativo', '2026-01-01', 1, 10)"))
        # ADR-034: Gerente Multi tem empresa principal A (todos os projetos) e
        # ACL explicita SOMENTE no Projeto B, que e da Empresa B.
        self.session.execute(text(
            "INSERT INTO usuario_empresa_acessos (usuario_id, company_id, acesso_todos_projetos, ativo, principal)"
            " VALUES (4, 10, 1, 1, 1), (4, 20, 0, 1, 0)"))
        self.session.execute(text(
            "INSERT INTO usuario_projeto_acessos (usuario_id, projeto_id, ativo) VALUES (4, 200, 1)"))
        self.session.commit()
        self.superadmin = self.session.get(models.Usuario, SUPER)
        self.gerente_multi = self.session.get(models.Usuario, GERENTE_MULTI)
        # Projeto A1 ja nasce com a Privada A como principal (como o Projeto 14 do DEV).
        self.session.add(models.ProjetoBaseEleitoral(
            projeto_id=PROJETO_A1, base_eleitoral_id=self.privada_a.id, principal=True))
        self.session.commit()

    def _limpar(self):
        for tabela in ("usuario_projeto_acessos", "usuario_empresa_acessos"):
            self.session.execute(text(f"DELETE FROM {tabela}"))
        super()._limpar()

    def base_do_projeto(self, usuario, projeto_id):
        r = self._cliente(usuario).get(f"/projetos/{projeto_id}/base-eleitoral")
        return r.status_code, (r.json() if r.status_code == 200 else r.text)

    def candidatas(self, usuario, projeto_id):
        r = self._cliente(usuario).get(f"/projetos/{projeto_id}/bases-eleitorais-disponiveis")
        return r.status_code, (sorted(b["nome"] for b in r.json()) if r.status_code == 200 else r.text)

    def vincular(self, usuario, projeto_id, base_id):
        return self._cliente(usuario).post(f"/projetos/{projeto_id}/base-eleitoral/{base_id}/vincular").status_code

    # --- 1/2: invariancia do card ------------------------------------------------
    def test_01_gerente_ve_base_vinculada_do_proprio_projeto(self):
        status, corpo = self.base_do_projeto(self.usuario_a, PROJETO_A1)
        self.assertEqual(status, 200)
        self.assertTrue(corpo["principal"])
        self.assertEqual((corpo["base"]["id"], corpo["base"]["nome"]), (self.privada_a.id, "Privada A"))

    def test_02_superadmin_de_outra_empresa_ve_a_MESMA_base(self):
        self.assertNotEqual(self.superadmin.company_id, EMPRESA_A, "premissa: Superadmin e de outro tenant")
        _, gerente = self.base_do_projeto(self.usuario_a, PROJETO_A1)
        status, superadmin = self.base_do_projeto(self.superadmin, PROJETO_A1)
        self.assertEqual(status, 200)
        chaves = ("id", "nome", "versao", "status", "eh_oficial")
        self.assertEqual({k: superadmin["base"][k] for k in chaves}, {k: gerente["base"][k] for k in chaves})
        self.assertEqual(superadmin["principal"], gerente["principal"])

    # --- 3/4: Gerente multiempresa (ADR-034) ------------------------------------
    def test_03_usuario_principal_A_com_acl_em_projeto_B_ve_base_B(self):
        self.session.add(models.ProjetoBaseEleitoral(
            projeto_id=PROJETO_B, base_eleitoral_id=self.privada_b.id, principal=True))
        self.session.commit()
        status, corpo = self.base_do_projeto(self.gerente_multi, PROJETO_B)
        self.assertEqual(status, 200, corpo)
        self.assertEqual(corpo["base"]["id"], self.privada_b.id)
        status, nomes = self.candidatas(self.gerente_multi, PROJETO_B)
        self.assertEqual((status, nomes), (200, ["Oficial", "Privada B"]))

    def test_04_empresa_principal_do_usuario_nao_torna_base_A_candidata_no_projeto_B(self):
        _, nomes = self.candidatas(self.gerente_multi, PROJETO_B)
        self.assertNotIn("Privada A", nomes, "tenant do Projeto > tenant do usuario")
        self.assertEqual(self.vincular(self.gerente_multi, PROJETO_B, self.privada_a.id), 404)

    # --- 5/6: candidatas -------------------------------------------------------
    def test_05_candidatas_sao_oficiais_mais_privadas_do_tenant_do_projeto(self):
        for usuario in (self.usuario_a, self.superadmin):
            status, nomes = self.candidatas(usuario, PROJETO_A2)
            self.assertEqual((status, nomes), (200, ["Oficial", "Privada A"]), usuario.email)

    def test_06_privada_de_outro_tenant_nao_aparece_nem_para_superadmin(self):
        _, nomes = self.candidatas(self.superadmin, PROJETO_A2)
        self.assertNotIn("Privada B", nomes)
        _, nomes = self.candidatas(self.superadmin, PROJETO_B)
        self.assertEqual(nomes, ["Oficial", "Privada B"])

    # --- 7/8: vinculo ---------------------------------------------------------
    def test_07_superadmin_vincula_base_privada_do_tenant_do_projeto(self):
        self.assertEqual(self.vincular(self.superadmin, PROJETO_A2, self.privada_a.id), 200)
        self.assertEqual(self.vincular(self.superadmin, PROJETO_A2, self.oficial.id), 200)
        _, corpo = self.base_do_projeto(self.usuario_a, PROJETO_A2)
        self.assertEqual(corpo["base"]["id"], self.oficial.id, "Gerente ve o que o Superadmin vinculou")

    def test_08_vinculo_recusado_quando_tenant_diverge_inclusive_superadmin(self):
        self.assertEqual(self.vincular(self.superadmin, PROJETO_A2, self.privada_b.id), 404)
        self.assertEqual(self.vincular(self.usuario_a, PROJETO_A1, self.privada_b.id), 404)
        self.assertEqual(self.session.query(models.ProjetoBaseEleitoral).filter_by(
            base_eleitoral_id=self.privada_b.id).count(), 0, "nada persistido")

    # --- 9/10 ---------------------------------------------------------------------
    def test_09_projeto_nao_autorizado_continua_404_no_card_e_nas_candidatas(self):
        self.assertEqual(self.base_do_projeto(self.usuario_b, PROJETO_A1)[0], 404)
        self.assertEqual(self.candidatas(self.usuario_b, PROJETO_A1)[0], 404)
        self.assertEqual(self.candidatas(self.gerente_multi, PROJETO_A2)[0], 200, "empresa principal A: todos os projetos de A")
        self.assertEqual(self.candidatas(self.usuario_a, PROJETO_B)[0], 404)

    def test_10_mesma_base_reutilizada_em_dois_projetos(self):
        self.assertEqual(self.vincular(self.superadmin, PROJETO_A2, self.privada_a.id), 200)
        for projeto in (PROJETO_A1, PROJETO_A2):
            for usuario in (self.usuario_a, self.superadmin):
                _, corpo = self.base_do_projeto(usuario, projeto)
                self.assertEqual(corpo["base"]["id"], self.privada_a.id, (projeto, usuario.email))

    def test_11_vinculo_inconsistente_no_banco_nao_exibe_base_de_outro_tenant(self):
        # Defesa: vinculo gravado fora da API apontando para base de outro tenant.
        self.session.execute(text(
            f"UPDATE projeto_base_eleitoral SET base_eleitoral_id={self.privada_b.id} WHERE projeto_id={PROJETO_A1}"))
        self.session.commit()
        self.assertEqual(self.base_do_projeto(self.usuario_a, PROJETO_A1)[0], 404)
        self.assertEqual(self.base_do_projeto(self.superadmin, PROJETO_A1)[0], 404)

    def test_13_territorios_da_base_do_projeto_sao_legiveis_por_quem_acessa_o_projeto(self):
        # Superadmin (empresa Admin) e Gerente Multi (principal A, ACL no Projeto B)
        # leem territorios/detalhe da Base usada pelo Projeto; escrita continua do dono.
        self.session.add(models.ProjetoBaseEleitoral(
            projeto_id=PROJETO_B, base_eleitoral_id=self.privada_b.id, principal=True))
        self.session.commit()
        for usuario, base in ((self.superadmin, self.privada_a), (self.superadmin, self.privada_b), (self.gerente_multi, self.privada_b)):
            c = self._cliente(usuario)
            self.assertEqual(c.get(f"/base-eleitoral/{base.id}/territorios", params={"tipo": "MUNICIPIO"}).status_code, 200, usuario.email)
            self.assertEqual(c.get(f"/base-eleitoral/{base.id}").status_code, 200)
        # Sem ACL no Projeto, base privada de outro tenant segue invisivel (404).
        self.assertEqual(self._cliente(self.usuario_a).get(f"/base-eleitoral/{self.privada_b.id}/territorios").status_code, 404)
        # Gerente Multi nao ganha ESCRITA na base B por ler o Projeto B.
        self.assertEqual(self._cliente(self.gerente_multi).post(f"/base-eleitoral/{self.privada_b.id}/validar").status_code, 404)

    def test_12_servico_nao_filtra_base_do_projeto_pelo_tenant_do_usuario(self):
        import inspect

        from pesquisa360.services import base_eleitoral as service

        import re

        def codigo(funcao):   # sem docstring/comentarios: citar o termo nao e usa-lo
            fonte = re.sub(r'"""[\s\S]*?"""', "", inspect.getsource(funcao))
            return re.sub(r"#.*", "", fonte)

        fonte = codigo(service.obter_base_principal_projeto_opcional)
        self.assertNotIn("current_user.company_id", fonte)
        fonte = codigo(service.vincular_base_eleitoral_ao_projeto)
        self.assertNotIn("obter_base_eleitoral_visivel", fonte)
        self.assertIn("obter_base_eleitoral_elegivel_para_projeto", fonte)


for _nome in [n for n in dir(BaseEleitoralApiTests) if n.startswith("test_")]:
    setattr(BaseEleitoralMultiempresaTests, _nome, None)


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(BaseEleitoralMultiempresaTests)


if __name__ == "__main__":
    unittest.main()
