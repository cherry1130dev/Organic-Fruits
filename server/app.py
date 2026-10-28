"""
Flask Backend Server for IoT-Based Detection of Pesticide in Organic Fruits and Vegetables
REST API Endpoints:
- POST /api/analyze   : Ingests ESP32-CAM multipart payload (MQ2, MQ135, image, produce_type) -> returns ML verdict
- GET  /api/history   : Retrieves past scan records with filtering and pagination
- GET  /api/latest    : Retrieves the most recent scan record
- GET  /api/health    : Health check endpoint
- POST /api/calibrate : Calibrates clean air baseline R0 values for MQ sensors
- GET  /api/stats     : Aggregated screening statistics
- GET/POST /api/settings : View or update thresholds, model weights, and alerts
- POST /api/test-telegram: Sends test notification via Telegram bot
- GET  /              : Responsive Web Dashboard (PWA)
"""

import os
import sys
import io
import time
import json
import sqlite3
import datetime
import yaml
import requests
from flask import Flask, request, jsonify, render_template, send_from_directory, current_app
from flask_cors import CORS
from werkzeug.utils import secure_filename
from PIL import Image

# Add current directory to path
SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from ml.predict import get_predictor
from ml.features import raw_to_voltage, calculate_rs

# Initialize Flask App
app = Flask(
    __name__,
    static_folder="static",
    template_folder="templates"
)
CORS(app)

# Load Configuration
CONFIG_PATH = os.path.join(SERVER_DIR, "config.yaml")
def load_app_config():
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)

app_config = load_app_config()
app.secret_key = app_config["server"].get("secret_key", "pesticide-secret-key-2026")

UPLOAD_DIR = os.path.join(SERVER_DIR, app_config["server"].get("upload_folder", "data/uploads"))
os.makedirs(UPLOAD_DIR, exist_ok=True)
DB_PATH = os.path.join(SERVER_DIR, app_config["database"].get("sqlite_path", "data/pesticide_history.db"))
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


