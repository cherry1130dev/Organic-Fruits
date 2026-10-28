"""
Live Laptop Webcam Produce Screener
Uses the laptop's integrated webcam to capture produce in real time, sends frames to the
Flask ML server (http://127.0.0.1:5000/api/analyze), and displays live inspection HUD overlays.

Controls in Webcam Window:
  [SPACE]     : Capture snapshot and run AI pesticide analysis
  [1] - [8]   : Switch produce profile (Apple, Tomato, Spinach, Grapes, etc.)
  [C]         : Toggle clean air vs elevated headspace sensor simulation
  [Q] or [ESC]: Quit webcam
"""

import os
import sys
import time
import requests
import numpy as np

try:
    import cv2
except ImportError:
    print("[!] OpenCV is not installed. Run: pip install opencv-python")
    sys.exit(1)

SERVER_URL = "http://127.0.0.1:5000"

PRODUCE_LIST = [
    "Apple", "Tomato", "Spinach", "Grapes",
    "Strawberry", "Bell Pepper", "Cucumber", "Orange"
]


def check_server():
    """Verify backend server is accessible."""
    try:
        r = requests.get(f"{SERVER_URL}/api/health", timeout=3)
        return r.status_code == 200
    except requests.RequestException:
        return False


def main():
    print("==================================================================")
    print("   Live Laptop Webcam Pesticide Screener")
    print("   Connecting to Flask ML Server at:", SERVER_URL)
    print("==================================================================")

    if not check_server():
        print(f"[!] Warning: Flask server not reachable on {SERVER_URL}.")
        print("    Ensure the backend is started with: python server/app.py")
        print("    Continuing, but API analysis will fail until server is online.\n")

    # Open Default Laptop Webcam (Device 0)
    print("[*] Accessing Laptop Integrated Webcam (Device index 0)...")
    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("[!] Error: Could not open laptop webcam. Check camera permissions or index.")
        return

    # Set camera resolution (640x480 standard)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    produce_idx = 0
    sensor_mode = "NORMAL"  # "NORMAL" (Safe/Organic) or "ELEVATED" (Pesticide residue)
    last_verdict = None
    analyzing = False
    flash_frames = 0

    print("[+] Webcam stream started.")
    print("    - Hold a fruit or vegetable in front of your camera.")
    print("    - Press [SPACE] to capture and screen for pesticide residue.")
    print("    - Press [1-8] to switch produce type.")
    print("    - Press [C] to toggle simulated gas sensor headspace (Normal vs Elevated).")
    print("    - Press [Q] to quit.\n")

    window_name = "AgriShield - Laptop Webcam Produce Screener"
    cv2.namedWindow(window_name, cv2.WINDOW_AUTOSIZE)

    while True:
        ret, frame = cap.read()
        if not ret:
            print("[!] Failed to grab webcam frame.")
            break

        h, w, _ = frame.shape
        display = frame.copy()

        # Draw inspection target reticle in center
        cx, cy = w // 2, h // 2
        box_size = 220
        x1, y1 = cx - box_size // 2, cy - box_size // 2
        x2, y2 = cx + box_size // 2, cy + box_size // 2

        # Reticle color based on last verdict
        reticle_color = (16, 185, 129)  # Default emerald green
        if last_verdict:
            status = last_verdict.get("status")
            if status == "UNSAFE":
                reticle_color = (68, 68, 239)   # Red (BGR)
            elif status == "MODERATE":
                reticle_color = (11, 158, 245)  # Amber (BGR)

        # Draw rounded corner brackets
        corner_len = 25
        thickness = 3
        # Top-left
        cv2.line(display, (x1, y1), (x1 + corner_len, y1), reticle_color, thickness)
        cv2.line(display, (x1, y1), (x1, y1 + corner_len), reticle_color, thickness)
        # Top-right
        cv2.line(display, (x2, y1), (x2 - corner_len, y1), reticle_color, thickness)
        cv2.line(display, (x2, y1), (x2, y1 + corner_len), reticle_color, thickness)
        # Bottom-left
        cv2.line(display, (x1, y2), (x1 + corner_len, y2), reticle_color, thickness)
        cv2.line(display, (x1, y2), (x1, y2 - corner_len), reticle_color, thickness)
        # Bottom-right
        cv2.line(display, (x2, y2), (x2 - corner_len, y2), reticle_color, thickness)
        cv2.line(display, (x2, y2), (x2, y2 - corner_len), reticle_color, thickness)

        # Top HUD overlay banner
        cv2.rectangle(display, (0, 0), (w, 50), (15, 23, 42), -1)
        current_produce = PRODUCE_LIST[produce_idx]
        header_text = f"TARGET: {current_produce.upper()} | SENSOR HEADSPACE: {sensor_mode}"
        cv2.putText(display, header_text, (15, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)

        # Bottom HUD overlay banner
        cv2.rectangle(display, (0, h - 60), (w, h), (15, 23, 42), -1)
        if analyzing:
            cv2.putText(display, "ANALYZING PRODUCE... Running Multimodal ML Inference", (15, h - 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)
        elif last_verdict:
            status = last_verdict.get("status", "UNKNOWN")
            risk = last_verdict.get("risk_score", 0.0)
            conf = last_verdict.get("confidence", 0.0)
            verdict_text = f"VERDICT: {status}  |  RISK SCORE: {risk}%  |  CONFIDENCE: {conf}%"
            cv2.putText(display, verdict_text, (15, h - 25), cv2.FONT_HERSHEY_SIMPLEX, 0.65, reticle_color, 2)
        else:
            cv2.putText(display, "[SPACE]: Capture & Inspect  |  [1-8]: Select Produce  |  [Q]: Quit",
                        (15, h - 25), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)

        # Shutter flash effect
        if flash_frames > 0:
            white_overlay = np.full_like(display, 255)
            alpha = flash_frames / 4.0
            display = cv2.addWeighted(display, 1.0 - alpha, white_overlay, alpha, 0)
            flash_frames -= 1

        cv2.imshow(window_name, display)
        key = cv2.waitKey(1) & 0xFF

        if key in [ord('q'), ord('Q'), 27]:  # 27 is ESC
            break
        elif key in [ord(str(i)) for i in range(1, 9)]:
            produce_idx = int(chr(key)) - 1
            print(f"[*] Target produce profile changed to: {PRODUCE_LIST[produce_idx]}")
        elif key in [ord('c'), ord('C')]:
            sensor_mode = "ELEVATED" if sensor_mode == "NORMAL" else "NORMAL"
            print(f"[*] Sensor headspace simulation toggled to: {sensor_mode}")
        elif key == 32:  # SPACEBAR pressed -> Capture & Analyze!
            print(f"\n[*] [Webcam] Capturing snapshot for {PRODUCE_LIST[produce_idx]} inspection...")
            flash_frames = 4  # Trigger flash effect
            analyzing = True
            cv2.imshow(window_name, display)
            cv2.waitKey(1)

            # Crop or use whole frame
            # Encode frame to JPEG memory buffer
            success, encoded_img = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
            if not success:
                print("[!] Image encoding failed.")
                analyzing = False
                continue

            img_bytes = encoded_img.tobytes()

            # Set realistic sensor values matching selected sensor mode
            if sensor_mode == "NORMAL":
                # Clean organic headspace
                mq2_val = round(np.random.uniform(0.75, 0.95), 2)
                mq135_val = round(np.random.uniform(1.85, 2.15), 2)
            else:
                # Elevated solvent & agrochemical headspace
                mq2_val = round(np.random.uniform(1.45, 1.80), 2)
                mq135_val = round(np.random.uniform(2.50, 2.85), 2)

            files = {"image": ("webcam_scan.jpg", img_bytes, "image/jpeg")}
            data = {
                "mq2_raw": mq2_val,
                "mq135_raw": mq135_val,
                "produce_type": PRODUCE_LIST[produce_idx]
            }

            try:
                t0 = time.time()
                resp = requests.post(f"{SERVER_URL}/api/analyze", files=files, data=data, timeout=8)
                t_elapsed = round((time.time() - t0) * 1000, 1)

                if resp.status_code == 200:
                    last_verdict = resp.json()
                    st = last_verdict["status"]
                    print(f"[+] [ML Analysis Complete - {t_elapsed}ms]:")
                    print(f"    Verdict:        {st}")
                    print(f"    Risk Score:     {last_verdict['risk_score']}% ({last_verdict['pesticide_risk_level']})")
                    print(f"    Confidence:     {last_verdict['confidence']}%")
                    print(f"    MQ-2 Reading:   {last_verdict['sensor_readings']['mq2_ppm']} ppm")
                    print(f"    MQ-135 Reading: {last_verdict['sensor_readings']['mq135_ppm']} ppm")
                    print(f"    Residue Ratio:  {last_verdict['visual_metrics']['total_residue_ratio']}%")
                    print(f"    Action:         {last_verdict['action_recommendation']}")
                    print(f"    Dashboard updated in real-time at: {SERVER_URL}/\n")
                else:
                    print(f"[!] Server returned HTTP {resp.status_code}: {resp.text}")
            except requests.RequestException as err:
                print(f"[!] Analysis request failed: {err}")
            finally:
                analyzing = False

    cap.release()
    cv2.destroyAllWindows()
    print("[*] Webcam session ended.")


if __name__ == "__main__":
    main()
