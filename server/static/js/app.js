/**
 * IoT Pesticide Detection - Interactive Client Application
 * Handles:
 * - Real-time polling & WebSocket/SSE-like updates
 * - Animated SVG circular and semi-circular gauges
 * - Multi-view tab navigation (Live Monitor, History, Settings, About)
 * - Chart.js historical trend lines
 * - Browser audio synthesis buzzer on UNSAFE
 * - Browser push notifications
 * - Image inspection toggle (Original vs Heatmap) & modal zoom
 * - Manual inspection upload & ESP32 simulator trigger
 * - PWA Service Worker registration
 */

// Application State
const state = {
  activeTab: "monitor",
  currentRecord: null,
  historyData: [],
  config: null,
  historyChart: null,
  activeImageView: "raw", // "raw" or "heatmap"
  lastAlertId: null
};

// DOM Content Loaded Initializer
document.addEventListener("DOMContentLoaded", () => {
  initPWA();
  initNavigation();
  initGaugeElements();
  loadLatestScan();
  loadSettings();
  setupEventListeners();

  // Auto-refresh monitor every 3.5 seconds
  setInterval(loadLatestScan, 3500);
});

// -----------------------------------------------------------------------------
// 1. Navigation & View Controller
// -----------------------------------------------------------------------------
function initNavigation() {
  const tabs = document.querySelectorAll(".nav-tab-btn");
  tabs.forEach(btn => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.tab;
      switchTab(target);
    });
  });
}

function switchTab(tabId) {
  state.activeTab = tabId;
  document.querySelectorAll(".nav-tab-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.tab === tabId);
  });
  document.querySelectorAll(".tab-view").forEach(view => {
    view.classList.toggle("active", view.id === `view-${tabId}`);
  });

  if (tabId === "history") {
    loadHistory();
  } else if (tabId === "settings") {
    loadSettings();
  }
}

// -----------------------------------------------------------------------------
// 2. Audio Alert Synthesizer (Web Audio API)
// -----------------------------------------------------------------------------
function playAlertTone(type = "unsafe") {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();

    osc.connect(gain);
    gain.connect(ctx.destination);

    if (type === "unsafe") {
      // Rapid warning high-frequency pulse
      osc.type = "sawtooth";
      osc.frequency.setValueAtTime(880, ctx.currentTime);
      osc.frequency.setValueAtTime(440, ctx.currentTime + 0.15);
      osc.frequency.setValueAtTime(880, ctx.currentTime + 0.3);
      gain.gain.setValueAtTime(0.3, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.6);
      osc.start();
      osc.stop(ctx.currentTime + 0.6);
    } else {
      // Gentle safe chime
      osc.type = "sine";
      osc.frequency.setValueAtTime(523.25, ctx.currentTime);
      osc.frequency.setValueAtTime(659.25, ctx.currentTime + 0.12);
      gain.gain.setValueAtTime(0.15, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.4);
      osc.start();
      osc.stop(ctx.currentTime + 0.4);
    }
  } catch (e) {
    console.warn("Audio Context playback prevented by browser policy", e);
  }
}

// -----------------------------------------------------------------------------
// 3. Browser Push Notifications
// -----------------------------------------------------------------------------
function requestNotificationPermission() {
  if ("Notification" in window && Notification.permission === "default") {
    Notification.requestPermission();
  }
}

function triggerBrowserNotification(title, body) {
  if ("Notification" in window && Notification.permission === "granted") {
    new Notification(title, {
      body: body,
      icon: "/static/icons/icon-192.png"
    });
  }
}

// -----------------------------------------------------------------------------
// 4. Live Monitor & Gauge Rendering
// -----------------------------------------------------------------------------
function initGaugeElements() {
  // Initial zero state
  updateVerdictDial(0, "SAFE");
  updateSemiGauge("mq2", 0, 500);
  updateSemiGauge("mq135", 0, 300);
}

