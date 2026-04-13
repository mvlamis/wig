import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from muse_state import RuntimeState, snapshot_participant


class ScoreHTTPHandler(BaseHTTPRequestHandler):
    runtime_state: RuntimeState | None = None
    default_participant: str = "left"

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

    def _write_json(self, payload: dict) -> None:
        self.send_response(200)
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
