"""Apuracao TSE, fase 2: recorte territorial na Central e no Majoritario,
distribuicao territorial em lote e paineis do tipo DISTRIBUICAO_TERRITORIAL.

Base: fixtures SIMULADO_* (Deputado Federal, AP, Macapa e suas 3 zonas). Os
demais cargos e municipios sao CLONES desses arquivos, com `sqcand` prefixado
pelo cargo e votos em escala conhecida -- o suficiente para provar que cada
recorte le o seu proprio arquivo.
"""

import copy

from sqlalchemy import event

from pesquisa360.db import models
from pesquisa360.db.models_tse import TseTotalizacao

from tests.test_apuracao_api import _ApiFixture
from tests.test_tse_persistencia import BASE, CANDIDATO, DADOS, ESCOPO

ESTADUAL, FEDERAL = "21272", "21270"
CONFIG = f"{BASE}/simulado2026/ele2026"
# cargo -> (eleicao, nome, vagas, fator dos votos)
CARGOS = {
    "0001": (FEDERAL, "Presidente", "1", 5),
    "0003": (ESTADUAL, "Governador", "1", 3),
    "0005": (ESTADUAL, "Senador", "2", 4),
    "0007": (ESTADUAL, "Deputado Estadual", "24", 2),
}
# municipio clonado -> divisor dos votos de Macapa
MUNICIPIOS_CLONE = {"06157": 2, "06009": 4}
FEDERACAO, PARTIDO_ISOLADO = "100", "58"