# ----------------------------------------------------------------------
# Database Initialization & Helpers
# ----------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pesticide_scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                produce_type TEXT NOT NULL,
                status TEXT NOT NULL,
                verdict_badge TEXT NOT NULL,
                risk_score REAL NOT NULL,
                confidence REAL NOT NULL,
                pesticide_risk_level TEXT NOT NULL,
                action_recommendation TEXT,
                v_mq2 REAL,
                v_mq135 REAL,
                ppm_mq2 REAL,
                ppm_mq135 REAL,
                ratio_mq2 REAL,
                ratio_mq135 REAL,
                powder_ratio REAL,
                copper_ratio REAL,
                sulfur_ratio REAL,
                total_residue_ratio REAL,
                spot_score REAL,
                surface_cleanliness REAL,
                image_filename TEXT,
                heatmap_filename TEXT
            )
        """)
        conn.commit()

init_db()


# ----------------------------------------------------------------------
# External Alert Dispatcher (Telegram Bot)
# ----------------------------------------------------------------------

def send_telegram_alert(verdict_data):
    """Dispatch alert to configured Telegram bot if enabled."""
    cfg = load_app_config()
    tg_cfg = cfg.get("alerts", {}).get("telegram", {})
    if not tg_cfg.get("enabled", False):
        return False, "Telegram notifications disabled in config"

    bot_token = tg_cfg.get("bot_token")
    chat_id = tg_cfg.get("chat_id")
    if not bot_token or not chat_id or "YOUR_TELEGRAM" in bot_token:
        return False, "Telegram credentials not configured"

    status = verdict_data.get("status", "UNKNOWN")
    if status == "SAFE" and not tg_cfg.get("alert_on_safe", False):
        return True, "Suppressed for SAFE status"
    if status == "MODERATE" and not tg_cfg.get("alert_on_moderate", False):
        return True, "Suppressed for MODERATE status"

    # Emoji & Header
    emoji_map = {"SAFE": "🟢 SAFE", "MODERATE": "🟡 MODERATE RESIDUE", "UNSAFE": "🔴 UNSAFE / CONTAMINATED"}
    header = emoji_map.get(status, "⚠️ PESTICIDE ALERT")

    msg = (
        f"*{header}*\n\n"
        f"🍎 *Produce:* {verdict_data.get('produce_type')}\n"
        f"📊 *Verdict:* {status}\n"
        f"⚠️ *Risk Score:* {verdict_data.get('risk_score')}%\n"
        f"🎯 *Confidence:* {verdict_data.get('confidence')}%\n"
        f"💨 *MQ-2 (Solvents):* {verdict_data.get('sensor_readings', {}).get('mq2_ppm', 0)} ppm\n"
        f"💨 *MQ-135 (VOC/Organo):* {verdict_data.get('sensor_readings', {}).get('mq135_ppm', 0)} ppm\n"
        f"🔬 *Surface Residue:* {verdict_data.get('visual_metrics', {}).get('total_residue_ratio', 0)}%\n\n"
        f"💡 *Action:* {verdict_data.get('action_recommendation')}\n"
        f"🕒 *Time:* {verdict_data.get('timestamp')}"
    )

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": msg,
        "parse_mode": "Markdown"
    }

    try:
        resp = requests.post(url, json=payload, timeout=6)
        if resp.status_code == 200:
            return True, "Alert sent successfully"
        else:
            return False, f"Telegram API error: {resp.text}"
    except Exception as e:
        return False, f"Telegram connection error: {str(e)}"


# ----------------------------------------------------------------------
# REST API Endpoints
# ----------------------------------------------------------------------

@app.route("/api/health", methods=["GET"])
def health_check():
    """Health check endpoint to verify backend operational status."""
    return jsonify({
        "status": "healthy",
        "service": "IoT Pesticide Detection System",
        "version": "1.0.0",
        "timestamp": datetime.datetime.now().isoformat()
    }), 200


@app.route("/api/analyze", methods=["POST"])
def analyze_sample():
    """
    Main ingestion endpoint: receives sensor values + image from ESP32-CAM or test clients.
    Expected multipart form fields:
    - mq2_raw: raw ADC reading or millivolts
    - mq135_raw: raw ADC reading or millivolts
    - produce_type: produce name (e.g. 'Apple', 'Tomato')
    - image: JPEG/PNG image binary (optional, fallback to default visual inspection)
    """
    try:
        cfg = load_app_config()
        # Parse sensor inputs
        mq2_val = request.form.get("mq2_raw", request.form.get("mq2_ppm", 1.0))
        mq135_val = request.form.get("mq135_raw", request.form.get("mq135_ppm", 1.8))
        produce_type = request.form.get("produce_type", "Apple").strip().lower()

        # Handle image file
        image_file = request.files.get("image")
        timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
        
        if image_file and image_file.filename != "":
            raw_filename = secure_filename(image_file.filename)
            ext = os.path.splitext(raw_filename)[1] or ".jpg"
            img_save_name = f"scan_{timestamp_str}_{produce_type}{ext}"
            img_path = os.path.join(UPLOAD_DIR, img_save_name)
            image_file.save(img_path)
            pil_img = Image.open(img_path).convert("RGB")
        else:
            # Generate or use fallback inspection image
            img_save_name = f"scan_{timestamp_str}_{produce_type}_sample.jpg"
            img_path = os.path.join(UPLOAD_DIR, img_save_name)
            pil_img = Image.new("RGB", (320, 240), (45, 52, 58))
            pil_img.save(img_path, "JPEG")

        # Run Multimodal Inference
        predictor = get_predictor()
        prediction = predictor.predict(
            mq2_raw=mq2_val,
            mq135_raw=mq135_val,
            image_input=pil_img,
            produce_type=produce_type
        )

        # Save residue heatmap overlay
        heatmap_save_name = f"heatmap_{img_save_name}"
        heatmap_path = os.path.join(UPLOAD_DIR, heatmap_save_name)
        prediction["heatmap_image"].save(heatmap_path, "JPEG", quality=90)

        # Determine pesticide risk classification
        risk_score = prediction["risk_score"]
        if risk_score <= 35.0:
            risk_level = "LOW"
        elif risk_score <= 68.0:
            risk_level = "ELEVATED"
        else:
            risk_level = "CRITICAL"

        now_iso = datetime.datetime.now().isoformat()
        
        # Build comprehensive response
        result = {
            "status": prediction["status"],
            "verdict_badge": prediction["verdict_badge"],
            "risk_score": risk_score,
            "confidence": prediction["confidence"],
            "pesticide_risk_level": risk_level,
            "action_recommendation": prediction["action_recommendation"],
            "produce_type": prediction["produce_type"],
            "timestamp": now_iso,
            "sensor_readings": prediction["sensor_readings"],
            "visual_metrics": prediction["visual_metrics"],
            "probabilities": prediction["probabilities"],
            "image_url": f"/api/images/{img_save_name}",
            "heatmap_url": f"/api/images/{heatmap_save_name}"
        }

        # Store in SQLite Database
        with get_db() as conn:
            conn.execute("""
                INSERT INTO pesticide_scans (
                    timestamp, produce_type, status, verdict_badge, risk_score, confidence,
                    pesticide_risk_level, action_recommendation, v_mq2, v_mq135,
                    ppm_mq2, ppm_mq135, ratio_mq2, ratio_mq135, powder_ratio, copper_ratio,
                    sulfur_ratio, total_residue_ratio, spot_score, surface_cleanliness,
                    image_filename, heatmap_filename
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                now_iso,
                result["produce_type"],
                result["status"],
                result["verdict_badge"],
                result["risk_score"],
                result["confidence"],
                result["pesticide_risk_level"],
                result["action_recommendation"],
                prediction["sensor_readings"]["mq2_voltage"],
                prediction["sensor_readings"]["mq135_voltage"],
                prediction["sensor_readings"]["mq2_ppm"],
                prediction["sensor_readings"]["mq135_ppm"],
                prediction["sensor_readings"]["mq2_ratio"],
                prediction["sensor_readings"]["mq135_ratio"],
                prediction["visual_metrics"]["powder_residue_ratio"],
                prediction["visual_metrics"]["copper_fungicide_ratio"],
                prediction["visual_metrics"]["sulfur_dust_ratio"],
                prediction["visual_metrics"]["total_residue_ratio"],
                prediction["visual_metrics"]["spot_texture_score"],
                prediction["visual_metrics"]["surface_cleanliness"],
                img_save_name,
                heatmap_save_name
            ))
            conn.commit()

        # Trigger Telegram Alert if needed
        if result["status"] in ["UNSAFE", "MODERATE"]:
            send_telegram_alert(result)

        return jsonify(result), 200

    except Exception as e:
        app.logger.error(f"Error in /api/analyze: {str(e)}", exc_info=True)
        return jsonify({
            "error": "Analysis failed",
            "message": str(e)
        }), 500


