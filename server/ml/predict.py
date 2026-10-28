"""
Predict Pipeline for Pesticide Residue Detection
Performs multimodal inference combining:
1. Physical gas sensor array (MQ-2, MQ-135 via ADS1115)
2. Computer vision residue feature extraction (powdery mildew/mancozeb, copper sulfate, sulfur dust)
3. Produce sensitivity profiles
4. Weighted probability fusion & 0-100% Risk Score calculation
"""

import os
import sys
import yaml
import numpy as np
import joblib
from PIL import Image

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.dirname(CURRENT_DIR)
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from ml.features import compute_sensor_features, extract_image_features


class PesticidePredictor:
    def __init__(self, config_path=None):
        if config_path is None:
            config_path = os.path.join(SERVER_DIR, "config.yaml")
        with open(config_path, "r") as f:
            self.config = yaml.safe_load(f)

        self.models_dir = os.path.join(SERVER_DIR, self.config["ml"].get("model_dir", "models"))
        self._load_models()

    def _load_models(self):
        """Load trained machine learning models, scalers, and label encoders."""
        sensor_model_path = os.path.join(self.models_dir, self.config["ml"]["sensor_model_file"])
        scaler_path = os.path.join(self.models_dir, self.config["ml"]["scaler_file"])
        image_model_path = os.path.join(self.models_dir, self.config["ml"]["image_model_file"])
        image_scaler_path = os.path.join(self.models_dir, "image_scaler.pkl")
        le_path = os.path.join(self.models_dir, self.config["ml"]["label_encoder_file"])

        if not all(os.path.exists(p) for p in [sensor_model_path, scaler_path, image_model_path, le_path]):
            # If models not trained yet, run training pipeline
            print("[!] Model files not found. Auto-triggering model training...")
            from ml.train_model import train_models
            train_models()

        self.sensor_model = joblib.load(sensor_model_path)
        self.sensor_scaler = joblib.load(scaler_path)
        self.image_model = joblib.load(image_model_path)
        self.image_scaler = joblib.load(image_scaler_path) if os.path.exists(image_scaler_path) else None
        self.le = joblib.load(le_path)
        self.classes = list(self.le.classes_)

    def predict(self, mq2_raw, mq135_raw, image_input, produce_type="apple"):
        """
        Execute end-to-end multimodal prediction.
        :param mq2_raw: float/int raw ADC or millivolts for MQ-2
        :param mq135_raw: float/int raw ADC or millivolts for MQ-135
        :param image_input: PIL Image, bytes, or filepath
        :param produce_type: string (e.g. 'Apple', 'Tomato', 'Spinach')
        :return: comprehensive dictionary with verdict, probabilities, risk score, and heatmap
        """
        # 1. Compute physical sensor features
        sensor_dict = compute_sensor_features(mq2_raw, mq135_raw, self.config, produce_type=produce_type)
        # Model input: [v_mq2, v_mq135, rs_mq2, rs_mq135, ratio_mq2, ratio_mq135, ppm_mq2, ppm_mq135]
        X_s = np.array([sensor_dict["feature_vector"][:8]], dtype=np.float32)
        X_s_scaled = self.sensor_scaler.transform(X_s)
        sensor_probs = self.sensor_model.predict_proba(X_s_scaled)[0]

        # 2. Extract image features
        img_features, img_metrics, heatmap_img = extract_image_features(image_input)
        # Image model features: [powder_ratio, copper_ratio, sulfur_ratio, total_residue_ratio, spot_ratio, edge_mean]
        X_i = np.array([[
            img_features[0],  # powder_ratio
            img_features[1],  # copper_ratio
            img_features[2],  # sulfur_ratio
            img_features[3],  # total_residue_ratio
            img_features[4],  # spot_ratio
            img_features[5]   # edge_mean
        ]], dtype=np.float32)

        if self.image_scaler:
            X_i_scaled = self.image_scaler.transform(X_i)
        else:
            X_i_scaled = X_i
        image_probs = self.image_model.predict_proba(X_i_scaled)[0]

        # 3. Multimodal Weighted Fusion
        w_s = self.config["ml"].get("weight_sensor", 0.65)
        w_i = self.config["ml"].get("weight_image", 0.35)
        fused_probs = (w_s * sensor_probs) + (w_i * image_probs)
        # Normalize sum
        fused_probs = fused_probs / np.sum(fused_probs)

        # Mapping classes (e.g. 0: MODERATE, 1: SAFE, 2: UNSAFE)
        prob_dict = {cls: round(float(fused_probs[idx]) * 100, 2) for idx, cls in enumerate(self.classes)}
        p_safe = prob_dict.get("SAFE", 0.0)
        p_mod = prob_dict.get("MODERATE", 0.0)
        p_unsafe = prob_dict.get("UNSAFE", 0.0)

        # 4. Continuous Risk Score (0.0 to 100.0 %)
        # Base probability contribution: SAFE ~ 5-25%, MODERATE ~ 40-65%, UNSAFE ~ 70-100%
        prob_risk = (p_mod * 0.52) + (p_unsafe * 0.90)

        # Gas concentration and visual residue exceedance boost
        mq2_exceed = max(0.0, (sensor_dict["ppm_mq2"] - 130.0) / 120.0)
        mq135_exceed = max(0.0, (sensor_dict["ppm_mq135"] - 75.0) / 90.0)
        sensor_boost = min(20.0, (mq2_exceed + mq135_exceed) * 10.0)
        visual_boost = min(20.0, img_metrics["total_residue_ratio"] * 80.0)

        raw_risk = prob_risk + sensor_boost + visual_boost
        adjusted_risk = raw_risk * sensor_dict["sensitivity"]
        final_risk = float(np.clip(adjusted_risk, 0.0, 100.0))

        # 5. Threshold evaluation
        safe_max = self.config["thresholds"].get("safe_max_risk", 35.0)
        mod_max = self.config["thresholds"].get("moderate_max_risk", 68.0)

        # Status determined by majority fused probability and risk score
        if p_unsafe >= 45.0 or final_risk > mod_max:
            status = "UNSAFE"
            verdict_badge = "danger"
            action_text = "Heavy pesticide residue detected - NOT RECOMMENDED for consumption."
            if final_risk <= mod_max:
                final_risk = max(final_risk, mod_max + 2.0)
        elif p_mod >= 35.0 or final_risk > safe_max:
            status = "MODERATE"
            verdict_badge = "warning"
            action_text = "Moderate residue detected - Thorough washing or peeling recommended."
            if final_risk <= safe_max:
                final_risk = safe_max + 5.0
        else:
            status = "SAFE"
            verdict_badge = "success"
            action_text = "Clean & Organic - Safe for direct consumption."
            if final_risk > safe_max:
                final_risk = min(final_risk, safe_max - 2.0)

        # Confidence is maximum probability in distribution
        confidence = float(np.max(fused_probs) * 100)

        return {
            "status": status,
            "verdict_badge": verdict_badge,
            "risk_score": round(final_risk, 1),
            "confidence": round(confidence, 1),
            "action_recommendation": action_text,
            "produce_type": produce_type.capitalize(),
            "probabilities": prob_dict,
            "sensor_readings": {
                "mq2_voltage": sensor_dict["v_mq2"],
                "mq135_voltage": sensor_dict["v_mq135"],
                "mq2_ppm": sensor_dict["ppm_mq2"],
                "mq135_ppm": sensor_dict["ppm_mq135"],
                "mq2_ratio": sensor_dict["ratio_mq2"],
                "mq135_ratio": sensor_dict["ratio_mq135"]
            },
            "visual_metrics": img_metrics,
            "heatmap_image": heatmap_img
        }


# Singleton instance
_predictor = None

def get_predictor():
    global _predictor
    if _predictor is None:
        _predictor = PesticidePredictor()
    return _predictor
