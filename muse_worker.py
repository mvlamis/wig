import math
import json
import socket
import time
from datetime import datetime

import numpy as np
from brainflow.board_shim import BoardShim, BrainFlowInputParams
from brainflow.data_filter import DataFilter

from muse_config import StreamConfig
from muse_state import ParticipantState, RuntimeState


class UdpGSRReceiver:
    def __init__(self, host: str, port: int):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.setblocking(False)
        print(f"GSR UDP receiver listening on udp://{host}:{port}")

    def poll(self) -> dict[str, float]:
        latest: dict[str, float] = {}
        while True:
            try:
                payload_bytes, _ = self.sock.recvfrom(4096)
            except BlockingIOError:
                break

            try:
                payload = json.loads(payload_bytes.decode("utf-8"))
                participant_id = str(payload.get("participant", "")).strip()
                value = float(payload.get("value"))
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                continue

            if participant_id:
                latest[participant_id] = value

        return latest


def label_from_score(score: float) -> str:
    if score < 35:
        return "calm"
    if score < 55:
        return "mild"
    if score < 75:
        return "moderate"
    return "high"


def clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def logistic(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def zscore(value: float, mean: float, std: float, min_std: float) -> float:
    safe_std = std if std > min_std else min_std
    return (value - mean) / safe_std


def build_input_params_for_participant(config: StreamConfig, participant_id: str) -> BrainFlowInputParams:
    params = BrainFlowInputParams()

    if participant_id == "left":
        params.mac_address = config.left_mac_address or config.mac_address
        params.serial_port = config.left_serial_port or config.serial_port
    elif participant_id == "right":
        params.mac_address = config.right_mac_address or config.mac_address
        params.serial_port = config.right_serial_port or config.serial_port
    else:
        params.mac_address = config.mac_address
        params.serial_port = config.serial_port

    return params


def signal_quality_ok(data: np.ndarray, eeg_channels: list[int], std_threshold_uv: float) -> tuple[bool, float]:
    channel_stds = [float(np.std(data[channel])) for channel in eeg_channels]
    max_std = max(channel_stds) if channel_stds else 0.0
    return max_std <= std_threshold_uv, max_std


def extract_beta_alpha_ratio(
    data: np.ndarray,
    eeg_channels: list[int],
    sampling_rate: int,
    epsilon: float,
) -> float:
    avg_bands, _ = DataFilter.get_avg_band_powers(data, eeg_channels, sampling_rate, True)
    alpha = float(avg_bands[2])
    beta = float(avg_bands[3])
    return beta / (alpha + epsilon)


def update_muse_state(
    participant: ParticipantState,
    ratio: float,
    elapsed_sec: float,
    config: StreamConfig,
) -> None:
    participant.beta_alpha_ratio = ratio

    if not participant.baseline_ready:
        if elapsed_sec < config.baseline_sec:
            participant.baseline_samples.append(ratio)
            participant.status = "calibrating"
            participant.label = "calibrating"
            return

        if not participant.baseline_samples:
            participant.baseline_samples.append(ratio)

        values = np.array(participant.baseline_samples, dtype=float)
        participant.baseline_mean = float(np.mean(values))
        participant.baseline_std = float(max(float(np.std(values)), config.min_std))
        print(
            f"[{participant.participant_id}] baseline ready: "
            f"mean={participant.baseline_mean:.4f}, std={participant.baseline_std:.4f}"
        )

    z = clip(
        zscore(ratio, participant.baseline_mean, participant.baseline_std, config.min_std),
        -config.max_abs_z,
        config.max_abs_z,
    )
    raw_score = 100.0 * logistic(z)
    participant.muse_score = (
        (1.0 - config.score_ema_alpha) * participant.muse_score
    ) + (config.score_ema_alpha * raw_score)
    participant.bang_down_percent = participant.muse_score
    participant.label = label_from_score(participant.muse_score)
    participant.status = "live"


def update_gsr_state(
    participant: ParticipantState,
    gsr_raw: float,
    elapsed_sec: float,
    config: StreamConfig,
) -> None:
    if participant.gsr_value is None:
        participant.gsr_value = gsr_raw
    else:
        one_minus = 1.0 - config.gsr_ema_alpha
        participant.gsr_value = (one_minus * participant.gsr_value) + (config.gsr_ema_alpha * gsr_raw)

    if not participant.gsr_baseline_ready:
        if elapsed_sec < config.baseline_sec:
            participant.gsr_baseline_samples.append(participant.gsr_value)
            return

        if not participant.gsr_baseline_samples:
            participant.gsr_baseline_samples.append(participant.gsr_value)

        values = np.array(participant.gsr_baseline_samples, dtype=float)
        participant.gsr_baseline_mean = float(np.mean(values))
        participant.gsr_baseline_std = float(max(float(np.std(values)), config.min_std))
        print(
            f"[{participant.participant_id}] GSR baseline ready: "
            f"mean={participant.gsr_baseline_mean:.1f}, std={participant.gsr_baseline_std:.1f}"
        )

    participant.gsr_z = clip(
        zscore(
            participant.gsr_value,
            participant.gsr_baseline_mean,
            participant.gsr_baseline_std,
            config.min_std,
        ),
        -config.max_abs_z,
        config.max_abs_z,
    )


def run_stream_loop(
    boards: dict[str, BoardShim],
    state: RuntimeState,
    should_stop: dict,
    config: StreamConfig,
) -> None:
    gsr_receiver = None
    try:
        gsr_receiver = UdpGSRReceiver(config.gsr_udp_host, config.gsr_udp_port)
    except OSError as exc:
        print(f"GSR UDP receiver unavailable ({exc}); continuing without GSR updates.")

    metadata: dict[str, dict] = {}
    for participant_id, board in boards.items():
        sampling_rate = BoardShim.get_sampling_rate(config.board_id)
        eeg_channels = BoardShim.get_eeg_channels(config.board_id)
        window_points = int(config.window_sec * sampling_rate)
        if window_points <= 0:
            raise ValueError("window_sec must produce at least 1 sample")
        metadata[participant_id] = {
            "board": board,
            "sampling_rate": sampling_rate,
            "eeg_channels": eeg_channels,
            "window_points": window_points,
        }

    print(
        f"Streaming started. Baseline {config.baseline_sec:.1f}s, "
        f"window {config.window_sec:.1f}s, step {config.step_sec:.1f}s."
    )

    start = time.time()

    while not should_stop["flag"]:
        elapsed = time.time() - start
        if config.timeout_sec > 0 and elapsed >= config.timeout_sec:
            print("Timeout reached, stopping stream")
            break

        gsr_updates = gsr_receiver.poll() if gsr_receiver is not None else {}

        updates: list[tuple[str, float]] = []
        offline_ids: list[str] = []
        for participant_id in config.participants:
            if participant_id not in metadata:
                offline_ids.append(participant_id)
                continue

            info = metadata[participant_id]
            board = info["board"]
            sampling_rate = info["sampling_rate"]
            eeg_channels = info["eeg_channels"]
            window_points = info["window_points"]

            data = board.get_current_board_data(window_points)
            if data.shape[1] < window_points:
                continue

            if config.enable_signal_quality_gate:
                quality_ok, max_std_uv = signal_quality_ok(
                    data,
                    eeg_channels,
                    config.noise_std_uv_threshold,
                )
                if not quality_ok:
                    print(f"[{participant_id}] noisy window (max std={max_std_uv:5.1f}uV), holding previous score")
                    continue

            ratio = extract_beta_alpha_ratio(data, eeg_channels, sampling_rate, config.epsilon)
            updates.append((participant_id, ratio))

        with state.lock:
            for participant_id in offline_ids:
                participant = state.participants[participant_id]
                participant.status = "offline"
                participant.label = "calibrating"

            for participant_id, gsr_raw in gsr_updates.items():
                participant = state.participants.get(participant_id)
                if participant is not None:
                    update_gsr_state(participant, gsr_raw, elapsed, config)

            for participant_id, ratio in updates:
                participant = state.participants[participant_id]
                update_muse_state(participant, ratio, elapsed, config)

            state.last_update_iso = datetime.now().isoformat(timespec="seconds")

            timestamp = datetime.now().strftime("%H:%M:%S")
            for participant_id, ratio in updates:
                participant = state.participants[participant_id]
                print(
                    f"[{timestamp}] [{participant_id}] score={participant.muse_score:5.1f}/100 "
                    f"({participant.label}, {participant.status}) beta/alpha={ratio:.4f} "
                    f"gsr={participant.gsr_value if participant.gsr_value is not None else 0:.1f} "
                    f"gsr_z={participant.gsr_z:+.3f}"
                )

        time.sleep(config.step_sec)