@app.route("/api/latest", methods=["GET"])
def get_latest_scan():
    """Retrieve the most recent pesticide screening record."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM pesticide_scans ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        if not row:
            return jsonify({"message": "No scan records found"}), 404
        record = dict(row)
        record["image_url"] = f"/api/images/{record['image_filename']}" if record.get("image_filename") else None
        record["heatmap_url"] = f"/api/images/{record['heatmap_filename']}" if record.get("heatmap_filename") else None
        return jsonify(record), 200


@app.route("/api/history", methods=["GET"])
def get_history():
    """Retrieve historical screening scans with optional filtering and pagination."""
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    produce = request.args.get("produce")
    status = request.args.get("status")

    query = "SELECT * FROM pesticide_scans WHERE 1=1"
    params = []
    if produce and produce.lower() != "all":
        query += " AND LOWER(produce_type) = LOWER(?)"
        params.append(produce)
    if status and status.upper() != "ALL":
        query += " AND status = ?"
        params.append(status.upper())

    query += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        # Total count query for pagination
        count_query = "SELECT COUNT(*) as total FROM pesticide_scans WHERE 1=1"
        count_params = []
        if produce and produce.lower() != "all":
            count_query += " AND LOWER(produce_type) = LOWER(?)"
            count_params.append(produce)
        if status and status.upper() != "ALL":
            count_query += " AND status = ?"
            count_params.append(status.upper())
        cursor.execute(count_query, count_params)
        total = cursor.fetchone()["total"]

        items = []
        for r in rows:
            d = dict(r)
            d["image_url"] = f"/api/images/{d['image_filename']}" if d.get("image_filename") else None
            d["heatmap_url"] = f"/api/images/{d['heatmap_filename']}" if d.get("heatmap_filename") else None
            items.append(d)

        return jsonify({
            "total": total,
            "count": len(items),
            "offset": offset,
            "limit": limit,
            "items": items
        }), 200


@app.route("/api/stats", methods=["GET"])
def get_statistics():
    """Aggregate statistics for dashboard metrics and charts."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as total, AVG(risk_score) as avg_risk FROM pesticide_scans")
        total_row = cursor.fetchone()
        total_scans = total_row["total"] or 0
        avg_risk = round(total_row["avg_risk"] or 0.0, 1)

        cursor.execute("SELECT status, COUNT(*) as cnt FROM pesticide_scans GROUP BY status")
        status_rows = cursor.fetchall()
        status_counts = {"SAFE": 0, "MODERATE": 0, "UNSAFE": 0}
        for sr in status_rows:
            if sr["status"] in status_counts:
                status_counts[sr["status"]] = sr["cnt"]

        # Produce distribution
        cursor.execute("SELECT produce_type, COUNT(*) as cnt FROM pesticide_scans GROUP BY produce_type ORDER BY cnt DESC LIMIT 8")
        produce_dist = [dict(r) for r in cursor.fetchall()]

        # Recent 10 timeline for mini-sparklines
        cursor.execute("SELECT id, timestamp, risk_score, ppm_mq2, ppm_mq135 FROM pesticide_scans ORDER BY id DESC LIMIT 15")
        recent_trend = [dict(r) for r in cursor.fetchall()][::-1]

        safe_pct = round((status_counts["SAFE"] / total_scans * 100), 1) if total_scans > 0 else 100.0

        return jsonify({
            "total_scans": total_scans,
            "avg_risk": avg_risk,
            "safe_percentage": safe_pct,
            "counts": status_counts,
            "produce_distribution": produce_dist,
            "recent_trend": recent_trend
        }), 200


