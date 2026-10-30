"""Runtime smoke and helper tests for FRAMS."""
import unittest

from app import (
    app,
    distance_in_metres,
    validate_coordinates,
    decode_face_photo,
)


class FRAMSRuntimeTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-only-secret"
        self.client = app.test_client()

    def test_login_page_renders(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_protected_admin_page_redirects_when_logged_out(self):
        response = self.client.get("/admin-dashboard")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/", response.headers["Location"])

    def test_logout_clears_session_and_redirects(self):
        with self.client.session_transaction() as session:
            session["role"] = "admin"
            session["username"] = "test-admin"
        response = self.client.get("/logout")
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertNotIn("role", session)

    def test_gps_coordinates_accept_valid_values(self):
        validate_coordinates(19.0760, 72.8777)

    def test_gps_coordinates_reject_out_of_range_values(self):
        for latitude, longitude in ((91, 0), (0, 181), (-91, 0), (0, -181)):
            with self.subTest(latitude=latitude, longitude=longitude):
                with self.assertRaises(ValueError):
                    validate_coordinates(latitude, longitude)

    def test_gps_coordinates_reject_nan(self):
        with self.assertRaises(ValueError):
            validate_coordinates(float("nan"), 0.0)

    def test_distance_same_point_is_zero(self):
        self.assertAlmostEqual(
            distance_in_metres(19.0760, 72.8777, 19.0760, 72.8777),
            0.0,
            places=5,
        )

    def test_distance_is_symmetric_and_positive(self):
        a_to_b = distance_in_metres(19.0760, 72.8777, 19.0800, 72.8800)
        b_to_a = distance_in_metres(19.0800, 72.8800, 19.0760, 72.8777)
        self.assertGreater(a_to_b, 0)
        self.assertAlmostEqual(a_to_b, b_to_a, places=5)

    def test_invalid_face_photo_is_rejected(self):
        with self.assertRaises(ValueError):
            decode_face_photo("not-an-image")


if __name__ == "__main__":
    unittest.main()
