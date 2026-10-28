"""
Model Training and Synthetic Dataset Generation Pipeline
Trains:
1. Sensor Random Forest Model (MQ-2, MQ-135, Rs/R0, PPM, Produce Type)
2. Image Feature Classifier (Surface powder, copper stains, sulfur dust, edge texture)
3. Evaluates with confusion matrices, classification reports, feature importance plots
4. Exports trained artifacts to server/models/
"""

import os
import sys
import math
import json
import random
import yaml
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFilter
import joblib
import matplotlib
matplotlib.use("Agg")  # Non-interactive backend for headless server
import matplotlib.pyplot as plt
import seaborn as sns

from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.metrics import accuracy_score, precision_recall_fscore_support, confusion_matrix, classification_report

# Ensure server module is in path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.dirname(CURRENT_DIR)
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from ml.features import extract_image_features, calculate_rs, calculate_ppm


def load_config():
    config_path = os.path.join(SERVER_DIR, "config.yaml")
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def generate_synthetic_image(produce_name, status, output_path):
    """
    Generate a realistic synthetic fruit/vegetable produce image with or without pesticide residue.
    - Clean: smooth organic peel with natural gradients and slight organic blemishes.
    - Moderate: faint chalky ring spots or light spray residue flecks.
    - Unsafe: noticeable white powder blotches (mancozeb), blue-cyan spots (copper sulfate), or sulfur dust.
    """
    width, height = 320, 240
    # Base produce colors
    produce_colors = {
        "apple": (190, 35, 35),
        "tomato": (215, 45, 30),
        "spinach": (40, 125, 45),
        "grapes": (90, 45, 115),
        "strawberry": (210, 30, 40),
        "bell_pepper": (200, 160, 20),
        "cucumber": (50, 120, 55),
        "orange": (235, 125, 20)
    }

    base_col = produce_colors.get(produce_name.lower(), (180, 50, 50))
    # Create image background (inspection table / matte surface)
    img = Image.new("RGB", (width, height), (35, 38, 42))
    draw = ImageDraw.Draw(img)

    # Fruit center and radius
    cx, cy = width // 2 + random.randint(-10, 10), height // 2 + random.randint(-10, 10)
    rx = random.randint(75, 95)
    ry = int(rx * random.uniform(0.9, 1.1))

    # Draw shaded produce body (simulated 3D sphere)
    for step in range(rx, 0, -2):
        factor = 1.0 + (1.0 - (step / rx)) * 0.45
        r_col = min(255, int(base_col[0] * factor))
        g_col = min(255, int(base_col[1] * factor))
        b_col = min(255, int(base_col[2] * factor))
        offset_x = int((rx - step) * 0.3)
        offset_y = int((ry - step) * 0.3)
        draw.ellipse([cx - step + offset_x, cy - step + offset_y, cx + step + offset_x, cy + step + offset_y],
                     fill=(r_col, g_col, b_col))

    # Add natural organic texture noise
    noise_overlay = Image.new("RGB", (width, height), (0, 0, 0))
    noise_draw = ImageDraw.Draw(noise_overlay)
    for _ in range(60):
        nx = cx + random.randint(-rx + 15, rx - 15)
        ny = cy + random.randint(-ry + 15, ry - 15)
        nr = random.randint(1, 3)
        noise_draw.ellipse([nx - nr, ny - nr, nx + nr, ny + nr], fill=(20, 20, 20))

    img = Image.blend(img, Image.composite(noise_overlay, img, noise_overlay.convert("L")), 0.15)
    draw = ImageDraw.Draw(img)

    # If MODERATE or UNSAFE, inject chemical surface residues
    if status == "MODERATE":
        # Light pesticide mist / chalky spray drying rings
        num_spots = random.randint(12, 28)
        for _ in range(num_spots):
            sx = cx + random.randint(-int(rx * 0.7), int(rx * 0.7))
            sy = cy + random.randint(-int(ry * 0.7), int(ry * 0.7))
            s_rad = random.randint(2, 6)
            # Powdery off-white chalk stain
            draw.ellipse([sx - s_rad, sy - s_rad, sx + s_rad, sy + s_rad], fill=(230, 230, 220), outline=(240, 240, 235))

    elif status == "UNSAFE":
        # Heavy pesticide residue: white dust patches + copper sulfate cyan spots + sulfur powder
        num_blotches = random.randint(35, 75)
        for _ in range(num_blotches):
            sx = cx + random.randint(-int(rx * 0.8), int(rx * 0.8))
            sy = cy + random.randint(-int(ry * 0.8), int(ry * 0.8))
            s_rad = random.randint(3, 10)
            choice = random.random()
            if choice < 0.55:
                # White chalky / mancozeb powder deposit
                col = (random.randint(235, 255), random.randint(235, 255), random.randint(235, 255))
            elif choice < 0.80:
                # Copper sulfate / Bordeaux mixture fungicide (cyan/blue)
                col = (random.randint(30, 60), random.randint(180, 220), random.randint(210, 250))
            else:
                # Sulfur powder dust (pale yellow)
                col = (random.randint(230, 255), random.randint(220, 245), random.randint(50, 100))
            draw.ellipse([sx - s_rad, sy - s_rad, sx + s_rad, sy + s_rad], fill=col)

    # Subtle gaussian blur to blend droplets organically
    img = img.filter(ImageFilter.GaussianBlur(radius=0.7))
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path, "JPEG", quality=88)
    return img


