"""
Integration and API Unit Tests
Tests all Flask endpoints:
- GET  /api/health
- POST /api/analyze (safe, moderate, unsafe cases)
- GET  /api/latest
- GET  /api/history
- GET  /api/stats
- POST /api/calibrate
- GET/POST /api/settings
"""

import os
import sys
import unittest
import json
import io
from PIL import Image

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
SERVER_DIR = os.path.join(PROJECT_ROOT, "server")
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from app import app, init_db


class TestPesticideApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.client = app.test_client()
        init_db()

    def test_01_health_check(self):
        """Verify /api/health returns 200 OK and valid JSON."""
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertEqual(data["status"], "healthy")
        self.assertIn("service", data)

    def test_02_analyze_safe_sample(self):
        """Verify /api/analyze correctly classifies safe organic sample."""
        # Create small test image
        img = Image.new("RGB", (100, 100), (180, 40, 40))
        img_bytes = io.BytesIO()
        img.save(img_bytes, format="JPEG")
        img_bytes.seek(0)

        data = {
            "mq2_raw": "0.78",
            "mq135_raw": "1.95",
            "produce_type": "apple",
            "image": (img_bytes, "test_safe.jpg")
        }

        res = self.client.post("/api/analyze", data=data, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 200)
        result = json.loads(res.data)

        self.assertIn("status", result)
        self.assertIn("risk_score", result)
        self.assertIn("confidence", result)
        self.assertIn("sensor_readings", result)
        self.assertIn("visual_metrics", result)
        self.assertEqual(result["status"], "SAFE")
        self.assertLessEqual(result["risk_score"], 35.0)

    def test_03_analyze_unsafe_sample(self):
        """Verify /api/analyze flags high solvent and residue sample as UNSAFE."""
        from PIL import ImageDraw
        img = Image.new("RGB", (160, 160), (180, 40, 40))
        draw = ImageDraw.Draw(img)
        # Draw chalky powder blotches and copper sulfate stains
        for i in range(40):
            x = (i * 17) % 150
            y = (i * 23) % 150
            col = (250, 250, 250) if i % 2 == 0 else (40, 190, 220)
            draw.ellipse([x, y, x + 10, y + 10], fill=col)

        img_bytes = io.BytesIO()
        img.save(img_bytes, format="JPEG")
        img_bytes.seek(0)

        data = {
            "mq2_raw": "1.55",
            "mq135_raw": "2.55",
            "produce_type": "tomato",
            "image": (img_bytes, "test_unsafe.jpg")
        }

        res = self.client.post("/api/analyze", data=data, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 200)
        result = json.loads(res.data)

        self.assertEqual(result["status"], "UNSAFE")
        self.assertGreater(result["risk_score"], 65.0)
        self.assertEqual(result["pesticide_risk_level"], "CRITICAL")

    def test_04_get_latest(self):
        """Verify /api/latest retrieves the most recent record."""
        res = self.client.get("/api/latest")
        self.assertEqual(res.status_code, 200)
        record = json.loads(res.data)
        self.assertIn("id", record)
        self.assertIn("status", record)

    def test_05_get_history(self):
        """Verify /api/history returns list of scans with count."""
        res = self.client.get("/api/history?limit=10")
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertIn("items", data)
        self.assertIn("total", data)
        self.assertGreaterEqual(len(data["items"]), 1)

    def test_06_get_stats(self):
        """Verify /api/stats computes summary distributions."""
        res = self.client.get("/api/stats")
        self.assertEqual(res.status_code, 200)
        stats = json.loads(res.data)
        self.assertIn("total_scans", stats)
        self.assertIn("counts", stats)
        self.assertIn("safe_percentage", stats)

    def test_07_calibrate_endpoint(self):
        """Verify /api/calibrate updates clean air baseline."""
        payload = {"mq2_raw": 1.10, "mq135_raw": 1.80}
        res = self.client.post("/api/calibrate", json=payload)
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertTrue(data.get("success"))
        self.assertIn("mq2_r0", data)
        self.assertIn("mq135_r0", data)

    def test_08_settings_endpoint(self):
        """Verify /api/settings reads and updates configuration."""
        get_res = self.client.get("/api/settings")
        self.assertEqual(get_res.status_code, 200)

        # Update test threshold
        post_res = self.client.post("/api/settings", json={"safe_max_risk": 36.0})
        self.assertEqual(post_res.status_code, 200)
        updated = json.loads(post_res.data)
        self.assertEqual(updated["config"]["thresholds"]["safe_max_risk"], 36.0)


if __name__ == "__main__":
    unittest.main()