async function loadLatestScan() {
  try {
    const res = await fetch("/api/latest");
    if (!res.ok) return;
    const data = await res.json();
    if (!data || !data.id) return;

    // Only update if new record arrived
    if (!state.currentRecord || state.currentRecord.id !== data.id) {
      const isInitial = state.currentRecord === null;
      state.currentRecord = data;
      renderMonitorData(data);

      if (!isInitial && data.status === "UNSAFE") {
        playAlertTone("unsafe");
        triggerBrowserNotification(
          "⚠️ High Pesticide Risk Detected!",
          `${data.produce_type}: Risk score ${data.risk_score}%. Immediate washing or peeling required.`
        );
        showToast(`Alert: Heavy residue detected in ${data.produce_type}!`, "danger");
      }
    }
  } catch (err) {
    console.error("Error loading latest scan:", err);
  }
}

function renderMonitorData(record) {
  // Update Header / Produce Tag
  document.getElementById("disp-produce").textContent = record.produce_type || "Produce";
  const dateStr = record.timestamp ? new Date(record.timestamp).toLocaleTimeString() : "--:--:--";
  document.getElementById("disp-time").textContent = dateStr;

  // Update Verdict Hero Card
  const heroCard = document.getElementById("verdict-card");
  heroCard.className = `glass-panel verdict-hero-card status-${(record.status || "safe").toLowerCase()}`;

  // Update Verdict Dial
  const risk = parseFloat(record.risk_score || 0);
  updateVerdictDial(risk, record.status);

  // Verdict Badge
  const badge = document.getElementById("disp-badge");
  badge.textContent = record.status || "UNKNOWN";
  badge.className = `verdict-badge badge-${(record.status || "safe").toLowerCase()}`;

  // Action Recommendation
  document.getElementById("disp-action").textContent = record.action_recommendation || "System initialized. Ready to inspect.";

  // Confidence & Probabilities
  const confElem = document.getElementById("disp-conf");
  if (confElem) {
    confElem.textContent = `${record.confidence || 0}%`;
  }
  
  // If record has probabilities from analyze API
  if (record.probabilities) {
    document.getElementById("prob-safe-val").textContent = `${record.probabilities.SAFE || 0}%`;
    document.getElementById("prob-safe-bar").style.width = `${record.probabilities.SAFE || 0}%`;

    document.getElementById("prob-mod-val").textContent = `${record.probabilities.MODERATE || 0}%`;
    document.getElementById("prob-mod-bar").style.width = `${record.probabilities.MODERATE || 0}%`;

    document.getElementById("prob-unsafe-val").textContent = `${record.probabilities.UNSAFE || 0}%`;
    document.getElementById("prob-unsafe-bar").style.width = `${record.probabilities.UNSAFE || 0}%`;
  }

  // Update Gas Sensor Semi-Gauges
  const mq2Ppm = parseFloat(record.ppm_mq2 || 0);
  const mq135Ppm = parseFloat(record.ppm_mq135 || 0);
  updateSemiGauge("mq2", mq2Ppm, 400);
  updateSemiGauge("mq135", mq135Ppm, 250);

  // Sensor sub-metrics
  document.getElementById("mq2-volt").textContent = `${parseFloat(record.v_mq2 || 0).toFixed(2)}V`;
  document.getElementById("mq2-ratio").textContent = parseFloat(record.ratio_mq2 || 0).toFixed(2);
  document.getElementById("mq135-volt").textContent = `${parseFloat(record.v_mq135 || 0).toFixed(2)}V`;
  document.getElementById("mq135-ratio").textContent = parseFloat(record.ratio_mq135 || 0).toFixed(2);

  // Visual Metrics
  document.getElementById("disp-residue-ratio").textContent = `${parseFloat(record.total_residue_ratio || 0).toFixed(1)}%`;
  document.getElementById("disp-powder-ratio").textContent = `${parseFloat(record.powder_ratio || 0).toFixed(1)}%`;
  document.getElementById("disp-cleanliness").textContent = `${parseFloat(record.surface_cleanliness || 100).toFixed(0)}%`;

  // Update Image Previews
  updateInspectionImage(record);
}

function updateVerdictDial(riskPercent, status) {
  const dialBar = document.getElementById("verdict-dial-bar");
  const scoreNum = document.getElementById("verdict-score-num");
  scoreNum.textContent = Math.round(riskPercent);

  // Circumference = 2 * PI * r = 2 * PI * 70 = 439.8
  const circumference = 440;
  const offset = circumference - (Math.min(100, Math.max(0, riskPercent)) / 100) * circumference;
  dialBar.style.strokeDashoffset = offset;

  if (status === "SAFE") {
    dialBar.style.stroke = "var(--accent-safe)";
  } else if (status === "MODERATE") {
    dialBar.style.stroke = "var(--accent-mod)";
  } else {
    dialBar.style.stroke = "var(--accent-unsafe)";
  }
}

function updateSemiGauge(sensorId, value, maxPpm) {
  const bar = document.getElementById(`${sensorId}-bar`);
  const numElem = document.getElementById(`${sensorId}-val`);
  numElem.textContent = Math.round(value);

  // Semi-circle circumference = PI * r = PI * 58 = ~182.2 (track total 220)
  const maxArc = 220;
  const ratio = Math.min(1.0, value / maxPpm);
  const offset = maxArc - (ratio * maxArc);
  bar.style.strokeDashoffset = offset;

  if (value > (maxPpm * 0.65)) {
    bar.style.stroke = "var(--accent-unsafe)";
  } else if (value > (maxPpm * 0.35)) {
    bar.style.stroke = "var(--accent-mod)";
  } else {
    bar.style.stroke = "var(--accent-safe)";
  }
}

function updateInspectionImage(record) {
  const previewImg = document.getElementById("preview-img");
  const rawUrl = record.image_url || "/static/icons/icon-512.png";
  const heatmapUrl = record.heatmap_url || rawUrl;

  if (state.activeImageView === "heatmap" && record.heatmap_url) {
    previewImg.src = heatmapUrl;
  } else {
    previewImg.src = rawUrl;
  }
}

// -----------------------------------------------------------------------------
// 5. History & Chart.js Visualizer
// -----------------------------------------------------------------------------
async function loadHistory() {
  const produce = document.getElementById("filter-produce").value;
  const status = document.getElementById("filter-status").value;

  try {
    const res = await fetch(`/api/history?limit=30&produce=${encodeURIComponent(produce)}&status=${encodeURIComponent(status)}`);
    const data = await res.json();
    state.historyData = data.items || [];
    renderHistoryTable(state.historyData);
    renderHistoryChart(state.historyData);
  } catch (err) {
    console.error("Error loading history:", err);
  }
}

function renderHistoryTable(items) {
  const tbody = document.getElementById("history-tbody");
  tbody.innerHTML = "";

  if (items.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-dim); padding: 2rem;">No screening records matching filter criteria.</td></tr>`;
    return;
  }

  items.forEach(item => {
    const tr = document.createElement("tr");
    const statusCls = (item.status || "safe").toLowerCase();
    const dateStr = item.timestamp ? new Date(item.timestamp).toLocaleString() : "N/A";
    const imgUrl = item.heatmap_url || item.image_url || "/static/icons/icon-192.png";

    tr.innerHTML = `
      <td>
        <img src="${imgUrl}" alt="${item.produce_type}" class="thumb-preview" onclick="openModal('${imgUrl}')" title="Click to inspect">
      </td>
      <td><strong>${item.produce_type}</strong></td>
      <td><span class="mini-badge ${statusCls}">${item.status}</span></td>
      <td><strong>${item.risk_score}%</strong></td>
      <td><span style="font-family: var(--font-mono);">${item.ppm_mq2} / ${item.ppm_mq135} ppm</span></td>
      <td>${item.total_residue_ratio || 0}%</td>
      <td style="font-size: 0.78rem; color: var(--text-dim);">${dateStr}</td>
    `;
    tbody.appendChild(tr);
  });
}

function renderHistoryChart(items) {
  const ctx = document.getElementById("history-chart").getContext("2d");
  const reversed = [...items].reverse();

  const labels = reversed.map(i => i.timestamp ? new Date(i.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : "");
  const mq2Data = reversed.map(i => i.ppm_mq2 || 0);
  const mq135Data = reversed.map(i => i.ppm_mq135 || 0);
  const riskData = reversed.map(i => i.risk_score || 0);

  if (state.historyChart) {
    state.historyChart.destroy();
  }

  state.historyChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: labels,
      datasets: [
        {
          label: "MQ-2 Solvent PPM",
          data: mq2Data,
          borderColor: "#06b6d4",
          backgroundColor: "rgba(6, 182, 212, 0.1)",
          tension: 0.35,
          borderWidth: 2,
          pointRadius: 3
        },
        {
          label: "MQ-135 Agrochemical PPM",
          data: mq135Data,
          borderColor: "#8b5cf6",
          backgroundColor: "rgba(139, 92, 246, 0.1)",
          tension: 0.35,
          borderWidth: 2,
          pointRadius: 3
        },
        {
          label: "Risk Score %",
          data: riskData,
          borderColor: "#ef4444",
          borderDash: [5, 5],
          tension: 0.35,
          borderWidth: 2,
          pointRadius: 2
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          labels: { color: "#94a3b8", font: { family: "Inter", size: 11 } }
        }
      },
      scales: {
        x: {
          ticks: { color: "#64748b", font: { size: 10 } },
          grid: { color: "rgba(255, 255, 255, 0.04)" }
        },
        y: {
          ticks: { color: "#64748b", font: { size: 10 } },
          grid: { color: "rgba(255, 255, 255, 0.04)" }
        }
      }
    }
  });
}

function exportHistoryCSV() {
  if (!state.historyData || state.historyData.length === 0) {
    showToast("No records available to export", "warning");
    return;
  }

  const headers = ["ID", "Timestamp", "Produce", "Status", "Risk_Score", "Confidence", "MQ2_PPM", "MQ135_PPM", "Residue_Ratio", "Recommendation"];
  const rows = state.historyData.map(i => [
    i.id,
    `"${i.timestamp}"`,
    `"${i.produce_type}"`,
    i.status,
    i.risk_score,
    i.confidence,
    i.ppm_mq2,
    i.ppm_mq135,
    i.total_residue_ratio,
    `"${(i.action_recommendation || "").replace(/"/g, '""')}"`
  ]);

  const csvContent = "data:text/csv;charset=utf-8," + [headers.join(","), ...rows.map(e => e.join(","))].join("\n");
  const encodedUri = encodeURI(csvContent);
  const link = document.createElement("a");
  link.setAttribute("href", encodedUri);
  link.setAttribute("download", `pesticide_scans_${Date.now()}.csv`);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  showToast("CSV export generated successfully", "success");
}

// -----------------------------------------------------------------------------
// 6. Settings & Sensor Calibration
// -----------------------------------------------------------------------------
async function loadSettings() {
  try {
    const res = await fetch("/api/settings");
    const cfg = await res.json();
    state.config = cfg;

    // Populate sliders and inputs
    document.getElementById("slider-safe-risk").value = cfg.thresholds.safe_max_risk;
    document.getElementById("val-safe-risk").textContent = `${cfg.thresholds.safe_max_risk}%`;

    document.getElementById("slider-mod-risk").value = cfg.thresholds.moderate_max_risk;
    document.getElementById("val-mod-risk").textContent = `${cfg.thresholds.moderate_max_risk}%`;

    document.getElementById("slider-sensor-weight").value = cfg.ml.weight_sensor;
    document.getElementById("val-sensor-weight").textContent = `${Math.round(cfg.ml.weight_sensor * 100)}%`;

    // Baseline R0 values
    document.getElementById("mq2-r0-val").textContent = `${cfg.sensors.mq2_default_r0} kΩ`;
    document.getElementById("mq135-r0-val").textContent = `${cfg.sensors.mq135_default_r0} kΩ`;

    // Telegram inputs
    const tg = cfg.alerts.telegram || {};
    document.getElementById("tg-enabled").checked = !!tg.enabled;
    document.getElementById("tg-token").value = tg.bot_token || "";
    document.getElementById("tg-chatid").value = tg.chat_id || "";
  } catch (err) {
    console.error("Error loading settings:", err);
  }
}

