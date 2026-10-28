"""
ESP32 Serial Telemetry Data Logger
Reads live serial output from the ESP32-CAM over USB UART, logs raw voltages and readings
to a CSV file, and can forward telemetry to the Flask backend.

Usage:
  python scripts/serial_logger.py --port COM3 --baud 115200 --output data/serial_telemetry.csv
"""

import os
import sys
import time
import csv
import re
import argparse
from datetime import datetime

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None


def list_available_ports():
    """List all accessible serial COM ports."""
    if serial is None:
        print("[!] PySerial is not installed. Run: pip install pyserial")
        return []
    ports = serial.tools.list_ports.comports()
    return [p.device for p in ports]


def run_logger(port, baud, output_file):
    """Listen on serial port and write structured records to CSV."""
    if serial is None:
        print("[!] Error: pyserial package required. Install with: pip install pyserial")
        sys.exit(1)

    os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
    file_exists = os.path.exists(output_file)

    print(f"[*] Opening Serial Port {port} at {baud} baud...")
    try:
        ser = serial.Serial(port, baud, timeout=2)
    except Exception as e:
        print(f"[!] Could not open port {port}: {e}")
        available = list_available_ports()
        print(f"    Available ports: {available if available else 'None detected'}")
        sys.exit(1)

    print(f"[+] Connected to {port}. Logging to: {output_file}")
    print("[*] Press Ctrl+C to stop logging.\n")

    with open(output_file, mode="a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["Timestamp", "MQ2_mV", "MQ135_mV", "Raw_Log"])
            f.flush()

        pattern_mq2 = re.compile(r"MQ-2.*?([\d\.]+)\s*mV", re.IGNORECASE)
        pattern_mq135 = re.compile(r"MQ-135.*?([\d\.]+)\s*mV", re.IGNORECASE)

        last_mq2 = None
        last_mq135 = None

        try:
            while True:
                line = ser.readline().decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                print(f"[ESP32] {line}")
                now_str = datetime.now().isoformat()

                m2 = pattern_mq2.search(line)
                if m2:
                    last_mq2 = float(m2.group(1))

                m135 = pattern_mq135.search(line)
                if m135:
                    last_mq135 = float(m135.group(1))

                if last_mq2 is not None and last_mq135 is not None:
                    writer.writerow([now_str, last_mq2, last_mq135, line])
                    f.flush()
                    print(f"    --> Logged record: MQ2={last_mq2}mV, MQ135={last_mq135}mV")
                    last_mq2 = None
                    last_mq135 = None

        except KeyboardInterrupt:
            print("\n[*] Serial logging stopped by user.")
        finally:
            ser.close()


def main():
    parser = argparse.ArgumentParser(description="ESP32 Serial Telemetry Logger")
    parser.add_argument("--port", default="COM3", help="Serial port (e.g. COM3, COM4, /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("--output", default="data/serial_telemetry.csv", help="Output CSV path")
    parser.add_argument("--list-ports", action="store_true", help="List available COM ports")

    args = parser.parse_args()

    if args.list_ports:
        ports = list_available_ports()
        print("Available Serial Ports:")
        for p in ports:
            print(f" - {p}")
        return

    run_logger(args.port, args.baud, args.output)


if __name__ == "__main__":
    main()