@app.route("/api/calibrate", methods=["POST"])
def calibrate_sensors():
    """
    Calibrate MQ-2 and MQ-135 sensors in clean ambient air.
    Calculates R0 = Rs / clean_air_ratio and stores updated baseline in config.yaml.
    """
    try:
        data = request.get_json(silent=True) or request.form
        mq2_clean_val = data.get("mq2_raw", 1.1)
        mq135_clean_val = data.get("mq135_raw", 1.8)

        cfg = load_app_config()
        sens_cfg = cfg.get("sensors", {})
        vc = sens_cfg.get("supply_voltage_vc", 5.0)
        divider = sens_cfg.get("voltage_divider_ratio", 0.66)
        rl_mq2 = sens_cfg.get("rl_mq2", 10.0)
        rl_mq135 = sens_cfg.get("rl_mq135", 10.0)

        v_mq2 = raw_to_voltage(mq2_clean_val)
        v_mq135 = raw_to_voltage(mq135_clean_val)

        rs_mq2 = calculate_rs(v_mq2, vc=vc, rl=rl_mq2, divider_ratio=divider)
        rs_mq135 = calculate_rs(v_mq135, vc=vc, rl=rl_mq135, divider_ratio=divider)

        # Standard clean air ratios: MQ2 = 9.83, MQ135 = 3.6
        new_r0_mq2 = round(rs_mq2 / 9.83, 3)
        new_r0_mq135 = round(rs_mq135 / 3.60, 3)

        # Update and save to config.yaml
        cfg["sensors"]["mq2_default_r0"] = new_r0_mq2
        cfg["sensors"]["mq135_default_r0"] = new_r0_mq135
        with open(CONFIG_PATH, "w") as f:
            yaml.safe_dump(cfg, f, default_flow_style=False)

        # Re-initialize predictor with new config
        global _predictor
        from ml import predict
        predict._predictor = None

        return jsonify({
            "success": True,
            "message": "Clean-air baseline calibrated and saved successfully",
            "mq2_r0": new_r0_mq2,
            "mq135_r0": new_r0_mq135,
            "rs_mq2": round(rs_mq2, 3),
            "rs_mq135": round(rs_mq135, 3)
        }), 200

    except Exception as e:
        return jsonify({"error": f"Calibration failed: {str(e)}"}), 500