async function saveSettings() {
  const safeRisk = parseFloat(document.getElementById("slider-safe-risk").value);
  const modRisk = parseFloat(document.getElementById("slider-mod-risk").value);
  const sensorWeight = parseFloat(document.getElementById("slider-sensor-weight").value);
  const tgEnabled = document.getElementById("tg-enabled").checked;
  const tgToken = document.getElementById("tg-token").value.trim();
  const tgChatId = document.getElementById("tg-chatid").value.trim();

  const payload = {
    safe_max_risk: safeRisk,
    moderate_max_risk: modRisk,
    weight_sensor: sensorWeight,
    telegram: {
      enabled: tgEnabled,
      bot_token: tgToken,
      chat_id: tgChatId
    }
  };

  try {
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      showToast("Configuration saved successfully", "success");
    } else {
      showToast("Failed to save configuration", "danger");
    }
  } catch (e) {
    showToast("Error updating settings", "danger");
  }
}

async function calibrateSensors() {
  const btn = document.getElementById("btn-calibrate");
  btn.disabled = true;
  btn.innerHTML = `<span class="pulse-dot"></span> Calibrating in clean air...`;

  try {
    const res = await fetch("/api/calibrate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mq2_raw: 1.15, mq135_raw: 1.85 })
    });
    const data = await res.json();
    if (res.ok) {
      document.getElementById("mq2-r0-val").textContent = `${data.mq2_r0} kΩ`;
      document.getElementById("mq135-r0-val").textContent = `${data.mq135_r0} kΩ`;
      showToast("Sensors calibrated to clean-air baseline!", "success");
    } else {
      showToast(`Calibration error: ${data.error}`, "danger");
    }
  } catch (err) {
    showToast("Calibration request failed", "danger");
  } finally {
    btn.disabled = false;
    btn.innerHTML = `Start Clean-Air Calibration`;
  }
}

async function testTelegramAlert() {
  try {
    const res = await fetch("/api/test-telegram", { method: "POST" });
    const data = await res.json();
    if (data.success) {
      showToast("Test notification sent to Telegram!", "success");
    } else {
      showToast(`Telegram test failed: ${data.message}`, "warning");
    }
  } catch (err) {
    showToast("Failed to trigger Telegram alert", "danger");
  }
}

// -----------------------------------------------------------------------------
// 7. Manual Simulator & Test Scan Trigger
// -----------------------------------------------------------------------------
async function triggerSimulatedScan() {
  const produceSelect = document.getElementById("sel-produce");
  const produce = produceSelect.value;
  const sampleType = document.getElementById("sel-sample-type").value;
  const scanBtn = document.getElementById("btn-run-scan");

  scanBtn.disabled = true;
  scanBtn.innerHTML = `<span class="pulse-dot"></span> Analyzing Sample...`;

  // Simulate realistic voltages based on selection
  let mq2_v = 0.85, mq135_v = 1.95;
  if (sampleType === "MODERATE") {
    mq2_v = 1.15;
    mq135_v = 2.25;
  } else if (sampleType === "UNSAFE") {
    mq2_v = 1.55;
    mq135_v = 2.55;
  }

  // Check if manual image file chosen
  const fileInput = document.getElementById("manual-image-input");
  const formData = new FormData();
  formData.append("mq2_raw", mq2_v);
  formData.append("mq135_raw", mq135_v);
  formData.append("produce_type", produce);

  if (fileInput.files.length > 0) {
    formData.append("image", fileInput.files[0]);
  }

  try {
    const res = await fetch("/api/analyze", {
      method: "POST",
      body: formData
    });
    const result = await res.json();
    if (res.ok) {
      state.currentRecord = result;
      renderMonitorData(result);
      showToast(`Analysis complete: ${result.status} (${result.risk_score}%)`, result.status === "SAFE" ? "success" : (result.status === "MODERATE" ? "warning" : "danger"));
      if (result.status === "UNSAFE") {
        playAlertTone("unsafe");
      }
    } else {
      showToast(`Error: ${result.message || "Analysis failed"}`, "danger");
    }
  } catch (err) {
    showToast("Scan trigger failed", "danger");
  } finally {
    scanBtn.disabled = false;
    scanBtn.innerHTML = `Analyze via Upload / Simulation`;
  }
}

// -----------------------------------------------------------------------------
// 8. Laptop Webcam Controller (In-Browser Capture)
// -----------------------------------------------------------------------------
async function startWebcam() {
  const video = document.getElementById("webcam-feed");
  const previewImg = document.getElementById("preview-img");
  const reticle = document.getElementById("webcam-reticle");
  const legend = document.getElementById("heatmap-legend");

  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 640 }, height: { ideal: 480 } }
    });
    state.webcamStream = stream;
    video.srcObject = stream;
    video.style.display = "block";
    previewImg.style.display = "none";
    if (reticle) reticle.style.display = "block";
    if (legend) legend.style.display = "none";
    showToast("Laptop webcam active. Position produce in frame.", "info");
  } catch (err) {
    console.error("Webcam access error:", err);
    showToast("Could not access laptop webcam. Please grant camera permission.", "danger");
  }
}

function stopWebcam() {
  if (state.webcamStream) {
    state.webcamStream.getTracks().forEach(track => track.stop());
    state.webcamStream = null;
  }
  const video = document.getElementById("webcam-feed");
  const previewImg = document.getElementById("preview-img");
  const reticle = document.getElementById("webcam-reticle");
  const legend = document.getElementById("heatmap-legend");

  video.style.display = "none";
  previewImg.style.display = "block";
  if (reticle) reticle.style.display = "none";
  if (legend) legend.style.display = "flex";
}

async function captureWebcamAndAnalyze() {
  const snapBtn = document.getElementById("btn-webcam-snap");
  const video = document.getElementById("webcam-feed");
  const canvas = document.getElementById("webcam-canvas");

  // If webcam is not running, activate it first
  if (!state.webcamStream) {
    await startWebcam();
    document.getElementById("btn-view-webcam").classList.add("active");
    document.getElementById("btn-view-raw").classList.remove("active");
    document.getElementById("btn-view-heatmap").classList.remove("active");
    snapBtn.innerHTML = `<span class="pulse-dot"></span> Snap Produce Now!`;
    return;
  }

  // Flash / Capture frame
  snapBtn.disabled = true;
  snapBtn.innerHTML = `<span class="pulse-dot"></span> Analyzing Snapshot...`;

  canvas.width = video.videoWidth || 640;
  canvas.height = video.videoHeight || 480;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

  canvas.toBlob(async (blob) => {
    if (!blob) {
      showToast("Failed to capture image from webcam", "danger");
      snapBtn.disabled = false;
      snapBtn.innerHTML = `📸 Snap Laptop Webcam & Analyze`;
      return;
    }

    const produce = document.getElementById("sel-produce").value;
    const sampleType = document.getElementById("sel-sample-type").value;

    let mq2_v = 0.82, mq135_v = 1.96;
    if (sampleType === "MODERATE") {
      mq2_v = 1.15;
      mq135_v = 2.25;
    } else if (sampleType === "UNSAFE") {
      mq2_v = 1.58;
      mq135_v = 2.62;
    }

    const formData = new FormData();
    formData.append("mq2_raw", mq2_v);
    formData.append("mq135_raw", mq135_v);
    formData.append("produce_type", produce);
    formData.append("image", blob, "laptop_webcam_scan.jpg");

    try {
      const res = await fetch("/api/analyze", {
        method: "POST",
        body: formData
      });
      const result = await res.json();
      if (res.ok) {
        state.currentRecord = result;
        renderMonitorData(result);

        // Switch to Inspected Photo / Heatmap view so user sees analysis
        stopWebcam();
        document.getElementById("btn-view-raw").classList.add("active");
        document.getElementById("btn-view-webcam").classList.remove("active");
        document.getElementById("btn-view-heatmap").classList.remove("active");
        state.activeImageView = "raw";
        updateInspectionImage(result);

        showToast(`Scan complete: ${result.status} (Risk: ${result.risk_score}%)`, result.status === "SAFE" ? "success" : "danger");
        if (result.status === "UNSAFE") {
          playAlertTone("unsafe");
        }
      } else {
        showToast(`Analysis error: ${result.message}`, "danger");
      }
    } catch (err) {
      showToast("Webcam scan request failed", "danger");
    } finally {
      snapBtn.disabled = false;
      snapBtn.innerHTML = `📸 Snap Laptop Webcam & Analyze`;
    }
  }, "image/jpeg", 0.90);
}

