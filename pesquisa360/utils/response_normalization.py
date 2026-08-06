import re
import unicodedata


def normalizar_resposta_espontanea(valor: str | None) -> str:
    if valor is None or not isinstance(valor, str):
        return ""

    texto = valor
    texto = texto.strip().casefold()
    if not texto:
        return ""

    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[\W_]+", " ", texto, flags=re.UNICODE)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto
