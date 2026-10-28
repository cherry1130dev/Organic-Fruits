"""
Feature Extraction for Pesticide Detection
Handles:
1. Physical gas sensor conversions (ADC raw -> Volts -> Rs -> Rs/R0 -> ppm)
2. Image feature extraction (color spaces, residue segmentation masks, spot frequency, texture variance)
3. Residue heatmap overlay generation
"""

import math
import numpy as np
from PIL import Image, ImageFilter, ImageOps
import io
import os


# ----------------------------------------------------------------------
# 1. Physical Gas Sensor Calculations
# ----------------------------------------------------------------------

def raw_to_voltage(raw_val, adc_ref=3.3, max_raw=32767.0, is_millivolts=False):
    """
    Convert raw ADS1115 reading, millivolts, or direct voltage to Volts.
    - If val <= 5.0: treated as direct voltage (e.g. 1.45V)
    - If 5.0 < val <= 5000.0 or is_millivolts: treated as millivolts (e.g. 1450 mV)
    - If val > 5000.0: treated as 16-bit raw ADC counts (e.g. 14400 / 32767 * 3.3)
    """
    try:
        val = float(raw_val)
    except (ValueError, TypeError):
        val = 0.0

    if is_millivolts:
        v = val / 1000.0
    elif val <= 5.0:
        v = val
    elif val <= 5000.0:
        v = val / 1000.0
    else:
        v = (val / max_raw) * adc_ref

    # Clamping within realistic 0.05V to 5.0V range
    return max(0.05, min(v, 5.0))


def calculate_rs(voltage, vc=5.0, rl=10.0, divider_ratio=0.66):
    """
    Calculate sensor surface resistance Rs (kOhms).
    Equation: Rs = RL * (Vc - V_sensor) / V_sensor
    If voltage divider is present between 5V MQ sensor output and 3.3V ADC:
    V_sensor = V_measured / divider_ratio
    """
    # Reconstruct actual 5V sensor output voltage
    v_actual = voltage / divider_ratio if divider_ratio > 0 else voltage
    v_actual = max(0.05, min(v_actual, vc - 0.05))

    # Rs in kOhms
    rs = rl * (vc - v_actual) / v_actual
    return max(0.1, rs)


def calculate_ppm(rs, r0, a, b):
    """
    Calculate gas concentration in parts per million (ppm) using log-log sensor curve:
    ppm = a * (Rs / R0) ^ b
    """
    if r0 <= 0 or rs <= 0:
        return 0.0
    ratio = rs / r0
    # Prevent math overflow or negative base
    if ratio <= 0:
        return 0.0
    try:
        ppm = a * math.pow(ratio, b)
    except (ValueError, OverflowError):
        ppm = 0.0
    return max(0.0, min(ppm, 10000.0))


def compute_sensor_features(mq2_val, mq135_val, config, produce_type="apple"):
    """
    Compute comprehensive sensor features given raw readings and configuration.
    Returns dictionary with raw voltages, Rs, Rs/R0, ppm, and normalized risk vector.
    """
    sens_cfg = config.get("sensors", {})
    vc = sens_cfg.get("supply_voltage_vc", 5.0)
    divider = sens_cfg.get("voltage_divider_ratio", 0.66)
    rl_mq2 = sens_cfg.get("rl_mq2", 10.0)
    rl_mq135 = sens_cfg.get("rl_mq135", 10.0)
    r0_mq2 = sens_cfg.get("mq2_default_r0", 9.83)
    r0_mq135 = sens_cfg.get("mq135_default_r0", 3.60)

    # Convert voltages
    v_mq2 = raw_to_voltage(mq2_val, adc_ref=sens_cfg.get("adc_voltage_ref", 3.3))
    v_mq135 = raw_to_voltage(mq135_val, adc_ref=sens_cfg.get("adc_voltage_ref", 3.3))

    # Compute Rs
    rs_mq2 = calculate_rs(v_mq2, vc=vc, rl=rl_mq2, divider_ratio=divider)
    rs_mq135 = calculate_rs(v_mq135, vc=vc, rl=rl_mq135, divider_ratio=divider)

    # Rs/R0 ratios
    ratio_mq2 = rs_mq2 / r0_mq2
    ratio_mq135 = rs_mq135 / r0_mq135

    # Compute PPM
    curve_mq2 = sens_cfg.get("mq2_curve", {"a": 574.25, "b": -2.222})
    curve_mq135 = sens_cfg.get("mq135_curve", {"a": 110.47, "b": -2.862})
    ppm_mq2 = calculate_ppm(rs_mq2, r0_mq2, curve_mq2["a"], curve_mq2["b"])
    ppm_mq135 = calculate_ppm(rs_mq135, r0_mq135, curve_mq135["a"], curve_mq135["b"])

    # Produce profile natural baseline adjustment
    produce_list = config.get("produce_profiles", [])
    profile = next((p for p in produce_list if p["id"].lower() == produce_type.lower() or p["name"].lower() == produce_type.lower()), None)
    voc_factor = profile["natural_voc_factor"] if profile else 1.0
    sensitivity = profile["risk_sensitivity"] if profile else 1.0

    # Adjusted PPM after discounting natural fruit emissions
    net_ppm_mq2 = max(0.0, ppm_mq2 - (35.0 * (voc_factor - 1.0)))
    net_ppm_mq135 = max(0.0, ppm_mq135 - (25.0 * (voc_factor - 1.0)))

    # Feature array for ML model (numeric vector):
    # [v_mq2, v_mq135, rs_mq2, rs_mq135, ratio_mq2, ratio_mq135, ppm_mq2, ppm_mq135, net_ppm_mq2, net_ppm_mq135]
    feature_vector = [
        v_mq2,
        v_mq135,
        rs_mq2,
        rs_mq135,
        ratio_mq2,
        ratio_mq135,
        ppm_mq2,
        ppm_mq135,
        net_ppm_mq2,
        net_ppm_mq135
    ]

    return {
        "v_mq2": round(v_mq2, 3),
        "v_mq135": round(v_mq135, 3),
        "rs_mq2": round(rs_mq2, 3),
        "rs_mq135": round(rs_mq135, 3),
        "ratio_mq2": round(ratio_mq2, 3),
        "ratio_mq135": round(ratio_mq135, 3),
        "ppm_mq2": round(ppm_mq2, 1),
        "ppm_mq135": round(ppm_mq135, 1),
        "net_ppm_mq2": round(net_ppm_mq2, 1),
        "net_ppm_mq135": round(net_ppm_mq135, 1),
        "sensitivity": sensitivity,
        "feature_vector": feature_vector
    }


# ----------------------------------------------------------------------
# 2. Image Feature Extraction
# ----------------------------------------------------------------------

def extract_image_features(image_input):
    """
    Extracts computer vision features from an image (PIL Image, bytes, or file path).
    Features reflect pesticide surface residues:
    - Chalky / powder residue deposits (white/pale clusters)
    - Copper sulfate / fungicide stains (cyan/blue)
    - Sulfur spray marks (pale yellow)
    - Uneven chemical spray spots / droplet rings
    - Texture contrast and high-frequency edge variance
    Returns:
    - feature_vector (list of 14 floats)
    - metrics_dict (human-readable metrics)
    - heatmap_img (PIL Image with highlighted residue overlay)
    """
    if isinstance(image_input, (str, os.PathLike)):
        img = Image.open(image_input).convert("RGB")
    elif isinstance(image_input, (bytes, bytearray)):
        img = Image.open(io.BytesIO(image_input)).convert("RGB")
    elif isinstance(image_input, Image.Image):
        img = image_input.convert("RGB")
    else:
        raise ValueError("Unsupported image input type")

    # Resize to standardized inspection resolution (256x256) for stable feature computation
    target_size = (256, 256)
    img_resized = img.resize(target_size, Image.Resampling.BILINEAR)
    img_np = np.array(img_resized, dtype=np.float32)

    # Normalized RGB channels
    r = img_np[:, :, 0]
    g = img_np[:, :, 1]
    b = img_np[:, :, 2]
    brightness = 0.299 * r + 0.587 * g + 0.114 * b

    # HSV Conversion in NumPy
    max_c = np.max(img_np, axis=2)
    min_c = np.min(img_np, axis=2)
    delta = max_c - min_c + 1e-6

    # Saturation (0 to 255)
    sat = np.where(max_c == 0, 0, (delta / (max_c + 1e-6)) * 255.0)
    val = max_c

    # Hue calculation (0 to 360)
    hue = np.zeros_like(max_c)
    mask_r = (max_c == r) & (delta > 0)
    mask_g = (max_c == g) & (delta > 0)
    mask_b = (max_c == b) & (delta > 0)

    hue[mask_r] = (60.0 * ((g[mask_r] - b[mask_r]) / delta[mask_r])) % 360.0
    hue[mask_g] = (60.0 * ((b[mask_g] - r[mask_g]) / delta[mask_g]) + 120.0) % 360.0
    hue[mask_b] = (60.0 * ((r[mask_b] - g[mask_b]) / delta[mask_b]) + 240.0) % 360.0

    # 1. White Powdery Residue Mask (Mancozeb, Kaolin, wettable powders)
    # Brightness is high (Val > 185), and Saturation is low (Sat < 65)
    powder_mask = (val > 185) & (sat < 65)
    powder_ratio = float(np.sum(powder_mask)) / (target_size[0] * target_size[1])

    # 2. Copper Sulfate / Fungicide Mask (Cyan/Blue-Green droplets)
    # Hue between 150 and 220, Saturation > 50, Val > 80
    copper_mask = (hue >= 150) & (hue <= 220) & (sat > 50) & (val > 80)
    copper_ratio = float(np.sum(copper_mask)) / (target_size[0] * target_size[1])

    # 3. Sulfur Powder Mask (Pale sulfur yellow dust)
    # Hue between 40 and 65, Saturation > 45, Val > 150
    sulfur_mask = (hue >= 40) & (hue <= 65) & (sat > 45) & (val > 150)
    sulfur_ratio = float(np.sum(sulfur_mask)) / (target_size[0] * target_size[1])

    # Total combined residue mask
    combined_residue_mask = powder_mask | copper_mask | sulfur_mask
    total_residue_ratio = float(np.sum(combined_residue_mask)) / (target_size[0] * target_size[1])

    # 4. High-Frequency Surface Spot / Texture Variance (Laplacian Edge Detection)
    gray_pil = ImageOps.grayscale(img_resized)
    edges = gray_pil.filter(ImageFilter.FIND_EDGES)
    edge_np = np.array(edges, dtype=np.float32)
    edge_mean = float(np.mean(edge_np))
    edge_std = float(np.std(edge_np))

    # Spot roughness index (ratio of isolated high-gradient pixels indicative of droplet spots)
    spot_ratio = float(np.sum(edge_np > 55.0)) / (target_size[0] * target_size[1])

    # 5. Color channel statistics
    r_mean, r_std = float(np.mean(r)), float(np.std(r))
    g_mean, g_std = float(np.mean(g)), float(np.std(g))
    b_mean, b_std = float(np.mean(b)), float(np.std(b))
    val_mean, val_std = float(np.mean(val)), float(np.std(val))
    sat_mean = float(np.mean(sat))

    # Compile 14-dimensional feature vector
    feature_vector = [
        powder_ratio,
        copper_ratio,
        sulfur_ratio,
        total_residue_ratio,
        spot_ratio,
        edge_mean,
        edge_std,
        r_mean,
        r_std,
        g_mean,
        g_std,
        b_mean,
        b_std,
        sat_mean
    ]

    metrics = {
        "powder_residue_ratio": round(powder_ratio * 100, 2),
        "copper_fungicide_ratio": round(copper_ratio * 100, 2),
        "sulfur_dust_ratio": round(sulfur_ratio * 100, 2),
        "total_residue_ratio": round(total_residue_ratio * 100, 2),
        "spot_texture_score": round(spot_ratio * 100, 2),
        "edge_roughness": round(edge_mean, 2),
        "surface_cleanliness": round(max(0.0, 100.0 - (total_residue_ratio * 250.0 + spot_ratio * 150.0)), 1)
    }

    # Generate visual heatmap overlay
    overlay_np = np.array(img_resized, dtype=np.uint8).copy()
    if np.any(combined_residue_mask):
        # Paint residue regions with semi-transparent fluorescent crimson / yellow highlight
        overlay_np[powder_mask, 0] = np.clip(overlay_np[powder_mask, 0] * 0.4 + 220, 0, 255)
        overlay_np[powder_mask, 1] = np.clip(overlay_np[powder_mask, 1] * 0.4 + 40, 0, 255)
        overlay_np[powder_mask, 2] = np.clip(overlay_np[powder_mask, 2] * 0.4 + 40, 0, 255)

        overlay_np[copper_mask, 0] = np.clip(overlay_np[copper_mask, 0] * 0.4 + 30, 0, 255)
        overlay_np[copper_mask, 1] = np.clip(overlay_np[copper_mask, 1] * 0.4 + 200, 0, 255)
        overlay_np[copper_mask, 2] = np.clip(overlay_np[copper_mask, 2] * 0.4 + 230, 0, 255)

        overlay_np[sulfur_mask, 0] = np.clip(overlay_np[sulfur_mask, 0] * 0.4 + 235, 0, 255)
        overlay_np[sulfur_mask, 1] = np.clip(overlay_np[sulfur_mask, 1] * 0.4 + 220, 0, 255)
        overlay_np[sulfur_mask, 2] = np.clip(overlay_np[sulfur_mask, 2] * 0.4 + 20, 0, 255)

    heatmap_img = Image.fromarray(overlay_np)

    return feature_vector, metrics, heatmap_img
