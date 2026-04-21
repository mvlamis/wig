import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from muse_state import RuntimeState, snapshot_participant


class ScoreHTTPHandler(BaseHTTPRequestHandler):
    runtime_state: RuntimeState | None = None
    default_participant: str = "left"

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/score":
            query = parse_qs(parsed.query)
            participant_id = query.get("participant", [self.default_participant])[0]
            self._write_json(self._single_score_response(participant_id))
            return

        if parsed.path == "/api/scores":
            self._write_json(self._dual_scores_response())
            return

        if parsed.path == "/api/weights":
            self._write_json(self._weights_response())
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/weights":
            self._handle_update_weights()
            return

        self.send_response(404)
        self.end_headers()

    def _single_score_response(self, participant_id: str) -> dict:
        if not self.runtime_state:
            return {
                "participant": participant_id,
                "score": 50.0,
                "label": "calibrating",
                "status": "offline",
            }

        with self.runtime_state.lock:
            participant = self.runtime_state.participants.get(participant_id)
            if participant is None:
                return {
                    "participant": participant_id,
                    "score": 50.0,
                    "label": "calibrating",
                    "status": "missing-participant",
                }

            payload = snapshot_participant(participant)

        payload["participant"] = participant_id
        return payload

    def _dual_scores_response(self) -> dict:
        if not self.runtime_state:
            return {
                "participants": {},
                "status": "offline",
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }

        with self.runtime_state.lock:
            payload = {
                pid: snapshot_participant(participant)
                for pid, participant in self.runtime_state.participants.items()
            }
            timestamp = self.runtime_state.last_update_iso

        return {
            "participants": payload,
            "status": "ok",
            "timestamp": timestamp,
        }

    def _weights_response(self) -> dict:
        if not self.runtime_state:
            return {
                "status": "offline",
                "muse_weight": 0.5,
                "gsr_weight": 0.5,
            }

        with self.runtime_state.lock:
            muse_weight = self.runtime_state.muse_weight
            gsr_weight = self.runtime_state.gsr_weight

        return {
            "status": "ok",
            "muse_weight": muse_weight,
            "gsr_weight": gsr_weight,
        }

    def _handle_update_weights(self) -> None:
        if not self.runtime_state:
            self._write_json(
                {
                    "status": "offline",
                    "error": "runtime state unavailable",
                },
                status_code=503,
            )
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0

        if content_length <= 0:
            self._write_json(
                {
                    "status": "error",
                    "error": "request body is required",
                },
                status_code=400,
            )
            return

        try:
            body = self.rfile.read(content_length)
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._write_json(
                {
                    "status": "error",
                    "error": "invalid JSON body",
                },
                status_code=400,
            )
            return

        if not isinstance(payload, dict):
            self._write_json(
                {
                    "status": "error",
                    "error": "JSON object expected",
                },
                status_code=400,
            )
            return

        if "muse_weight" not in payload and "gsr_weight" not in payload:
            self._write_json(
                {
                    "status": "error",
                    "error": "include muse_weight and/or gsr_weight",
                },
                status_code=400,
            )
            return

        with self.runtime_state.lock:
            current_muse = self.runtime_state.muse_weight
            current_gsr = self.runtime_state.gsr_weight

            muse_weight = current_muse if "muse_weight" not in payload else payload["muse_weight"]
            gsr_weight = current_gsr if "gsr_weight" not in payload else payload["gsr_weight"]

            try:
                muse_weight = float(muse_weight)
                gsr_weight = float(gsr_weight)
            except (TypeError, ValueError):
                self._write_json(
                    {
                        "status": "error",
                        "error": "weights must be numbers",
                    },
                    status_code=400,
                )
                return

            if muse_weight < 0 or gsr_weight < 0:
                self._write_json(
                    {
                        "status": "error",
                        "error": "weights must be >= 0",
                    },
                    status_code=400,
                )
                return

            total_weight = muse_weight + gsr_weight
            if total_weight <= 0:
                self._write_json(
                    {
                        "status": "error",
                        "error": "at least one weight must be > 0",
                    },
                    status_code=400,
                )
                return

            self.runtime_state.muse_weight = muse_weight / total_weight
            self.runtime_state.gsr_weight = gsr_weight / total_weight
            normalized_muse = self.runtime_state.muse_weight
            normalized_gsr = self.runtime_state.gsr_weight

        self._write_json(
            {
                "status": "ok",
                "muse_weight": normalized_muse,
                "gsr_weight": normalized_gsr,
            }
        )

    def _write_json(self, payload: dict, status_code: int = 200) -> None:
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def log_message(self, format, *args):
        _ = format, args
        pass


def start_score_server(state: RuntimeState, host: str, port: int, default_participant: str) -> threading.Thread:
    ScoreHTTPHandler.runtime_state = state
    ScoreHTTPHandler.default_participant = default_participant
    server = HTTPServer((host, port), ScoreHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"Score server listening on http://{host}:{port}/api/scores")
    return thread