// -----------------------------------------------------------------------------
// 9. Event Listeners & Lightbox Modal
// -----------------------------------------------------------------------------
function setupEventListeners() {
  // Image Toggle Buttons
  const btnWebcam = document.getElementById("btn-view-webcam");
  const btnRaw = document.getElementById("btn-view-raw");
  const btnHeatmap = document.getElementById("btn-view-heatmap");

  if (btnWebcam) {
    btnWebcam.addEventListener("click", () => {
      btnWebcam.classList.add("active");
      btnRaw.classList.remove("active");
      btnHeatmap.classList.remove("active");
      startWebcam();
    });
  }

  btnRaw.addEventListener("click", () => {
    stopWebcam();
    state.activeImageView = "raw";
    btnRaw.classList.add("active");
    if (btnWebcam) btnWebcam.classList.remove("active");
    btnHeatmap.classList.remove("active");
    if (state.currentRecord) updateInspectionImage(state.currentRecord);
  });

  btnHeatmap.addEventListener("click", () => {
    stopWebcam();
    state.activeImageView = "heatmap";
    btnHeatmap.classList.add("active");
    if (btnWebcam) btnWebcam.classList.remove("active");
    btnRaw.classList.remove("active");
    if (state.currentRecord) updateInspectionImage(state.currentRecord);
  });

  // Webcam Snap Button
  const btnWebcamSnap = document.getElementById("btn-webcam-snap");
  if (btnWebcamSnap) {
    btnWebcamSnap.addEventListener("click", captureWebcamAndAnalyze);
  }

  // History Filter Changes
  document.getElementById("filter-produce").addEventListener("change", loadHistory);
  document.getElementById("filter-status").addEventListener("change", loadHistory);
  document.getElementById("btn-export-csv").addEventListener("click", exportHistoryCSV);

  // Settings Sliders Live Feedback
  document.getElementById("slider-safe-risk").addEventListener("input", (e) => {
    document.getElementById("val-safe-risk").textContent = `${e.target.value}%`;
  });
  document.getElementById("slider-mod-risk").addEventListener("input", (e) => {
    document.getElementById("val-mod-risk").textContent = `${e.target.value}%`;
  });
  document.getElementById("slider-sensor-weight").addEventListener("input", (e) => {
    document.getElementById("val-sensor-weight").textContent = `${Math.round(e.target.value * 100)}%`;
  });

  document.getElementById("btn-save-settings").addEventListener("click", saveSettings);
  document.getElementById("btn-calibrate").addEventListener("click", calibrateSensors);
  document.getElementById("btn-test-telegram").addEventListener("click", testTelegramAlert);

  // Trigger Scan
  document.getElementById("btn-run-scan").addEventListener("click", triggerSimulatedScan);

  // Modal Image Zoom
  document.getElementById("preview-img").addEventListener("click", () => {
    const src = document.getElementById("preview-img").src;
    openModal(src);
  });

  document.getElementById("modal-overlay").addEventListener("click", (e) => {
    if (e.target.id === "modal-overlay" || e.target.classList.contains("modal-close-btn")) {
      closeModal();
    }
  });

  // Notification Permission on user interaction
  document.body.addEventListener("click", requestNotificationPermission, { once: true });
}

function openModal(imgSrc) {
  const modal = document.getElementById("modal-overlay");
  const modalImg = document.getElementById("modal-img");
  modalImg.src = imgSrc;
  modal.classList.add("active");
}

function closeModal() {
  document.getElementById("modal-overlay").classList.remove("active");
}

function showToast(message, type = "info") {
  const container = document.getElementById("toast-container");
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `<span>${type === "danger" ? "⚠️" : (type === "success" ? "✅" : "ℹ️")}</span> <span>${message}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateX(100%)";
    toast.style.transition = "all 0.3s ease";
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// -----------------------------------------------------------------------------
// 9. PWA Service Worker Registration
// -----------------------------------------------------------------------------
function initPWA() {
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/sw.js")
        .then(reg => console.log("[PWA] Service Worker registered:", reg.scope))
        .catch(err => console.log("[PWA] Registration failed:", err));
    });
  }
}
