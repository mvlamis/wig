import threading
from dataclasses import dataclass, field
from datetime import datetime

from muse_config import StreamConfig


@dataclass
class ParticipantState:
    participant_id: str
    baseline_samples: list[float] = field(default_factory=list)
    baseline_mean: float | None = None
    baseline_std: float | None = None
    beta_alpha_ratio: float | None = None
    gsr_baseline_samples: list[float] = field(default_factory=list)
    gsr_baseline_mean: float | None = None
    gsr_baseline_std: float | None = None
    gsr_value: float | None = None
    gsr_z: float = 0.0

    muse_score: float = 50.0
    fused_score: float = 50.0
    label: str = "calibrating"
    status: str = "waiting-for-muse"
    bang_down_percent: float = 50.0

    @property
    def baseline_ready(self) -> bool:
        return self.baseline_mean is not None and self.baseline_std is not None

    @property
    def gsr_baseline_ready(self) -> bool:
        return self.gsr_baseline_mean is not None and self.gsr_baseline_std is not None


@dataclass
class RuntimeState:
    participants: dict[str, ParticipantState] = field(default_factory=dict)
    muse_weight: float = 0.5
    gsr_weight: float = 0.5
    last_update_iso: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)


def create_runtime_state(config: StreamConfig) -> RuntimeState:
    total_weight = config.muse_weight + config.gsr_weight
    if total_weight <= 0:
        muse_weight = 0.5
        gsr_weight = 0.5
    else:
        muse_weight = config.muse_weight / total_weight
        gsr_weight = config.gsr_weight / total_weight

    participants = {pid: ParticipantState(participant_id=pid) for pid in config.participants}
    return RuntimeState(
        participants=participants,
        muse_weight=muse_weight,
        gsr_weight=gsr_weight,
        last_update_iso=datetime.now().isoformat(timespec="seconds"),
    )


def snapshot_participant(participant: ParticipantState) -> dict:
    return {
        "score": participant.fused_score,
        "label": participant.label,
        "status": participant.status,
        "muse_score": participant.muse_score,
        "fused_score": participant.fused_score,
        "gsr_value": participant.gsr_value,
        "gsr_z": participant.gsr_z,
        "bang_down_percent": participant.bang_down_percent,
        "calibration": {
            "muse_ready": participant.baseline_ready,
            "gsr_ready": participant.gsr_baseline_ready,
        },
    }
