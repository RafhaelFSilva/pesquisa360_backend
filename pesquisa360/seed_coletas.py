import random
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from pesquisa360.db import models
from pesquisa360.db.database import SessionLocal
from geoalchemy2.shape import from_shape
from shapely.geometry import Point

# Limites geográficos aproximados de Santana - AP
# (Bounding Box retangular cobrindo a área urbana)
MIN_LON, MAX_LON = -51.190, -51.150
MIN_LAT, MAX_LAT = -0.060, -0.020

def gerar_mock_coletas(db: Session, pesquisa_id: int, agente_id: int, pergunta_id: int, qtd: int = 500):
    candidatos = ["Candidato A", "Candidato B", "Candidato C", "Branco/Nulo"]
    # Probabilidades viciadas para criar "Bolsões" de votos no mapa
    pesos = [0.40, 0.35, 0.15, 0.10] 

    print(f"🚀 Gerando {qtd} coletas falsas em Santana-AP...")

    coletas_criadas = 0
    for _ in range(qtd):
        # 1. Gera coordenadas aleatórias dentro de Santana
        lon = random.uniform(MIN_LON, MAX_LON)
        lat = random.uniform(MIN_LAT, MAX_LAT)
        ponto_gps = from_shape(Point(lon, lat), srid=4326)

        # 2. Simula o metadado da coleta (Mobile)
        # Espalha as datas nos últimos 7 dias, em horários aleatórios
        data_simulada = datetime.now() - timedelta(days=random.randint(0, 7), hours=random.randint(0, 23))

        nova_coleta = models.Coleta(
            data_hora=data_simulada,
            localizacao=ponto_gps,
            status_sincronizacao="sincronizado",
            inconformidade_localizacao=False,
            pesquisa_id=pesquisa_id,
            agente_id=agente_id
        )
        db.add(nova_coleta)
        db.flush() # Grava temporariamente para obtermos o ID gerado (coleta_id)

        # 3. Simula a Resposta do Eleitor
        voto_escolhido = random.choices(candidatos, weights=pesos)[0]

        nova_resposta = models.Resposta(
            valor_resposta=voto_escolhido,
            pergunta_id=pergunta_id,
            coleta_id=nova_coleta.id
        )
        db.add(nova_resposta)

        coletas_criadas += 1

    # Confirma tudo no banco de uma vez (alta performance)
    db.commit()
    print(f"✅ Sucesso! {coletas_criadas} coletas e respostas inseridas no banco de dados.")


if __name__ == "__main__":
    db = SessionLocal()
    try:
        # Busca o primeiro agente, pesquisa e pergunta do banco para atrelar os dados
        agente = db.query(models.Usuario).first()
        pesquisa = db.query(models.Pesquisa).first()
        pergunta = db.query(models.Pergunta).first()

        if not agente or not pesquisa or not pergunta:
            print("⚠️ ERRO: Para rodar o gerador, você precisa ter criado pelo menos 1 Usuário, 1 Pesquisa e 1 Pergunta no sistema.")
        else:
            gerar_mock_coletas(db, pesquisa.id, agente.id, pergunta.id, qtd=500)
    finally:
        db.close()