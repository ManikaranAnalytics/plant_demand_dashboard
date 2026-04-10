"""
mock_api.py  —  Demo Live Generation API
Run alongside the Streamlit app:
    python mock_api.py
Serves on http://localhost:8765
"""
from __future__ import annotations

import math
import random
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

# ─────────────────────────────────────────────────────────────────────────────
# Generation profiles  (base MW + daily shape)
# Shape: maps hour → typical MW.  Interpolated for any time of day.
# ─────────────────────────────────────────────────────────────────────────────

# 80MW plant: night baseload ~60, dips mid-day, peaks evening
GEN_80MW_SHAPE = {
    0: 60.5,  2: 60.7,  4: 62.2,  6: 62.0,
    7: 59.0,  8: 54.0,  9: 50.0, 10: 44.0,
   11: 45.0, 12: 44.0, 13: 42.0, 14: 41.0,
   15: 40.0, 16: 41.0, 17: 54.0, 18: 63.0,
   19: 67.0, 20: 66.0, 21: 65.0, 22: 65.0,
   23: 63.0, 24: 62.0,
}

# 43MW plant: similar profile, lower capacity
GEN_43MW_SHAPE = {
    0: 38.0,  2: 38.5,  4: 39.0,  6: 39.0,
    7: 37.5,  8: 35.0,  9: 32.0, 10: 28.0,
   11: 28.5, 12: 28.0, 13: 27.0, 14: 26.5,
   15: 26.0, 16: 26.5, 17: 34.0, 18: 40.0,
   19: 42.5, 20: 42.0, 21: 41.5, 22: 41.5,
   23: 40.0, 24: 39.0,
}

# Solar: zero at night, bell curve through the day
GEN_SOLAR_SHAPE = {
    0: 0.0,  4: 0.0,  5: 0.2,  6: 1.5,
    7: 4.0,  8: 8.0,  9:13.0, 10:17.0,
   11:20.0, 12:22.0, 13:21.5, 14:20.0,
   15:17.0, 16:13.0, 17: 8.0, 18: 3.0,
   19: 0.5, 20: 0.0, 24: 0.0,
}

def _interpolate(shape: dict, hour_float: float) -> float:
    hours = sorted(shape.keys())
    for i in range(len(hours) - 1):
        h0, h1 = hours[i], hours[i+1]
        if h0 <= hour_float <= h1:
            t = (hour_float - h0) / (h1 - h0)
            return shape[h0] + t * (shape[h1] - shape[h0])
    return shape[hours[-1]]

def _noise(scale: float = 0.3) -> float:
    return random.gauss(0, scale)

def live_generation() -> dict:
    now = datetime.now()
    h = now.hour + now.minute / 60.0 + now.second / 3600.0

    gen_80  = round(_interpolate(GEN_80MW_SHAPE,  h) + _noise(0.25), 2)
    gen_43  = round(_interpolate(GEN_43MW_SHAPE,  h) + _noise(0.20), 2)
    gen_sol = round(max(0.0, _interpolate(GEN_SOLAR_SHAPE, h) + _noise(0.15)), 2)

    # Auxiliary: typically ~4% of generation
    aux_80  = round(gen_80  * 0.039 + _noise(0.05), 2)
    aux_43  = round(gen_43  * 0.041 + _noise(0.04), 2)
    aux_sol = round(gen_sol * 0.030 + _noise(0.02), 2)

    total_gen = round(gen_80 + gen_43 + gen_sol, 2)
    total_aux = round(aux_80 + aux_43 + aux_sol, 2)

    # current 15-min block label
    block_idx = now.hour * 4 + now.minute // 15
    h_s = block_idx * 15 // 60;  m_s = (block_idx * 15) % 60
    h_e = (block_idx * 15 + 15) // 60; m_e = ((block_idx * 15 + 15)) % 60
    time_block = f"{h_s}:{m_s:02d} - {h_e}:{m_e:02d}"

    return {
        "timestamp":    now.isoformat(),
        "time_block":   time_block,
        "block_index":  block_idx + 1,
        "plants": {
            "80MW": {
                "generation": gen_80,
                "auxiliary":  max(0.0, aux_80),
            },
            "43MW": {
                "generation": gen_43,
                "auxiliary":  max(0.0, aux_43),
            },
            "Solar": {
                "generation": gen_sol,
                "auxiliary":  max(0.0, aux_sol),
            },
        },
        "totals": {
            "total_generation": total_gen,
            "total_auxiliary":  max(0.0, total_aux),
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# HTTP server
# ─────────────────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence request logs

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/live":
            self._send_json(live_generation())
        elif self.path == "/health":
            self._send_json({"status": "ok"})
        else:
            self._send_json({"error": "not found"}, 404)


if __name__ == "__main__":
    port = 8765
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"✅ Mock Generation API running on http://localhost:{port}")
    print(f"   GET /live   → current generation reading")
    print(f"   GET /health → status check")
    server.serve_forever()
