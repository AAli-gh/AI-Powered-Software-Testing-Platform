import unittest

from notes import create_app


class NotesApiTest(unittest.TestCase):
    def setUp(self):
        self.client = create_app({"TESTING": True}).test_client()

    def test_empty_text_is_rejected(self):
        self.assertEqual(self.client.post("/notes", json={"text": " "}).status_code, 400)

    def test_created_note_is_listed(self):
        self.client.post("/notes", json={"text": "hello"})
        self.assertEqual(self.client.get("/notes").get_json()[0]["text"], "hello")


if __name__ == "__main__":
    unittest.main()
