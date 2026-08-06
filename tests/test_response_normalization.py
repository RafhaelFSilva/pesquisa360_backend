import unittest

from pesquisa360.utils.response_normalization import normalizar_resposta_espontanea


class ResponseNormalizationTests(unittest.TestCase):
    def test_normalizes_case_accents_spaces_and_simple_punctuation(self):
        self.assertEqual(normalizar_resposta_espontanea(" Clécio  "), "clecio")
        self.assertEqual(normalizar_resposta_espontanea("DR. FURLAN"), "dr furlan")
        self.assertEqual(normalizar_resposta_espontanea("Não   sei"), "nao sei")
        self.assertEqual(normalizar_resposta_espontanea("Clécio-Luís"), "clecio luis")
        self.assertEqual(normalizar_resposta_espontanea("  JOÃO, da Silva!  "), "joao da silva")

    def test_handles_empty_none_and_unexpected_values_defensively(self):
        self.assertEqual(normalizar_resposta_espontanea(""), "")
        self.assertEqual(normalizar_resposta_espontanea("   "), "")
        self.assertEqual(normalizar_resposta_espontanea(None), "")
        self.assertEqual(normalizar_resposta_espontanea(123), "")

    def test_is_deterministic_for_common_unicode_names(self):
        entrada = "  Ângela D’Ávila  "
        esperado = "angela d avila"
        self.assertEqual(normalizar_resposta_espontanea(entrada), esperado)
        self.assertEqual(normalizar_resposta_espontanea(entrada), esperado)


    def test_preserves_expected_ascii_after_unicode_normalization(self):
        self.assertEqual(normalizar_resposta_espontanea("Clécio Luís"), "clecio luis")
        self.assertEqual(normalizar_resposta_espontanea("Não sabe/Indeciso"), "nao sabe indeciso")
        self.assertEqual(normalizar_resposta_espontanea("João d'Ávila"), "joao d avila")
        self.assertEqual(normalizar_resposta_espontanea("François-é/çãí"), "francois e cai")


if __name__ == "__main__":
    unittest.main()