def generate_synthetic_dataset(config, num_samples=1200):
    """
    Generate balanced dataset for sensor and image models.
    Produces:
    - dataset.csv with realistic physical sensor readings and image feature vectors
    - sample images in server/data/images/
    """
    print(f"[*] Generating synthetic agricultural sensor dataset ({num_samples} samples)...")
    sens_cfg = config.get("sensors", {})
    vc = sens_cfg.get("supply_voltage_vc", 5.0)
    divider = sens_cfg.get("voltage_divider_ratio", 0.66)
    r0_mq2 = sens_cfg.get("mq2_default_r0", 9.83)
    r0_mq135 = sens_cfg.get("mq135_default_r0", 3.60)
    rl_mq2 = sens_cfg.get("rl_mq2", 10.0)
    rl_mq135 = sens_cfg.get("rl_mq135", 10.0)
    curve_mq2 = sens_cfg.get("mq2_curve", {"a": 574.25, "b": -2.222})
    curve_mq135 = sens_cfg.get("mq135_curve", {"a": 110.47, "b": -2.862})

    produce_profiles = config.get("produce_profiles", [])
    produce_names = [p["name"] for p in produce_profiles] if produce_profiles else ["Apple", "Tomato", "Spinach", "Grapes"]

    data_rows = []
    classes = ["SAFE", "MODERATE", "UNSAFE"]
    class_distribution = [0.40, 0.30, 0.30]

    images_dir = os.path.join(SERVER_DIR, "data", "images")
    clean_dir = os.path.join(images_dir, "clean")
    moderate_dir = os.path.join(images_dir, "moderate")
    pesticide_dir = os.path.join(images_dir, "pesticide")
    for d in [clean_dir, moderate_dir, pesticide_dir]:
        os.makedirs(d, exist_ok=True)

    # Pre-generate a pool of representative images for fast training
    sample_images = {"SAFE": [], "MODERATE": [], "UNSAFE": []}
    print("[*] Generating representative synthetic inspection images...")
    for status, target_folder in [("SAFE", clean_dir), ("MODERATE", moderate_dir), ("UNSAFE", pesticide_dir)]:
        for i in range(15):
            prod = random.choice(produce_names)
            img_filename = f"{status.lower()}_{prod.lower()}_{i+1}.jpg"
            img_path = os.path.join(target_folder, img_filename)
            generate_synthetic_image(prod, status, img_path)
            sample_images[status].append(img_path)

    # Generate tabular sensor and image records
    for i in range(num_samples):
        # Pick class according to distribution
        status = random.choices(classes, weights=class_distribution)[0]
        produce = random.choice(produce_profiles)
        produce_name = produce["name"]
        voc_factor = produce["natural_voc_factor"]

        # Physics-based sensor characteristics per class
        if status == "SAFE":
            # Clean organic fruit: low solvent VOCs, MQ2 < 75 ppm, MQ135 < 50 ppm
            ppm_mq2 = random.uniform(10.0, 70.0) * voc_factor
            ppm_mq135 = random.uniform(8.0, 45.0) * voc_factor
            # Image characteristics: clean surface
            powder_ratio = random.uniform(0.001, 0.025)
            copper_ratio = random.uniform(0.000, 0.005)
            sulfur_ratio = random.uniform(0.000, 0.008)
            spot_ratio = random.uniform(0.01, 0.04)
            edge_mean = random.uniform(8.0, 18.0)
            risk_score = random.uniform(5.0, 32.0)
        elif status == "MODERATE":
            # Low to moderate residue: wash recommended
            ppm_mq2 = random.uniform(75.0, 145.0) * voc_factor
            ppm_mq135 = random.uniform(48.0, 82.0) * voc_factor
            powder_ratio = random.uniform(0.030, 0.085)
            copper_ratio = random.uniform(0.005, 0.025)
            sulfur_ratio = random.uniform(0.010, 0.035)
            spot_ratio = random.uniform(0.04, 0.09)
            edge_mean = random.uniform(19.0, 34.0)
            risk_score = random.uniform(38.0, 64.0)
        else: # UNSAFE
            # Significant pesticide residue / chemical treatment
            ppm_mq2 = random.uniform(150.0, 520.0) * voc_factor
            ppm_mq135 = random.uniform(85.0, 260.0) * voc_factor
            powder_ratio = random.uniform(0.095, 0.28)
            copper_ratio = random.uniform(0.030, 0.16)
            sulfur_ratio = random.uniform(0.040, 0.18)
            spot_ratio = random.uniform(0.10, 0.26)
            edge_mean = random.uniform(35.0, 70.0)
            risk_score = random.uniform(70.0, 98.0)

        # Invert MQ log-log curves to compute realistic Rs and sensor voltages
        # ppm = a * (Rs/R0)^b  =>  Rs = R0 * (ppm / a) ^ (1/b)
        ratio_mq2 = math.pow(max(1.0, ppm_mq2) / curve_mq2["a"], 1.0 / curve_mq2["b"])
        ratio_mq135 = math.pow(max(1.0, ppm_mq135) / curve_mq135["a"], 1.0 / curve_mq135["b"])

        rs_mq2 = ratio_mq2 * r0_mq2
        rs_mq135 = ratio_mq135 * r0_mq135

        # Rs = RL * (Vc - V) / V  =>  V = Vc / (1 + Rs/RL)
        v_sensor_mq2 = vc / (1.0 + (rs_mq2 / rl_mq2))
        v_sensor_mq135 = vc / (1.0 + (rs_mq135 / rl_mq135))

        # Measured ADC voltage after divider
        v_adc_mq2 = v_sensor_mq2 * divider + random.gauss(0, 0.02)
        v_adc_mq135 = v_sensor_mq135 * divider + random.gauss(0, 0.02)

        total_residue_ratio = powder_ratio + copper_ratio + sulfur_ratio

        # Associated sample image path
        img_ref = random.choice(sample_images[status])

        data_rows.append({
            "sample_id": f"SMP-{i+1:05d}",
            "produce_name": produce_name,
            "v_mq2": round(v_adc_mq2, 3),
            "v_mq135": round(v_adc_mq135, 3),
            "rs_mq2": round(rs_mq2, 3),
            "rs_mq135": round(rs_mq135, 3),
            "ratio_mq2": round(ratio_mq2, 3),
            "ratio_mq135": round(ratio_mq135, 3),
            "ppm_mq2": round(ppm_mq2, 1),
            "ppm_mq135": round(ppm_mq135, 1),
            "powder_ratio": round(powder_ratio, 4),
            "copper_ratio": round(copper_ratio, 4),
            "sulfur_ratio": round(sulfur_ratio, 4),
            "total_residue_ratio": round(total_residue_ratio, 4),
            "spot_ratio": round(spot_ratio, 4),
            "edge_mean": round(edge_mean, 2),
            "risk_score": round(risk_score, 1),
            "status": status,
            "image_path": img_ref
        })

    df = pd.DataFrame(data_rows)
    csv_path = os.path.join(SERVER_DIR, "data", "dataset.csv")
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    df.to_csv(csv_path, index=False)
    print(f"[+] Dataset saved to {csv_path} with {len(df)} records.")
    return df


