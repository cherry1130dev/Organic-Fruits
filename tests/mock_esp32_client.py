"""
Mock ESP32-CAM Client
Simulates the ESP32-CAM hardware by posting sensor data + JPEG images to the Flask server
Allows end-to-end testing of the pipeline without physical hardware.

Usage:
  python tests/mock_esp32_client.py --condition safe --produce apple
  python tests/mock_esp32_client.py --condition unsafe --produce tomato
  python tests/mock_esp32_client.py --continuous --interval 4
"""

import os
import sys
import time
import random
import argparse
import requests
from PIL import Image, ImageDraw, ImageFilter

SERVER_URL = "http://127.0.0.1:5000"


def generate_test_image(produce_name, condition):
    """Generate a realistic test image matching the requested condition."""
    width, height = 320, 240
    produce_colors = {
        "apple": (195, 30, 30),
        "tomato": (220, 45, 30),
        "spinach": (40, 130, 45),
        "grapes": (95, 40, 120),
        "strawberry": (210, 25, 40),
        "bell_pepper": (205, 165, 20),
        "cucumber": (50, 125, 55),
        "orange": (235, 130, 20)
    }

    base_col = produce_colors.get(produce_name.lower(), (180, 50, 50))
    img = Image.new("RGB", (width, height), (30, 34, 40))
    draw = ImageDraw.Draw(img)

    cx, cy = width // 2, height // 2
    rx, ry = 85, 80

    # Draw shaded fruit body
    for step in range(rx, 0, -2):
        factor = 1.0 + (1.0 - (step / rx)) * 0.4
        draw.ellipse([cx - step, cy - int(step * 0.95), cx + step, cy + int(step * 0.95)],
                     fill=(min(255, int(base_col[0] * factor)),
                           min(255, int(base_col[1] * factor)),
                           min(255, int(base_col[2] * factor))))

    if condition == "MODERATE":
        # Add light spray mist flecks
        for _ in range(20):
            sx = cx + random.randint(-50, 50)
            sy = cy + random.randint(-40, 40)
            draw.ellipse([sx - 3, sy - 3, sx + 3, sy + 3], fill=(235, 235, 225))
    elif condition == "UNSAFE":
        # Add heavy powdery spots and copper-blue stains
        for _ in range(50):
            sx = cx + random.randint(-65, 65)
            sy = cy + random.randint(-55, 55)
            col = random.choice([
                (245, 245, 245), # White chalky powder
                (40, 190, 220),  # Copper sulfate
                (240, 230, 50)   # Sulfur dust
            ])
            r = random.randint(3, 8)
            draw.ellipse([sx - r, sy - r, sx + r, sy + r], fill=col)

    img = img.filter(ImageFilter.GaussianBlur(radius=0.5))
    temp_path = os.path.join(os.path.dirname(__file__), "temp_mock_capture.jpg")
    img.save(temp_path, "JPEG", quality=85)
    return temp_path


def send_mock_scan(produce="Apple", condition="SAFE"):
    """Simulate single ESP32-CAM POST transmission to Flask."""
    if condition == "RANDOM":
        condition = random.choice(["SAFE", "MODERATE", "UNSAFE"])

    # Physical voltage generation
    if condition == "SAFE":
        # Clean organic fruit: low solvent VOCs, low agrochemical detection
        mq2_val = round(random.uniform(0.70, 0.90), 2)
        mq135_val = round(random.uniform(1.85, 2.10), 2)
    elif condition == "MODERATE":
        # Moderate residue: slightly elevated
        mq2_val = round(random.uniform(1.05, 1.25), 2)
        mq135_val = round(random.uniform(2.20, 2.38), 2)
    else: # UNSAFE
        # Heavy pesticide treatment: high solvent vapors, organonitrogen decomposition
        mq2_val = round(random.uniform(1.40, 1.85), 2)
        mq135_val = round(random.uniform(2.45, 2.90), 2)

    img_path = generate_test_image(produce, condition)

    print(f"\n[*] [Mock ESP32] Sending {condition} sample for {produce.capitalize()}...")
    print(f"    MQ-2 ADC Voltage:   {mq2_val} V")
    print(f"    MQ-135 ADC Voltage: {mq135_val} V")
    print(f"    Captured Image:     {img_path}")

    url = f"{SERVER_URL}/api/analyze"
    try:
        start = time.time()
        with open(img_path, "rb") as img_file:
            files = {"image": ("mock_cam.jpg", img_file, "image/jpeg")}
            data = {
                "mq2_raw": mq2_val,
                "mq135_raw": mq135_val,
                "produce_type": produce
            }
            resp = requests.post(url, files=files, data=data, timeout=10)
        elapsed = round((time.time() - start) * 1000, 1)

        if resp.status_code == 200:
            result = resp.json()
            status = result["status"]
            badge_icon = "[SAFE]" if status == "SAFE" else ("[MODERATE]" if status == "MODERATE" else "[UNSAFE]")
            print(f"[+] [Server Response - {elapsed}ms]:")
            print(f"    Status:         {badge_icon} {status}")
            print(f"    Risk Score:     {result['risk_score']}% (Level: {result['pesticide_risk_level']})")
            print(f"    Confidence:     {result['confidence']}%")
            print(f"    MQ-2 PPM:       {result['sensor_readings']['mq2_ppm']} ppm")
            print(f"    MQ-135 PPM:     {result['sensor_readings']['mq135_ppm']} ppm")
            print(f"    Residue Ratio:  {result['visual_metrics']['total_residue_ratio']}%")
            print(f"    Recommendation: {result['action_recommendation']}")
            return True, result
        else:
            print(f"[!] Server returned HTTP {resp.status_code}: {resp.text}")
            return False, None

    except requests.exceptions.ConnectionError:
        print(f"[!] Connection failed! Ensure Flask backend is running on {SERVER_URL}")
        return False, None
    finally:
        if os.path.exists(img_path):
            try:
                os.remove(img_path)
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser(description="Mock ESP32-CAM Client for Pesticide Screener")
    parser.add_argument("--condition", choices=["safe", "moderate", "unsafe", "random"], default="random",
                        help="Target test condition (default: random)")
    parser.add_argument("--produce", default="apple",
                        choices=["apple", "tomato", "spinach", "grapes", "strawberry", "bell_pepper", "cucumber", "orange"],
                        help="Produce type to inspect")
    parser.add_argument("--continuous", action="store_true", help="Run continuous loop sending scans")
    parser.add_argument("--interval", type=int, default=5, help="Interval in seconds between continuous scans")
    parser.add_argument("--url", default="http://127.0.0.1:5000", help="Flask server URL")

    args = parser.parse_args()
    global SERVER_URL
    SERVER_URL = args.url

    print("==================================================================")
    print("   Mock ESP32-CAM Hardware Simulator Client")
    print(f"   Target Server: {SERVER_URL}")
    print("==================================================================")

    if args.continuous:
        print(f"[*] Starting continuous simulation every {args.interval} seconds. Press Ctrl+C to stop.")
        try:
            while True:
                produce_choice = random.choice(["apple", "tomato", "spinach", "grapes", "strawberry", "orange"])
                cond_choice = random.choice(["SAFE", "MODERATE", "UNSAFE"])
                send_mock_scan(produce_choice, cond_choice)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n[*] Stopped continuous mock client.")
    else:
        send_mock_scan(args.produce, args.condition.upper())


if __name__ == "__main__":
    main()
