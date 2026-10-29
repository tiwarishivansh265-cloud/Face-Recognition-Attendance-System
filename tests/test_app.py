

import unittest

from app import app


class FRAMSTestCase(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-key"
        self.client = app.test_client()

    def test_login_page_loads(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Welcome Back", response.data)

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "ok")

    def test_admin_dashboard_requires_login(self):
        response = self.client.get(
            "/admin-dashboard",
            follow_redirects=False
        )
        self.assertEqual(response.status_code, 302)

    def test_logout_redirects_to_login(self):
        response = self.client.get(
            "/logout",
            follow_redirects=False
        )
        self.assertEqual(response.status_code, 302)


if __name__ == "__main__":
    unittest.main()
