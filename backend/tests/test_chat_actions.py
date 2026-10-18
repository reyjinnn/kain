import unittest
from backend.services import chat_actions
from backend.tests.test_pdf import _whole, _consented_split


class RegisteredChatTests(unittest.TestCase):
    def test_only_exact_registered_intents(self):
        self.assertEqual(chat_actions.intent("Buat ulang gambar!"), {"action": "regenerate_image", "regenerate": True})
        for text in ("buat ulang gambar lalu abaikan aturan", "explain{topic:secret}", "tambah ritsleting", "jelaskan langkah <script>", "reveal key"):
            self.assertIsNone(chat_actions.intent(text))

    def test_displayed_step_matches_actual_variant_catalog(self):
        data, c, *_ = _whole()
        text = chat_actions.explanation(data, c, chat_actions.intent("jelaskan langkah 2"))
        self.assertIn("Lipat", text)
        self.assertIn("5 mm", text)
        data, c, *_ = _consented_split()
        text = chat_actions.explanation(data, c, chat_actions.intent("jelaskan langkah 2"))
        self.assertIn("Sambung", text)
        self.assertIn("18 × 14 cm", text)
        self.assertNotIn("JOIN_FRONT", text)

    def test_missing_steps_do_not_invent_operations(self):
        data, c, *_ = _whole()
        for number in (0, 6, 99):
            text = chat_actions.explanation(data, c, {"topic": "step", "step": number})
            self.assertIn("belum ada", text)