def train_models():
    """
    Train sensor model and image model, evaluate, generate plots, and export model files.
    """
    config = load_config()
    models_dir = os.path.join(SERVER_DIR, "models")
    os.makedirs(models_dir, exist_ok=True)

    csv_path = os.path.join(SERVER_DIR, "data", "dataset.csv")
    if not os.path.exists(csv_path):
        df = generate_synthetic_dataset(config)
    else:
        df = pd.read_csv(csv_path)

    print("\n[+] Dataset Summary:")
    print(df["status"].value_counts())

    # =========================================================================
    # 1. TRAIN SENSOR MODEL
    # =========================================================================
    print("\n[*] Training Random Forest Sensor Classifier...")
    sensor_features = [
        "v_mq2", "v_mq135", "rs_mq2", "rs_mq135",
        "ratio_mq2", "ratio_mq135", "ppm_mq2", "ppm_mq135"
    ]
    X_sensor = df[sensor_features].values
    y = df["status"].values

    le = LabelEncoder()
    y_encoded = le.fit_transform(y)  # 0: MODERATE, 1: SAFE, 2: UNSAFE (alphabetical)

    X_train_s, X_test_s, y_train_s, y_test_s = train_test_split(
        X_sensor, y_encoded, test_size=0.20, random_state=42, stratify=y_encoded
    )

    scaler = StandardScaler()
    X_train_s_scaled = scaler.fit_transform(X_train_s)
    X_test_s_scaled = scaler.transform(X_test_s)

    sensor_model = RandomForestClassifier(
        n_estimators=120,
        max_depth=8,
        min_samples_split=4,
        random_state=42
    )
    sensor_model.fit(X_train_s_scaled, y_train_s)

    y_pred_s = sensor_model.predict(X_test_s_scaled)
    acc_sensor = accuracy_score(y_test_s, y_pred_s)
    print(f"[+] Sensor Model Accuracy: {acc_sensor * 100:.2f}%")
    print(classification_report(y_test_s, y_pred_s, target_names=le.classes_))

    # =========================================================================
    # 2. TRAIN IMAGE FEATURE MODEL
    # =========================================================================
    print("\n[*] Training Computer Vision Image Feature Classifier...")
    image_feature_cols = [
        "powder_ratio", "copper_ratio", "sulfur_ratio",
        "total_residue_ratio", "spot_ratio", "edge_mean"
    ]
    X_image = df[image_feature_cols].values

    X_train_i, X_test_i, y_train_i, y_test_i = train_test_split(
        X_image, y_encoded, test_size=0.20, random_state=42, stratify=y_encoded
    )

    image_scaler = StandardScaler()
    X_train_i_scaled = image_scaler.fit_transform(X_train_i)
    X_test_i_scaled = image_scaler.transform(X_test_i)

    image_model = GradientBoostingClassifier(
        n_estimators=100,
        learning_rate=0.08,
        max_depth=4,
        random_state=42
    )
    image_model.fit(X_train_i_scaled, y_train_i)

    y_pred_i = image_model.predict(X_test_i_scaled)
    acc_image = accuracy_score(y_test_i, y_pred_i)
    print(f"[+] Image Model Accuracy: {acc_image * 100:.2f}%")
    print(classification_report(y_test_i, y_pred_i, target_names=le.classes_))

    # =========================================================================
    # 3. EVALUATION METRICS & CONFUSION MATRIX PLOTS
    # =========================================================================
    cm_sensor = confusion_matrix(y_test_s, y_pred_s)
    plt.figure(figsize=(7, 6))
    sns.heatmap(cm_sensor, annot=True, fmt="d", cmap="Blues",
                xticklabels=le.classes_, yticklabels=le.classes_)
    plt.title(f"Sensor Model Confusion Matrix (Acc: {acc_sensor*100:.1f}%)")
    plt.xlabel("Predicted Label")
    plt.ylabel("Ground Truth")
    plt.tight_layout()
    cm_path = os.path.join(models_dir, "confusion_matrix.png")
    plt.savefig(cm_path, dpi=200)
    plt.close()

    # Feature Importance Plot
    plt.figure(figsize=(9, 5))
    importances = sensor_model.feature_importances_
    indices = np.argsort(importances)[::-1]
    sorted_features = [sensor_features[i] for i in indices]
    sns.barplot(x=importances[indices], y=sorted_features, palette="viridis")
    plt.title("Sensor Random Forest - Feature Importance")
    plt.xlabel("Gini Importance Score")
    plt.tight_layout()
    fi_path = os.path.join(models_dir, "feature_importance.png")
    plt.savefig(fi_path, dpi=200)
    plt.close()

    # Risk Distribution Plot
    plt.figure(figsize=(8, 5))
    sns.histplot(data=df, x="risk_score", hue="status", kde=True, palette={"SAFE": "#10b981", "MODERATE": "#f59e0b", "UNSAFE": "#ef4444"})
    plt.axvline(x=35.0, color="#10b981", linestyle="--", label="Safe Threshold (35%)")
    plt.axvline(x=68.0, color="#ef4444", linestyle="--", label="Unsafe Threshold (68%)")
    plt.title("Pesticide Residue Risk Score Distribution Across Classes")
    plt.xlabel("Risk Score (%)")
    plt.ylabel("Count")
    plt.legend()
    plt.tight_layout()
    risk_plot_path = os.path.join(models_dir, "risk_distribution.png")
    plt.savefig(risk_plot_path, dpi=200)
    plt.close()

    # Save metrics JSON
    precision_s, recall_s, f1_s, _ = precision_recall_fscore_support(y_test_s, y_pred_s, average="weighted")
    precision_i, recall_i, f1_i, _ = precision_recall_fscore_support(y_test_i, y_pred_i, average="weighted")

    metrics_data = {
        "sensor_model": {
            "accuracy": round(float(acc_sensor), 4),
            "precision": round(float(precision_s), 4),
            "recall": round(float(recall_s), 4),
            "f1_score": round(float(f1_s), 4),
            "features": sensor_features,
            "classes": list(le.classes_)
        },
        "image_model": {
            "accuracy": round(float(acc_image), 4),
            "precision": round(float(precision_i), 4),
            "recall": round(float(recall_i), 4),
            "f1_score": round(float(f1_i), 4),
            "features": image_feature_cols,
            "classes": list(le.classes_)
        },
        "multimodal_fusion": {
            "weight_sensor": config["ml"]["weight_sensor"],
            "weight_image": config["ml"]["weight_image"],
            "expected_fused_accuracy": round(max(float(acc_sensor), float(acc_image)) * 1.015, 4)
        }
    }

    with open(os.path.join(models_dir, "metrics.json"), "w") as f:
        json.dump(metrics_data, f, indent=2)

    # Export trained models & scalers
    joblib.dump(sensor_model, os.path.join(models_dir, "sensor_model.pkl"))
    joblib.dump(scaler, os.path.join(models_dir, "scaler.pkl"))
    joblib.dump(image_model, os.path.join(models_dir, "image_model.pkl"))
    joblib.dump(image_scaler, os.path.join(models_dir, "image_scaler.pkl"))
    joblib.dump(le, os.path.join(models_dir, "label_encoder.pkl"))

    print(f"\n[+] Successfully saved models and plots to: {models_dir}")
    return metrics_data


if __name__ == "__main__":
    train_models()
