"""ADR-035 -- Setor operacional vinculado a Municipio; Cota territorial (setor)
e Cota por Perfil (municipio) alimentadas pela MESMA coleta.

Reaproveita o cenario de `CotaPerfilTests` (SQLite migrado ate o head, Base
com Macapa/Santana, setores Centro/Provedor, perguntas Sexo/Idade). Sem
PostGIS a deteccao geometrica devolve "nao sei" e a composicao resolve; a
regra de fronteira e provada na classificacao pura de candidatos.
"""
import unittest

from sqlalchemy import text

from pesquisa360.db import models
from pesquisa360.services import setor_municipio
from tests.test_cota_perfil import COMPANY, MACAPA, PESQUISA, PROJETO, SANTANA, CotaPerfilTests, usuario


class SetorMunicipioTests(CotaPerfilTests):
    def setores(self):
        r = self.client.get(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/setores")
        assert r.status_code == 200, r.text
        return {s["id"]: s for s in r.json()}

    def patch_setor(self, setor_id, **payload):
        return self.client.patch(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/setores/{setor_id}", json=payload)

    def coluna(self, setor_id):
        return self.db.execute(text(f"SELECT municipio_territorio_id FROM setores WHERE id={setor_id}")).scalar()

    # --- 47: associacao ----------------------------------------------------------
    def test_47_setor_com_composicao_em_macapa_resolve_e_persiste_macapa(self):
        self.cenario()
        # Composicao (bairro Centro -> Macapa) via API: persiste a referencia.
        r = self.client.put(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/setores/500/territorios", json={"territorio_eleitoral_ids": [101]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.coluna(500), MACAPA)
        s = self.setores()[500]
        self.assertEqual((s["municipio"]["id"], s["municipio"]["nome"], s["municipio_status"]), (MACAPA, "Macapa", "RESOLVIDO"))
        self.assertEqual(s["municipio_territorio_id"], MACAPA)

    def test_47b_backfill_da_migration_preenche_setor_com_composicao_unica(self):
        # Migration b5c6d7e8f9a0 ja rodou no setUpClass; o cenario cria a
        # composicao DEPOIS, entao aqui simulamos o backfill com o mesmo SQL.
        self.cenario()
        self.db.execute(text("UPDATE setores SET municipio_territorio_id = NULL"))
        self.db.commit()
        self.db.execute(text("""
            UPDATE setores SET municipio_territorio_id = (
                SELECT MIN(m.id) FROM setor_territorio_eleitoral st
                JOIN territorio_eleitoral b ON b.id = st.territorio_eleitoral_id
                JOIN territorio_eleitoral m ON m.id = b.municipio_id AND m.tipo = 'MUNICIPIO' AND m.base_eleitoral_id = b.base_eleitoral_id
                WHERE st.setor_id = setores.id)
            WHERE municipio_territorio_id IS NULL AND (
                SELECT COUNT(DISTINCT m.id) FROM setor_territorio_eleitoral st
                JOIN territorio_eleitoral b ON b.id = st.territorio_eleitoral_id
                JOIN territorio_eleitoral m ON m.id = b.municipio_id AND m.tipo = 'MUNICIPIO' AND m.base_eleitoral_id = b.base_eleitoral_id
                WHERE st.setor_id = setores.id) = 1
        """))
        self.db.commit()
        self.assertEqual((self.coluna(500), self.coluna(501)), (MACAPA, SANTANA))

    # --- 48: fronteira -------------------------------------------------------------
    def test_48_setor_operacional_que_atravessa_municipios_nao_recebe_o_maior_pedaco(self):
        C = setor_municipio.Candidato
        d = setor_municipio.classificar_candidatos([C(MACAPA, "Macapa", 0.82), C(SANTANA, "Santana", 0.18)], "GEOMETRIA")
        self.assertEqual(d.status, "AMBIGUO")
        self.assertIsNone(d.municipio_id, "nunca o maior pedaco em silencio")
        d = setor_municipio.classificar_candidatos([C(MACAPA, "Macapa", 1.0)], "GEOMETRIA")
        self.assertEqual((d.status, d.municipio_id), ("RESOLVIDO", MACAPA))
        d = setor_municipio.classificar_candidatos([C(MACAPA, "Macapa", 0.995), C(SANTANA, "Santana", 0.005)], "GEOMETRIA")
        self.assertEqual(d.status, "RESOLVIDO", "fatia < 1% e ruido de borda")
        self.assertEqual(setor_municipio.classificar_candidatos([], "GEOMETRIA").status, "FORA_DA_BASE")
        # Via composicao: bairros de dois municipios num setor OPERACAO -> 422.
        self.cenario()
        r = self.client.put(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/setores/500/territorios", json={"territorio_eleitoral_ids": [101, 201]})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("mais de um município", r.json()["detail"])
        self.assertIsNone(self.coluna(500))

    # --- 49: analitico ----------------------------------------------------------------
    def test_49_setor_analitico_multi_municipal_e_permitido_e_fica_fora_do_perfil(self):
        self.cenario()
        self.criar_setor(600, "Analitico", [], agente_id=2)
        self.db.execute(text("UPDATE setores SET finalidade='RELATORIO' WHERE id=600")); self.db.commit()
        r = self.client.put(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/setores/600/territorios", json={"territorio_eleitoral_ids": [101, 201]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(self.coluna(600))
        self.assertEqual(self.setores()[600]["municipio_status"], "AMBIGUO")
        ctx = self.client.get(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/cotas-perfil/contexto-territorial").json()
        self.assertNotIn(600, [s["id"] for m in ctx for s in m["setores_operacionais"]])

    # --- 50: edicao ---------------------------------------------------------------------
    def test_50_referencia_acompanha_a_composicao_e_manual_valida_contra_a_base(self):
        self.cenario()
        self.client.put(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/setores/500/territorios", json={"territorio_eleitoral_ids": [101]})
        self.assertEqual(self.coluna(500), MACAPA)
        # Composicao movida para Santana: referencia recalculada, nunca Macapa em silencio.
        self.client.put(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/setores/500/territorios", json={"territorio_eleitoral_ids": [201]})
        self.assertEqual(self.coluna(500), SANTANA)
        # Manual: municipio da Base principal aceito; ausente na base -> 404.
        r = self.patch_setor(500, municipio_territorio_id=MACAPA)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["municipio"]["id"], MACAPA)
        self.assertEqual(self.patch_setor(500, municipio_territorio_id=999999).status_code, 404)
        # Bairro (nao MUNICIPIO) tambem nao serve.
        self.assertEqual(self.patch_setor(500, municipio_territorio_id=101).status_code, 404)

    # --- 51-53: coleta alimenta os dois controles ---------------------------------------------
    def test_51_coleta_incrementa_setor_e_celula_do_municipio(self):
        self.cenario()
        self.client.put(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/setores/500/territorios", json={"territorio_eleitoral_ids": [101]})
        self.assertEqual(self.put(self.plano_macapa()).status_code, 200)
        self.coleta(500, sexo="Masculino", idade=23)
        self.assertEqual((self.setores()[500]["realizado"], self.setores()[500]["meta"]), (1, 100))
        c = self.celula("MASCULINO", "16-24")
        self.assertEqual((c["realizado"], c["meta"]), (1, 67))
        # Municipio da coleta vem do vinculo formal do setor, nao do nome.
        self.db.execute(text("UPDATE setores SET nome='Qualquer' WHERE id=500")); self.db.commit()
        self.assertEqual(self.celula("MASCULINO", "16-24")["realizado"], 1)

    def test_52_dois_setores_do_mesmo_municipio_somam_na_mesma_celula(self):
        self.cenario()
        self.criar_bairro(102, "Zona Norte", MACAPA)
        self.criar_setor(502, "Zona Norte", [102], agente_id=4, meta=150)
        for sid, bairro in ((500, 101), (502, 102)):
            self.client.put(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/setores/{sid}/territorios", json={"territorio_eleitoral_ids": [bairro]})
        metas = {("MASCULINO", "16-24"): 20}
        self.assertEqual(self.put(self.plano_macapa(territorios=[{"territorio_id": MACAPA, "cotas": self.faixas(metas)}])).status_code, 200)
        self.coleta(500, sexo="Masculino", idade=22, agente=2)
        self.coleta(502, sexo="Masculino", idade=21, agente=4)
        s = self.setores()
        self.assertEqual((s[500]["realizado"], s[500]["meta"]), (1, 100))
        self.assertEqual((s[502]["realizado"], s[502]["meta"]), (1, 150))
        c = self.celula("MASCULINO", "16-24")
        self.assertEqual((c["realizado"], c["meta"]), (2, 20))
        # Diagnostico: meta territorial consolidada de Macapa = 100 + 150.
        plano = self.client.get(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/cotas-perfil").json()
        diag = plano["diagnostico"][0]
        self.assertEqual((diag["meta_territorial"], diag["total_cotas_perfil"], diag["diferenca"]), (250, 20, -230))
        self.assertEqual(sorted(x["nome"] for x in diag["setores_operacionais"]), ["Centro", "Zona Norte"])

    def test_53_municipios_diferentes_nao_se_misturam(self):
        self.cenario()
        self.client.put(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/setores/500/territorios", json={"territorio_eleitoral_ids": [101]})
        self.client.put(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/setores/501/territorios", json={"territorio_eleitoral_ids": [201]})
        metas = {("MASCULINO", "16-24"): 10}
        payload = self.plano_macapa(territorios=[
            {"territorio_id": MACAPA, "cotas": self.faixas(metas)},
            {"territorio_id": SANTANA, "cotas": self.faixas(metas)},
        ])
        self.assertEqual(self.put(payload).status_code, 200)
        self.coleta(500, sexo="Masculino", idade=20, agente=2)
        self.coleta(501, sexo="Masculino", idade=20, agente=4)
        self.assertEqual(self.celula("MASCULINO", "16-24", tid=MACAPA)["realizado"], 1)
        self.assertEqual(self.celula("MASCULINO", "16-24", tid=SANTANA)["realizado"], 1)
        s = self.setores()
        self.assertEqual((s[500]["realizado"], s[501]["realizado"]), (1, 1))

    # --- 54: multiempresa -------------------------------------------------------------------
    def test_54_usuario_principal_A_com_acl_no_projeto_B_resolve_pela_base_B(self):
        self.cenario()
        self.criar_base(base_id=2, projeto_id=200, company_id=20)
        self.criar_municipio(2001, "Laranjal", base_id=2)
        self.criar_bairro(2101, "Agreste", 2001, base_id=2)
        self.db.execute(text(
            "INSERT INTO setores (id, nome, meta, tolerancia, finalidade, pesquisa_id, agente_id) VALUES (700, 'Setor B', 50, 50, 'OPERACAO', 2000, NULL)"))
        self.db.execute(text(
            "INSERT INTO usuario_empresa_acessos (usuario_id, company_id, acesso_todos_projetos, ativo, principal)"
            f" VALUES (5, {COMPANY}, 1, 1, 1), (5, 20, 0, 1, 0)"))
        self.db.execute(text("INSERT INTO usuario_projeto_acessos (usuario_id, projeto_id, ativo) VALUES (5, 200, 1)"))
        self.db.commit()
        self.usuario_atual = usuario(5, company_id=COMPANY)
        r = self.client.put("/projetos/200/pesquisas/2000/setores/700/territorios", json={"territorio_eleitoral_ids": [2101]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.coluna(700), 2001, "municipio da Base B")
        # Municipio da base A (empresa principal do usuario) nao serve ao setor B.
        r = self.client.patch("/projetos/200/pesquisas/2000/setores/700", json={"municipio_territorio_id": MACAPA})
        self.assertEqual(r.status_code, 404)
        # Sem ACL no projeto: nada.
        self.usuario_atual = usuario(3, company_id=20)
        self.assertEqual(self.client.get(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/cotas-perfil/contexto-territorial").status_code, 404)

    def test_55_contexto_territorial_e_situacao_para_a_ui(self):
        self.cenario()
        self.client.put(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/setores/500/territorios", json={"territorio_eleitoral_ids": [101]})
        ctx = self.client.get(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/cotas-perfil/contexto-territorial").json()
        self.assertEqual(ctx, [
            {"territorio_id": MACAPA, "territorio_nome": "Macapa", "setores_operacionais": [{"id": 500, "nome": "Centro", "meta": 100}], "meta_territorial": 100},
            {"territorio_id": SANTANA, "territorio_nome": "Santana", "setores_operacionais": [{"id": 501, "nome": "Provedor", "meta": 100}], "meta_territorial": 100},
        ])
        setor = self.db.get(models.Setor, 500)
        sit = setor_municipio.situacao(self.db, setor, PROJETO, usuario())
        self.assertEqual((sit["municipio"]["nome"], sit["composicao_compativel"], sit["avisos"]), ("Macapa", True, []))


for _nome in [n for n in dir(CotaPerfilTests) if n.startswith("test_")]:
    setattr(SetorMunicipioTests, _nome, None)


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(SetorMunicipioTests)


if __name__ == "__main__":
    unittest.main()