def _escalar(doc, fator=1, divisor=1, prefixo="", cargo=None):
    doc = copy.deepcopy(doc)
    carg = doc["carg"][0]
    if cargo:
        carg.update({"cd": str(int(cargo)), "nmn": CARGOS[cargo][1], "nv": CARGOS[cargo][2]})
        doc["ele"] = CARGOS[cargo][0]
    for agr in carg["agr"]:
        for par in agr["par"]:
            par["tvtl"] = str(int(par["tvtl"]) * fator // divisor)
            for cand in par["cand"]:
                cand["sqcand"] = prefixo + cand["sqcand"]
                cand["vap"] = str(int(cand["vap"]) * fator // divisor)
    for chave in ("vv", "vnom", "vl"):
        doc["v"][chave] = str(int(doc["v"][chave]) * fator // divisor)
    return doc


class _Fase2Fixture(_ApiFixture):
    def setUp(self):
        super().setUp()
        arquivos = self.tse.arquivos
        # Municipios extras do Deputado Federal (clones de Macapa, em escala).
        macapa = arquivos[f"{DADOS}/ap/ap06050-c0006-e0{ESTADUAL}-u.json"]
        for codigo, divisor in MUNICIPIOS_CLONE.items():
            clone = _escalar(macapa, divisor=divisor)
            clone["cdabr"] = codigo
            arquivos[f"{DADOS}/ap/ap{codigo}-c0006-e0{ESTADUAL}-u.json"] = clone
        # A eleicao federal (Presidente) tem EA12/EA14/EA15 proprios.
        for sufixo in ("config/mun-e0{e}-cm.json", "dados/br/br-e0{e}-ab.json",
                       "dados/ap/ap-e0{e}-ab.json"):
            origem = arquivos[f"{CONFIG}/{ESTADUAL}/{sufixo.format(e=ESTADUAL)}"]
            clone = copy.deepcopy(origem)
            if "ele" in clone:
                clone["ele"] = FEDERAL
            arquivos[f"{CONFIG}/{FEDERAL}/{sufixo.format(e=FEDERAL)}"] = clone
        # Demais cargos: clones do Federal, na eleicao certa.
        for url, doc in list(arquivos.items()):
            if "-c0006-" not in url:
                continue
            for cargo, (eleicao, _nome, _vagas, fator) in CARGOS.items():
                destino = url.replace("-c0006-", f"-c{cargo}-").replace(ESTADUAL, eleicao)
                arquivos[destino] = _escalar(doc, fator=fator, prefixo=str(int(cargo)),
                                             cargo=cargo)
        self.ingerir(type(ESCOPO)(**{
            **ESCOPO.__dict__, "cargos": ("0001", "0003", "0005", "0006", "0007"),
            "municipios": ("06050", "06157", "06009")}))
        self.estadual = self.repo.get_eleicao("SIMULADO", "17801", ESTADUAL).id
        self.federal = self.repo.get_eleicao("SIMULADO", "17801", FEDERAL).id

    def get(self, rota, eleicao=None, **params):
        return self.client.get(f"/apuracao/tse/eleicoes/{eleicao or self.estadual}/{rota}",
                               params={"uf": "ap", **params})

    def ok(self, rota, eleicao=None, **params):
        r = self.get(rota, eleicao, **params)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()


# ============================================================== CENTRAL
class ResumoPorRecorteTest(_Fase2Fixture):
    def secoes(self, corpo):
        return {c["codigo"]: (c["totalizacao"]["secoes_totalizadas"],
                              c["totalizacao"]["secoes_total"]) for c in corpo["cargos"]}

    def test_1_uf_sem_filtro_mantem_o_contrato(self):
        uf = self.ok("resumo")
        self.assertEqual(sorted(uf), ["abrangencia", "cargos", "eleicao", "origem",
                                      "totalizacao", "uf", "ultima_atualizacao"])
        self.assertEqual(uf["abrangencia"]["tipo"], "UF")
        self.assertEqual(self.secoes(uf), {c: (2177, 2177) for c in ("0003", "0005", "0006", "0007")})
        self.assertEqual(self.secoes(self.ok("resumo", self.federal)), {"0001": (2177, 2177)})

    def test_2_3_municipio_e_zona_trazem_o_universo_do_recorte_em_todos_os_cargos(self):
        mun = self.ok("resumo", municipio="06050")
        self.assertEqual((mun["abrangencia"]["tipo"], mun["abrangencia"]["municipio_nome"]),
                         ("MUNICIPIO", "MACAPÁ"))
        self.assertEqual(self.secoes(mun), {c: (1071, 1071) for c in ("0003", "0005", "0006", "0007")})
        zona = self.ok("resumo", municipio="06050", zona="0002")
        self.assertEqual(zona["abrangencia"]["zona"], "0002")
        self.assertEqual(self.secoes(zona), {c: (395, 395) for c in ("0003", "0005", "0006", "0007")})
        # Votos validos e comparecimento tambem sao os do recorte, nao os da UF.
        validos = lambda corpo: next(c for c in corpo["cargos"] if c["codigo"] == "0006")[
            "totalizacao"]["votos_validos"]
        self.assertLess(validos(zona), validos(mun))
        self.assertLess(validos(mun), validos(self.ok("resumo")))
        self.assertEqual(zona["totalizacao"]["idg"], "176247751")
        self.assertEqual(self.secoes(self.ok("resumo", self.federal, municipio="06050",
                                             zona="0010")), {"0001": (255, 255)})

    def test_4_5_recorte_invalido_e_404_e_secao_segue_a_hierarquia(self):
        self.assertEqual(self.get("resumo", municipio="99999").status_code, 404)
        self.assertEqual(self.get("resumo", municipio="06050", zona="0001").status_code, 404)
        self.assertEqual(self.get("resumo", municipio="06157", zona="0002").status_code, 404)
        self.assertEqual(self.get("resumo", zona="0002").status_code, 422)
        # Fase 3: a secao e aceita. Existe no EA16 e nao tem BU ingerido -> 200 sem resultado.
        r = self.get("resumo", municipio="06050", zona="0002", secao="0055")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["secao"]["status"], r.json()["cargos"]), ("AGUARDANDO_BU", []))
        r = self.get("resumo", municipio="06050", secao="0055")            # secao sem zona
        self.assertEqual(r.status_code, 422)
        self.assertIn("Seção", r.json()["detail"])
        self.assertEqual(self.get("resumo", municipio="06050", zona="0002",
                                  secao="9999").status_code, 404)


# =========================================================== MAJORITARIO
class MajoritarioPorRecorteTest(_Fase2Fixture):
    def test_6_a_10_presidente_governador_e_senador_por_municipio_e_zona(self):
        for cargo, (eleicao, nome, vagas, fator) in CARGOS.items():
            if cargo == "0007":
                continue
            eid = self.federal if eleicao == FEDERAL else self.estadual
            sq = str(int(cargo)) + CANDIDATO
            esperado = {(): 3115, ("06050",): 1612, ("06050", "0002"): 676,
                        ("06050", "0010"): 427, ("06050", "0014"): 509}
            secoes = {(): 2177, ("06050",): 1071, ("06050", "0002"): 395,
                      ("06050", "0010"): 255, ("06050", "0014"): 421}
            for recorte, votos in esperado.items():
                filtro = dict(zip(("municipio", "zona"), recorte))
                corpo = self.ok(f"cargos/{cargo}", eid, **filtro)
                self.assertEqual((corpo["cargo"]["nome"], str(corpo["cargo"]["vagas"])),
                                 (nome, vagas))
                alvo = next(c for c in corpo["candidatos"] if c["sqcand"] == sq)
                self.assertEqual(alvo["votos"], votos * fator, (cargo, recorte))
                t = corpo["totalizacao"]
                self.assertEqual((t["secoes_totalizadas"], t["secoes_total"]),
                                 (secoes[recorte], secoes[recorte]), (cargo, recorte))
                ordem = [c["votos"] for c in corpo["candidatos"]]
                self.assertEqual(ordem, sorted(ordem, reverse=True))
            # Secao sem BU: 200, sem totalizacao e sem candidatos (nunca zero voto).
            sem_bu = self.get(f"cargos/{cargo}", eid, municipio="06050", zona="0002",
                              secao="0055")
            self.assertEqual((sem_bu.status_code, sem_bu.json()["totalizacao"],
                              sem_bu.json()["candidatos"]), (200, None, []))

    def test_candidato_no_recorte(self):
        url = f"/apuracao/tse/candidatos/{CANDIDATO}"
        base = {"eleicao_id": self.estadual}
        uf = self.client.get(url, params=base).json()
        mun = self.client.get(url, params={**base, "municipio": "06050"}).json()
        zona = self.client.get(url, params={**base, "municipio": "06050", "zona": "0014"}).json()
        self.assertEqual([c["consolidado"]["votos"] for c in (uf, mun, zona)], [3115, 1612, 509])
        self.assertEqual([c["abrangencia"]["tipo"] for c in (uf, mun, zona)],
                         ["UF", "MUNICIPIO", "ZONA"])
        self.assertEqual(zona["totalizacao"]["secoes_total"], 421)
        self.assertEqual(self.client.get(url, params={**base, "municipio": "06050",
                                                      "zona": "0001"}).status_code, 404)
        secao = self.client.get(url, params={**base, "municipio": "06050", "zona": "0002",
                                             "secao": "0055"})
        self.assertEqual((secao.status_code, secao.json()["consolidado"]), (200, None))


# ========================================================== DISTRIBUICAO
class DistribuicaoTerritorialTest(_Fase2Fixture):
    OUTRO = "41609564"       # candidato anulado sub judice, 2588 votos na UF

    def dist(self, cargo="0006", eleicao=None, **params):
        return self.ok(f"cargos/{cargo}/distribuicao", eleicao, **params)

    def partes(self, item):
        return {p["codigo"]: p for p in item["partes"]}

    def test_11_candidato_por_todos_os_municipios(self):
        corpo = self.dist(candidato=CANDIDATO)
        self.assertEqual((corpo["nivel"], corpo["abrangencia"]["tipo"]), ("municipios", "UF"))
        self.assertEqual([p["nome"] for p in corpo["partes"]], ["MACAPÁ", "PRACUÚBA", "SANTANA"])
        item = corpo["itens"][0]
        self.assertEqual((item["tipo"], item["id"], item["total_votos"], item["posicao"]),
                         ("CANDIDATO", CANDIDATO, 3115, 1))
        self.assertEqual({c: p["votos"] for c, p in self.partes(item).items()},
                         {"06050": 1612, "06157": 806, "06009": 403})
        # Cada parte carrega a propria totalizacao (secoes, IDG, horario).
        macapa = next(p for p in corpo["partes"] if p["codigo"] == "06050")
        self.assertEqual((macapa["totalizacao"]["secoes_total"], macapa["totalizacao"]["idg"]),
                         (1071, "176248737"))
        self.assertTrue(corpo["partes_podem_divergir"])
        self.assertTrue(corpo["secao_disponivel"])
        self.assertIsNone(corpo["cobertura"])              # so existe no nivel de secoes

    def test_12_dois_candidatos_em_lote(self):
        corpo = self.dist(candidato=[CANDIDATO, self.OUTRO])
        self.assertEqual([i["id"] for i in corpo["itens"]], [CANDIDATO, self.OUTRO])
        outro = corpo["itens"][1]
        self.assertEqual(outro["total_votos"], 2588)
        self.assertEqual(set(self.partes(outro)), {"06050", "06157", "06009"})
        self.assertGreater(outro["posicao"], 1)

    def test_13_14_15_nominatas_e_candidato_mais_nominata(self):
        nominatas = self.ok("cargos/0006/nominatas")["nominatas"]
        fed = next(n for n in nominatas if n["tipo"] == "FEDERACAO" and n["numero"] == FEDERACAO)
        par = next(n for n in nominatas if n["tipo"] == "PARTIDO" and n["numero"] == PARTIDO_ISOLADO)
        corpo = self.dist(candidato=CANDIDATO, federacao=FEDERACAO, partido=PARTIDO_ISOLADO)
        self.assertEqual([(i["tipo"], i["id"]) for i in corpo["itens"]], [
            ("CANDIDATO", CANDIDATO), ("FEDERACAO", f"FEDERACAO:{FEDERACAO}"),
            ("PARTIDO", f"PARTIDO:{PARTIDO_ISOLADO}")])
        item_fed, item_par = corpo["itens"][1], corpo["itens"][2]
        # Mesmo total da pagina Proporcional (nominais validos + legenda oficial).
        self.assertEqual(item_fed["total_votos"], fed["total"])
        self.assertEqual(item_par["total_votos"], par["total"])
        self.assertEqual(sorted(item_fed["partidos"]), sorted(p["sigla"] for p in fed["partidos"]))
        macapa = next(n for n in self.ok("cargos/0006/nominatas", municipio="06050")["nominatas"]
                      if n["tipo"] == "FEDERACAO" and n["numero"] == FEDERACAO)
        self.assertEqual(self.partes(item_fed)["06050"]["votos"], macapa["total"])
        self.assertIsNone(item_fed["posicao"])
        # Partido federado e resolvido para a nominata da federacao, sem duplicar.
        via_partido = self.dist(partido="69", federacao=FEDERACAO)
        self.assertEqual([i["id"] for i in via_partido["itens"]], [f"FEDERACAO:{FEDERACAO}"])

    def test_16_municipio_distribui_por_zonas(self):
        corpo = self.dist(candidato=CANDIDATO, federacao=FEDERACAO, municipio="06050")
        self.assertEqual((corpo["nivel"], corpo["abrangencia"]["tipo"]), ("zonas", "MUNICIPIO"))
        self.assertEqual([p["codigo"] for p in corpo["partes"]], ["0002", "0010", "0014"])
        self.assertEqual([p["nome"] for p in corpo["partes"]],
                         ["Zona 0002", "Zona 0010", "Zona 0014"])
        cand = corpo["itens"][0]
        self.assertEqual(cand["total_votos"], 1612)
        self.assertEqual({c: p["votos"] for c, p in self.partes(cand).items()},
                         {"0002": 676, "0010": 427, "0014": 509})
        self.assertEqual(cand["soma_das_partes"], 1612)
        fed = corpo["itens"][1]
        self.assertEqual(fed["soma_das_partes"], fed["total_votos"])

    def test_17_zona_abre_por_secao_e_sem_bu_nao_ha_voto(self):
        corpo = self.dist(candidato=CANDIDATO, municipio="06050", zona="0002")
        self.assertEqual((corpo["nivel"], corpo["abrangencia"]["zona"]), ("secoes", "0002"))
        # Dez urnas no EA16 da zona, nenhuma com BU ingerido nesta base.
        self.assertEqual(corpo["cobertura"], {"partes": 10, "com_resultado": 0})
        self.assertEqual({p["bu_status"] for p in corpo["partes"]}, {"AGUARDANDO_BU"})
        self.assertTrue(all(p["totalizacao"] is None for p in corpo["partes"]))
        item = corpo["itens"][0]
        # O total continua sendo o do EA20 da zona; sem BU as partes sao nulas, nunca zero.
        self.assertEqual((item["total_votos"], item["soma_das_partes"]), (676, None))
        self.assertEqual({p["votos"] for p in item["partes"]}, {None})
        self.assertEqual(corpo["totalizacao"]["secoes_total"], 395)
        self.assertTrue(corpo["secao_disponivel"])

    def test_18_19_dois_percentuais_distintos(self):
        corpo = self.dist(candidato=CANDIDATO, federacao=FEDERACAO, municipio="06050")
        cand, fed = corpo["itens"]
        zona = self.partes(cand)["0002"]
        # A) participacao da zona nos votos do candidato NO RECORTE (Macapa).
        self.assertEqual(zona["percentual_item"], round(100 * 676 / 1612, 2))
        self.assertEqual(round(sum(p["percentual_item"] for p in cand["partes"])), 100)
        # B) desempenho dentro da zona: o percentual publicado pelo TSE naquele arquivo.
        bruto = self.tse.arquivos[f"{DADOS}/ap/ap06050-z0002-c0006-e0{ESTADUAL}-u.json"]
        oficial = next(c for a in bruto["carg"][0]["agr"] for p in a["par"] for c in p["cand"]
                       if c["sqcand"] == CANDIDATO)
        self.assertEqual(zona["percentual_parte"], float(oficial["pvap"].replace(",", ".")))
        self.assertNotEqual(zona["percentual_item"], zona["percentual_parte"])
        # Nominata: B) = votos / votos validos da parte.
        parte = next(p for p in corpo["partes"] if p["codigo"] == "0002")
        z_fed = self.partes(fed)["0002"]
        self.assertEqual(z_fed["percentual_parte"],
                         round(100 * z_fed["votos"] / parte["totalizacao"]["votos_validos"], 2))
        self.assertEqual(z_fed["percentual_item"],
                         round(100 * z_fed["votos"] / fed["total_votos"], 2))

    def test_20_partes_em_janelas_diferentes_nao_sao_reconciliadas(self):
        item = self.dist(candidato=CANDIDATO)["itens"][0]
        # UF oficial = 3115; as partes ingeridas somam 2821: nada e ajustado.
        self.assertEqual((item["total_votos"], item["soma_das_partes"]), (3115, 2821))
        self.assertNotEqual(round(sum(p["percentual_item"] for p in item["partes"])), 100)
        # Uma parte atrasada continua com o seu proprio numero e a sua propria janela.
        url = f"{DADOS}/ap/ap06157-c0006-e0{ESTADUAL}-u.json"
        self.tse.arquivos = copy.deepcopy(self.tse.arquivos)
        self.tse.arquivos[url].update({"and": "p", "tf": "n", "idg": "555"})
        self.tse.arquivos[url]["s"]["st"] = "10"
        self.ingerir(type(ESCOPO)(**{**ESCOPO.__dict__, "municipios": ("06050", "06157", "06009"),
                                    "force": True}))
        corpo = self.dist(candidato=CANDIDATO)
        santana = next(p for p in corpo["partes"] if p["codigo"] == "06157")
        self.assertEqual((santana["totalizacao"]["andamento"], santana["totalizacao"]["idg"],
                          santana["totalizacao"]["secoes_totalizadas"]), ("p", "555", 10))
        self.assertEqual(corpo["totalizacao"]["andamento"], "f")
        self.assertEqual(self.partes(corpo["itens"][0])["06157"]["votos"], 806)

    def test_21_consulta_em_lote_sem_n_mais_1(self):
        consultas = []
        escuta = lambda *_a, **_k: consultas.append(1)
        event.listen(self.engine, "before_cursor_execute", escuta)
        try:
            um = self.dist(candidato=CANDIDATO)
            com_um = len(consultas)
            consultas.clear()
            varios = self.dist(candidato=[CANDIDATO, self.OUTRO], federacao=FEDERACAO,
                               partido=PARTIDO_ISOLADO)
            com_varios = len(consultas)
        finally:
            event.remove(self.engine, "before_cursor_execute", escuta)
        self.assertEqual(len(um["partes"]), 3)
        self.assertEqual(len(varios["itens"]), 4)
        # O numero de consultas nao cresce com itens nem com partes.
        self.assertLessEqual(com_um, 14)
        self.assertLessEqual(com_varios, 14)

    def test_22_secao_e_o_nivel_minimo_e_itens_invalidos(self):
        r = self.get("cargos/0006/distribuicao", candidato=CANDIDATO, municipio="06050",
                     zona="0002", secao="0055")
        self.assertEqual((r.status_code, r.json()["nivel"], r.json()["partes"]), (200, "secao", []))
        self.assertIsNone(r.json()["itens"][0]["total_votos"])            # sem BU: sem voto
        self.assertEqual(self.get("cargos/0006/distribuicao", candidato=CANDIDATO,
                                  municipio="06050", secao="0055").status_code, 422)
        self.assertEqual(self.get("cargos/0006/distribuicao").status_code, 422)      # sem itens
        self.assertEqual(self.get("cargos/0006/distribuicao", candidato="0").status_code, 404)
        # Candidato de outro cargo nao e aceito na distribuicao deste cargo.
        self.assertEqual(self.get("cargos/0005/distribuicao", candidato=CANDIDATO).status_code, 404)
        self.assertEqual(self.get("cargos/0006/distribuicao", federacao="999").status_code, 404)
        self.assertEqual(self.get("cargos/0006/distribuicao", partido="99").status_code, 404)
        self.assertEqual(self.get("cargos/0006/distribuicao", candidato=CANDIDATO,
                                  municipio="99999").status_code, 404)
        muitos = [str(n) for n in range(21)]
        self.assertEqual(self.get("cargos/0006/distribuicao", candidato=muitos).status_code, 422)

    def test_majoritario_e_estadual_tambem_distribuem(self):
        senado = self.dist("0005", candidato=["5" + CANDIDATO, "5" + self.OUTRO])
        self.assertEqual(senado["cargo"]["nome"], "Senador")
        self.assertEqual(self.partes(senado["itens"][0])["06050"]["votos"], 1612 * 4)
        presidente = self.dist("0001", self.federal, candidato="1" + CANDIDATO, municipio="06050")
        self.assertEqual({c: p["votos"] for c, p in self.partes(presidente["itens"][0]).items()},
                         {"0002": 676 * 5, "0010": 427 * 5, "0014": 509 * 5})

    def test_leitura_nao_altera_dados(self):
        antes = self.contagens()
        ultima = self.session.query(TseTotalizacao).order_by(TseTotalizacao.id.desc()).first().id
        self.dist(candidato=CANDIDATO, federacao=FEDERACAO)
        self.ok("resumo", municipio="06050")
        self.session.expire_all()
        self.assertEqual(self.contagens(), antes)
        self.assertEqual(
            self.session.query(TseTotalizacao).order_by(TseTotalizacao.id.desc()).first().id, ultima)


# =============================================================== PAINEIS
class PainelDeDistribuicaoTest(_Fase2Fixture):
    def corpo(self, **extra):
        return {"nome": "Deputados — Distribuição", "tipo": "DISTRIBUICAO_TERRITORIAL",
                "eleicao_id": self.estadual, "uf": "ap", "itens": [
                    {"tipo": "CANDIDATO", "cargo_codigo": "0006", "sqcand": CANDIDATO},
                    {"tipo": "NOMINATA", "cargo_codigo": "0006", "federacao_numero": FEDERACAO},
                ], **extra}

    def post(self, usuario=None, **extra):
        return self.como(usuario or self.gerente_a).post("/apuracao/paineis",
                                                        json=self.corpo(**extra))

    def test_23_a_26_criar_listar_atualizar_e_excluir(self):
        r = self.post()
        self.assertEqual(r.status_code, 201, r.text)
        painel = r.json()
        self.assertEqual((painel["tipo"], painel["total_itens"]), ("DISTRIBUICAO_TERRITORIAL", 2))
        self.assertEqual([(i["tipo"], i["sqcand"], i["federacao_numero"]) for i in painel["itens"]],
                         [("CANDIDATO", CANDIDATO, None), ("NOMINATA", None, FEDERACAO)])
        self.assertNotIn("company_id", painel)
        lista = self.client.get("/apuracao/paineis").json()
        self.assertEqual([(p["nome"], p["tipo"]) for p in lista],
                         [("Deputados — Distribuição", "DISTRIBUICAO_TERRITORIAL")])
        url = f"/apuracao/paineis/{painel['id']}"
        novo = self.corpo(nome="Senado — Distribuição Municipal", itens=[
            {"tipo": "CANDIDATO", "cargo_codigo": "0005", "sqcand": "5" + CANDIDATO},
            {"tipo": "CANDIDATO", "cargo_codigo": "0005", "sqcand": "541609564"}])
        r = self.client.put(url, json=novo)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["nome"], [i["cargo_codigo"] for i in r.json()["itens"]]),
                         ("Senado — Distribuição Municipal", ["0005", "0005"]))
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_painel_antigo_continua_geral(self):
        r = self.client.post("/apuracao/paineis", json=self.corpo_painel())     # sem `tipo`
        self.assertEqual((r.status_code, r.json()["tipo"]), (201, "GERAL"))
        self.assertEqual(self.session.get(models.ApuracaoPainel, r.json()["id"]).tipo, "GERAL")

    def test_27_tenant_a_nao_acessa_tenant_b(self):
        painel = self.post().json()
        b = self.como(self.gerente_b)
        url = f"/apuracao/paineis/{painel['id']}"
        self.assertEqual(b.get("/apuracao/paineis").json(), [])
        self.assertEqual([b.get(url).status_code, b.put(url, json=self.corpo()).status_code,
                          b.delete(url).status_code], [404, 404, 404])
        self.assertEqual(self.post(company_id=20).status_code, 422)
        self.assertEqual(self.session.get(models.ApuracaoPainel, painel["id"]).company_id, 10)

    def test_28_configuracao_invalida_e_rejeitada(self):
        cand = {"tipo": "CANDIDATO", "cargo_codigo": "0006", "sqcand": CANDIDATO}
        invalidas = {
            "sem acompanhados": [],
            "cargos diferentes": [cand, {"tipo": "NOMINATA", "cargo_codigo": "0007",
                                         "federacao_numero": FEDERACAO}],
            "item CARGO": [{"tipo": "CARGO", "cargo_codigo": "0006"}],
            "duplicado": [cand, cand],
            "nominata sem agremiacao": [{"tipo": "NOMINATA", "cargo_codigo": "0006"}],
            "nominata em cargo majoritario": [{"tipo": "NOMINATA", "cargo_codigo": "0005",
                                               "federacao_numero": FEDERACAO}],
            "acima do limite": [{"tipo": "CANDIDATO", "cargo_codigo": "0006", "sqcand": str(n)}
                                for n in range(21)],
        }
        for caso, itens in invalidas.items():
            self.assertEqual(self.post(itens=itens).status_code, 422, caso)
        self.assertEqual(self.post(tipo="MAPA").status_code, 422)
        self.assertEqual(self.session.query(models.ApuracaoPainel).count(), 0)

    def test_29_30_candidato_de_cargo_incompativel_e_nominata_inexistente(self):
        def itens(*lista):
            return self.post(itens=list(lista))

        r = itens({"tipo": "CANDIDATO", "cargo_codigo": "0005", "sqcand": CANDIDATO})
        self.assertEqual(r.status_code, 422)
        self.assertIn("não disputa o cargo", r.json()["detail"])
        self.assertEqual(itens({"tipo": "CANDIDATO", "cargo_codigo": "0006",
                                "sqcand": "999"}).status_code, 422)
        self.assertEqual(itens({"tipo": "NOMINATA", "cargo_codigo": "0006",
                                "federacao_numero": "999"}).status_code, 422)
        self.assertEqual(itens({"tipo": "NOMINATA", "cargo_codigo": "0006",
                                "partido_numero": "99"}).status_code, 422)
        # Presidente esta em outra eleicao do MESMO pleito: e aceito.
        self.assertEqual(itens({"tipo": "CANDIDATO", "cargo_codigo": "0001",
                                "sqcand": "1" + CANDIDATO}).status_code, 201)

    def test_cliente_le_mas_nao_cria(self):
        self.assertEqual(self.post(self.cliente_a).status_code, 403)
        painel = self.post().json()
        self.assertEqual(self.como(self.cliente_a).get(
            f"/apuracao/paineis/{painel['id']}").status_code, 200)
