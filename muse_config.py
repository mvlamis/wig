from dataclasses import dataclass

from brainflow.board_shim import BoardIds


@dataclass(frozen=True)
class StreamConfig:
    board_id: int = BoardIds.MUSE_2_BOARD.value
    # optional fallback values used when participant-specific values are not set
    mac_address: str = ""
    serial_port: str = ""
    # participant-specific connections for running two Muse headbands at once
    left_mac_address: str = "1E08D27A-53A5-606A-CEC2-B336931D43C6"
    right_mac_address: str = "64ACDCE7-A568-8A91-380B-A9406DE91183"
    left_serial_port: str = ""
    right_serial_port: str = ""
    timeout_sec: float = 0.0

    participants: tuple[str, str] = ("left", "right")
    primary_muse_participant: str = "left"

    api_host: str = "localhost"
    api_port: int = 8000
    api_default_participant: str = "left"

    baseline_sec: float = 20.0
    window_sec: float = 4.0
    step_sec: float = 1.0

    score_ema_alpha: float = 0.2
    gsr_ema_alpha: float = 0.35
    muse_weight: float = 0.5
    gsr_weight: float = 0.5
    max_abs_z: float = 3.0
    min_std: float = 1e-3
    epsilon: float = 1e-8

    gsr_udp_host: str = "127.0.0.1"
    gsr_udp_port: int = 8765

    enable_dev_logger: bool = False
    enable_signal_quality_gate: bool = False
    noise_std_uv_threshold: float = 120.0


CONFIG = StreamConfig()