@app.route("/api/settings", methods=["GET", "POST"])
def manage_settings():
    """Retrieve or update system thresholds, fusion weights, and alerts."""
    cfg = load_app_config()
    if request.method == "POST":
        data = request.get_json() or {}
        # Update thresholds
        if "safe_max_risk" in data:
            cfg["thresholds"]["safe_max_risk"] = float(data["safe_max_risk"])
        if "moderate_max_risk" in data:
            cfg["thresholds"]["moderate_max_risk"] = float(data["moderate_max_risk"])
        if "weight_sensor" in data:
            w_s = float(data["weight_sensor"])
            cfg["ml"]["weight_sensor"] = round(w_s, 2)
            cfg["ml"]["weight_image"] = round(1.0 - w_s, 2)
        if "telegram" in data:
            tg = data["telegram"]
            cfg["alerts"]["telegram"]["enabled"] = bool(tg.get("enabled", False))
            if "bot_token" in tg and tg["bot_token"]:
                cfg["alerts"]["telegram"]["bot_token"] = tg["bot_token"]
            if "chat_id" in tg and tg["chat_id"]:
                cfg["alerts"]["telegram"]["chat_id"] = tg["chat_id"]

        with open(CONFIG_PATH, "w") as f:
            yaml.safe_dump(cfg, f, default_flow_style=False)

        # Invalidate predictor singleton to reload config
        from ml import predict
        predict._predictor = None

        return jsonify({"success": True, "message": "Settings updated successfully", "config": cfg}), 200

    return jsonify(cfg), 200


@app.route("/api/test-telegram", methods=["POST"])
def test_telegram_endpoint():
    """Test Telegram notification with sample alert."""
    sample_verdict = {
        "status": "UNSAFE",
        "produce_type": "Sample Apple (Test)",
        "risk_score": 88.5,
        "confidence": 96.0,
        "action_recommendation": "Test Alert from Pesticide Detection Dashboard",
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sensor_readings": {"mq2_ppm": 245.0, "mq135_ppm": 130.0},
        "visual_metrics": {"total_residue_ratio": 18.5}
    }
    success, msg = send_telegram_alert(sample_verdict)
    return jsonify({"success": success, "message": msg}), (200 if success else 400)


@app.route("/api/images/<path:filename>")
def serve_image(filename):
    """Serve uploaded photos and generated heatmaps."""
    return send_from_directory(UPLOAD_DIR, filename)


# ----------------------------------------------------------------------
# Progressive Web App (PWA) Assets & Dashboard UI
# ----------------------------------------------------------------------

@app.route("/")
def index():
    """Render the responsive Single-Page Dashboard."""
    cfg = load_app_config()
    return render_template("index.html", config=cfg)

@app.route("/manifest.json")
def manifest():
    return send_from_directory(os.path.join(SERVER_DIR, "static"), "manifest.json", mimetype="application/manifest+json")

@app.route("/sw.js")
def service_worker():
    return send_from_directory(os.path.join(SERVER_DIR, "static"), "sw.js", mimetype="application/javascript")


if __name__ == "__main__":
    host = app_config["server"].get("host", "0.0.0.0")
    port = int(app_config["server"].get("port", 5000))
    debug = bool(app_config["server"].get("debug", False))
    print(f"==================================================================")
    print(f"  IoT Pesticide Detection Server running on http://{host}:{port}")
    print(f"  Mobile PWA Dashboard: http://localhost:{port}/")
    print(f"==================================================================")
    app.run(host=host, port=port, debug=debug)
