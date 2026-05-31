import os
import random
import sys
from datetime import datetime, timedelta, timezone

import requests


BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
PESQUISA_ID = int(os.getenv("PESQUISA_ID", "4"))
LOGIN_EMAIL = os.getenv("AGENTE_EMAIL", "rafhael.ferreira@pesquisa360.com")
LOGIN_PASSWORD = os.getenv("AGENTE_SENHA", "SenhaTemporaria#123")

LAT_MIN = 0.0364
LAT_MAX = 0.0468
LON_MIN = -51.1394
LON_MAX = -51.1304
TZ = timezone(timedelta(hours=-3))


def expand_distribution(distribution):
    values = []
    for value, count in distribution:
        values.extend([value] * count)
    random.shuffle(values)
    return values


def clamp(value, min_value, max_value):
    return max(min_value, min(max_value, value))


def random_location():
    return {
        "lat": round(random.uniform(LAT_MIN, LAT_MAX), 7),
        "lon": round(random.uniform(LON_MIN, LON_MAX), 7),
    }


def nearby_location(origin):
    return {
        "lat": round(clamp(origin["lat"] + random.uniform(-0.00025, 0.00025), LAT_MIN, LAT_MAX), 7),
        "lon": round(clamp(origin["lon"] + random.uniform(-0.00025, 0.00025), LON_MIN, LON_MAX), 7),
    }


def get_access_token(session):
    env_token = os.getenv("ACCESS_TOKEN")
    if env_token:
        return env_token

    response = session.post(
        f"{BASE_URL}/login/token",
        data={"username": LOGIN_EMAIL, "password": LOGIN_PASSWORD},
        timeout=15,
    )

    if response.status_code != 200:
        raise RuntimeError(
            "Login falhou. Informe ACCESS_TOKEN via variavel de ambiente "
            f"ou revise credenciais. Status {response.status_code}: {response.text[:300]}"
        )

    return response.json()["access_token"]


def build_payloads():
    governador = expand_distribution(
        [
            ("Candidato A", 43),
            ("Candidato B", 34),
            ("Candidato C", 23),
        ]
    )
    presidente = expand_distribution(
        [
            ("Candidato A", 39),
            ("Candidato B", 36),
            ("Candidato C", 25),
        ]
    )
    sexo = expand_distribution(
        [
            ("Masculino", 48),
            ("Feminino", 52),
        ]
    )
    faixa_etaria = expand_distribution(
        [
            ("16 a 21 anos", 18),
            ("22 a 35", 46),
            ("36 ou mais", 36),
        ]
    )
    escolaridade = expand_distribution(
        [
            ("Fundamental", 28),
            ("Médio", 44),
            ("Superior", 28),
        ]
    )

    payloads = []
    base_time = datetime.now(TZ).replace(microsecond=0)

    for index in range(100):
        data_inicio = base_time - timedelta(
            days=random.randint(0, 14),
            hours=random.randint(0, 8),
            minutes=random.randint(0, 59),
        )
        data_fim = data_inicio + timedelta(minutes=random.randint(4, 18), seconds=random.randint(0, 59))
        localizacao_inicio = random_location()

        payloads.append(
            {
                "data_inicio_coleta": data_inicio.isoformat(),
                "data_fim_coleta": data_fim.isoformat(),
                "localizacao_inicio": localizacao_inicio,
                "localizacao_fim": nearby_location(localizacao_inicio),
                "foi_offline": False,
                "respostas": [
                    {"pergunta_id": 13, "valor_resposta": governador[index]},
                    {"pergunta_id": 14, "valor_resposta": presidente[index]},
                    {"pergunta_id": 15, "valor_resposta": sexo[index]},
                    {"pergunta_id": 16, "valor_resposta": faixa_etaria[index]},
                    {"pergunta_id": 17, "valor_resposta": escolaridade[index]},
                ],
            }
        )

    return payloads


def main():
    random.seed()
    payloads = build_payloads()
    success = 0
    failures = []

    with requests.Session() as session:
        try:
            token = get_access_token(session)
        except Exception as exc:
            print(str(exc))
            return 1

        headers = {"Authorization": f"Bearer {token}"}
        url = f"{BASE_URL}/pesquisas/{PESQUISA_ID}/coletas/"

        for index, payload in enumerate(payloads, start=1):
            try:
                response = session.post(url, json=payload, headers=headers, timeout=20)
            except requests.RequestException as exc:
                failures.append({"index": index, "erro": str(exc)})
                continue

            if response.status_code == 201:
                success += 1
            else:
                failures.append(
                    {
                        "index": index,
                        "status": response.status_code,
                        "resposta": response.text[:500],
                    }
                )

    print(f"Total sucesso: {success}")
    print(f"Total falha: {len(failures)}")
    if failures:
        print("Detalhes das falhas:")
        for failure in failures:
            print(failure)

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
