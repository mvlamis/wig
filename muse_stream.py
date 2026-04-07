import json
import math
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
from brainflow.board_shim import BoardIds, BoardShim, BrainFlowInputParams
from brainflow.data_filter import DataFilter
from live_plot import PlotContext, finalize_live_plot, setup_live_plot, update_live_plot


@dataclass(frozen=True)
class StreamConfig:
	board_id: int = BoardIds.MUSE_2_BOARD.value
	mac_address: str = ""
	serial_port: str = ""
	timeout_sec: float = 0.0
	baseline_sec: float = 20.0
	window_sec: float = 4.0
	step_sec: float = 1.0
	enable_dev_logger: bool = False
	enable_live_plot: bool = True
	plot_history_sec: float = 180.0
	noise_std_uv_threshold: float = 120.0
	max_abs_z: float = 3.0
	feature_ema_alpha: float = 0.25
	score_ema_alpha: float = 0.2
	min_std: float = 1e-3
	epsilon: float = 1e-8


CONFIG = StreamConfig()


class ScoreHTTPHandler(BaseHTTPRequestHandler):
	"""HTTP handler to expose the current discomfort score."""
	# Class variable to hold reference to runtime state
	runtime_state = None

	def do_GET(self):
		if self.path == "/api/score":
			if self.runtime_state and self.runtime_state.smoothed_score is not None:
				response = {
					"score": self.runtime_state.smoothed_score,
					"label": self.runtime_state.last_label,
				}
			else:
				response = {
					"score": 50.0,
					"label": "calibrating",
				}
			
			self.send_response(200)
			self.send_header("Content-Type", "application/json")
			self.send_header("Access-Control-Allow-Origin", "*")
			self.end_headers()
			self.wfile.write(json.dumps(response).encode())
		else:
			self.send_response(404)
			self.end_headers()

	def log_message(self, format, *args):
		# Suppress HTTP server logging
		pass


def start_score_server(state: RuntimeState, port: int = 8000) -> threading.Thread:
	"""Start HTTP server in a background thread."""
	ScoreHTTPHandler.runtime_state = state
	server = HTTPServer(("localhost", port), ScoreHTTPHandler)
	thread = threading.Thread(target=server.serve_forever, daemon=True)
	thread.start()
	print(f"Score server listening on http://localhost:{port}/api/score")
	return thread



@dataclass
class FeatureVector:
	beta_alpha_ratio: float
	alpha_mean: float
	gamma_mean: float
	theta_beta_ratio: float
	frontal_alpha_asymmetry: float


@dataclass
class BaselineStats:
	means: dict[str, float]
	stds: dict[str, float]


@dataclass
class RuntimeState:
	baseline_samples: list[FeatureVector] = field(default_factory=list)
	baseline_stats: BaselineStats | None = None
	smoothed_features: FeatureVector | None = None
	smoothed_score: float | None = None
	last_label: str = "calm"

# normalization
def zscore(value: float, mean: float, std: float, min_std: float) -> float:
	safe_std = std if std > min_std else min_std
	return (value - mean) / safe_std

# clip to a range
def clip(value: float, low: float, high: float) -> float:
	return max(low, min(high, value))

# map to 0-1 range
def logistic(value: float) -> float:
	return 1.0 / (1.0 + math.exp(-value))

# map score to label for printing
def label_from_score(score: float) -> str:
	if score < 35:
		return "calm"
	if score < 55:
		return "mild"
	if score < 75:
		return "moderate"
	return "high"


def build_input_params(config: StreamConfig) -> BrainFlowInputParams:
	params = BrainFlowInputParams()
	params.mac_address = config.mac_address
	params.serial_port = config.serial_port
	return params

def pick_frontal_channels(eeg_channels: list[int]) -> tuple[int, int]:
	if len(eeg_channels) < 2:
		raise RuntimeError("Need at least two EEG channels to compute frontal asymmetry.")
	return eeg_channels[0], eeg_channels[1]

def extract_features(
	data: np.ndarray,
	eeg_channels: list[int],
	sampling_rate: int,
	left_channel: int,
	right_channel: int,
	config: StreamConfig,
) -> FeatureVector:
	# band order: delta, theta, alpha, beta, gamma
	avg_bands, _ = DataFilter.get_avg_band_powers(data, eeg_channels, sampling_rate, True)
	left_bands, _ = DataFilter.get_avg_band_powers(data, [left_channel], sampling_rate, True)
	right_bands, _ = DataFilter.get_avg_band_powers(data, [right_channel], sampling_rate, True)

	theta = float(avg_bands[1])
	alpha = float(avg_bands[2])
	beta = float(avg_bands[3])
	gamma = float(avg_bands[4])

	left_alpha = float(left_bands[2])
	right_alpha = float(right_bands[2])

	return FeatureVector(
		# higher beta/alpha and lower theta/beta track arousal/effort states
		beta_alpha_ratio=beta / (alpha + config.epsilon),
		alpha_mean=alpha,
		gamma_mean=gamma,
		theta_beta_ratio=theta / (beta + config.epsilon),
		# FAA is log-right minus log-left alpha
		# sign captures hemispheric asymmetry
		frontal_alpha_asymmetry=math.log(right_alpha + config.epsilon)
		- math.log(left_alpha + config.epsilon),
	)

def blend_features(prev: FeatureVector | None, curr: FeatureVector, alpha: float) -> FeatureVector:
	if prev is None:
		return curr

	# EMA stabilizes per-window variability
	one_minus = 1.0 - alpha
	return FeatureVector(
		beta_alpha_ratio=(one_minus * prev.beta_alpha_ratio) + (alpha * curr.beta_alpha_ratio),
		alpha_mean=(one_minus * prev.alpha_mean) + (alpha * curr.alpha_mean),
		gamma_mean=(one_minus * prev.gamma_mean) + (alpha * curr.gamma_mean),
		theta_beta_ratio=(one_minus * prev.theta_beta_ratio) + (alpha * curr.theta_beta_ratio),
		frontal_alpha_asymmetry=(one_minus * prev.frontal_alpha_asymmetry)
		+ (alpha * curr.frontal_alpha_asymmetry),
	)


def signal_quality_ok(data: np.ndarray, eeg_channels: list[int], std_threshold_uv: float) -> tuple[bool, float]:
	# reject windows with unusually large channel variance
	channel_stds = [float(np.std(data[channel])) for channel in eeg_channels]
	max_std = max(channel_stds) if channel_stds else 0.0
	return max_std <= std_threshold_uv, max_std


def baseline_from_samples(samples: list[FeatureVector], min_std: float) -> BaselineStats:
	if not samples:
		raise RuntimeError("No baseline samples were collected.")

	feature_names = (
		"beta_alpha_ratio",
		"alpha_mean",
		"gamma_mean",
		"theta_beta_ratio",
		"frontal_alpha_asymmetry",
	)

	means: dict[str, float] = {}
	stds: dict[str, float] = {}
	for name in feature_names:
		values = np.array([getattr(sample, name) for sample in samples], dtype=float)
		means[name] = float(np.mean(values))
		stds[name] = float(max(float(np.std(values)), min_std))

	return BaselineStats(means=means, stds=stds)


def estimate_discomfort_score(
	features: FeatureVector,
	baseline: BaselineStats,
	config: StreamConfig,
) -> tuple[float, str]:
	# convert each feature to baseline-relative z-scores and clamp outliers
	z_beta_alpha = clip(
		zscore(
			features.beta_alpha_ratio,
			baseline.means["beta_alpha_ratio"],
			baseline.stds["beta_alpha_ratio"],
			config.min_std,
		),
		-config.max_abs_z,
		config.max_abs_z,
	)
	z_alpha_drop = clip(
		-zscore(
			features.alpha_mean,
			baseline.means["alpha_mean"],
			baseline.stds["alpha_mean"],
			config.min_std,
		),
		-config.max_abs_z,
		config.max_abs_z,
	)
	z_gamma = clip(
		zscore(
			features.gamma_mean,
			baseline.means["gamma_mean"],
			baseline.stds["gamma_mean"],
			config.min_std,
		),
		-config.max_abs_z,
		config.max_abs_z,
	)
	z_theta_beta_inverse = clip(
		-zscore(
			features.theta_beta_ratio,
			baseline.means["theta_beta_ratio"],
			baseline.stds["theta_beta_ratio"],
			config.min_std,
		),
		-config.max_abs_z,
		config.max_abs_z,
	)
	z_faa = clip(
		zscore(
			features.frontal_alpha_asymmetry,
			baseline.means["frontal_alpha_asymmetry"],
			baseline.stds["frontal_alpha_asymmetry"],
			config.min_std,
		),
		-config.max_abs_z,
		config.max_abs_z,
	)

	# weighted fusion gives one latent discomfort score before logistic squashing
	raw_score = (
		0.35 * z_beta_alpha
		+ 0.25 * z_alpha_drop
		+ 0.20 * z_faa
		+ 0.15 * z_gamma
		+ 0.05 * z_theta_beta_inverse
	)
	score = 100.0 * logistic(raw_score)
	return score, label_from_score(score)


def update_plot_if_enabled(
	plot_ctx: PlotContext | None,
	state: RuntimeState,
	elapsed: float,
	config: StreamConfig,
) -> None:
	if not config.enable_live_plot or plot_ctx is None:
		return
	if state.smoothed_score is None or state.smoothed_features is None:
		return

	update_live_plot(
		plot_ctx,
		elapsed,
		state.smoothed_score,
		state.smoothed_features.beta_alpha_ratio,
		state.smoothed_features.frontal_alpha_asymmetry,
		config.plot_history_sec,
		config.epsilon,
	)


def process_noisy_window(
	elapsed: float,
	max_std_uv: float,
	state: RuntimeState,
	plot_ctx: PlotContext | None,
	config: StreamConfig,
) -> None:
	# keep the previous estimate during artifact windows instead of recomputing
	timestamp = datetime.now().strftime("%H:%M:%S")
	if state.smoothed_score is None:
		print(f"[{timestamp}] noisy window (max std={max_std_uv:5.1f}uV), waiting for cleaner data")
	else:
		print(
			f"[{timestamp}] noisy window (max std={max_std_uv:5.1f}uV), "
			f"holding score={state.smoothed_score:5.1f}/100 ({state.last_label})"
		)
		update_plot_if_enabled(plot_ctx, state, elapsed, config)


def process_clean_window(
	elapsed: float,
	data: np.ndarray,
	eeg_channels: list[int],
	sampling_rate: int,
	left_ch: int,
	right_ch: int,
	state: RuntimeState,
	plot_ctx: PlotContext | None,
	config: StreamConfig,
) -> None:
	raw_features = extract_features(data, eeg_channels, sampling_rate, left_ch, right_ch, config)
	state.smoothed_features = blend_features(state.smoothed_features, raw_features, config.feature_ema_alpha)

	if state.baseline_stats is None:
		# during calibration only collect stable samples
		if elapsed < config.baseline_sec:
			state.baseline_samples.append(state.smoothed_features)
			print(
				f"Calibrating baseline: {elapsed:5.1f}/{config.baseline_sec:.1f}s",
				end="\r",
				flush=True,
			)
			return

		state.baseline_stats = baseline_from_samples(state.baseline_samples, config.min_std)
		print("\nBaseline complete. Starting live estimation.")
		return

	raw_score, label = estimate_discomfort_score(state.smoothed_features, state.baseline_stats, config)
	if state.smoothed_score is None:
		state.smoothed_score = raw_score
	else:
		# EMA on final score reduces jitter in printed/visual output
		state.smoothed_score = (1.0 - config.score_ema_alpha) * state.smoothed_score + (
			config.score_ema_alpha * raw_score
		)
	state.last_label = label

	timestamp = datetime.now().strftime("%H:%M:%S")
	print(
		f"[{timestamp}] discomfort/anxiety={state.smoothed_score:5.1f}/100 ({label}) | "
		f"beta/alpha={state.smoothed_features.beta_alpha_ratio:.3f}, "
		f"FAA={state.smoothed_features.frontal_alpha_asymmetry:.3f}"
	)
	update_plot_if_enabled(plot_ctx, state, elapsed, config)


def run_stream_loop(
	board: BoardShim,
	sampling_rate: int,
	eeg_channels: list[int],
	left_ch: int,
	right_ch: int,
	state: RuntimeState,
	plot_ctx: PlotContext | None,
	should_stop: dict,
	config: StreamConfig,
) -> None:
	# fetch, gate noisy data, process
	window_points = int(config.window_sec * sampling_rate)
	if window_points <= 0:
		raise ValueError("window_sec must produce at least 1 sample.")

	print(
		"Streaming started. Collecting baseline for "
		f"{config.baseline_sec:.1f}s using a {config.window_sec:.1f}s window..."
	)

	start = time.time()
	while not should_stop["flag"]:
		elapsed = time.time() - start
		if config.timeout_sec > 0 and elapsed >= config.timeout_sec:
			print("Timeout reached. Stopping stream.")
			break

		data = board.get_current_board_data(window_points)
		if data.shape[1] < window_points:
			# wait until a full analysis window is available
			time.sleep(config.step_sec)
			continue

		quality_ok, max_std_uv = signal_quality_ok(data, eeg_channels, config.noise_std_uv_threshold)
		# if not quality_ok:
		# 	process_noisy_window(elapsed, max_std_uv, state, plot_ctx, config)
		# 	time.sleep(config.step_sec)
		# 	continue

		process_clean_window(
			elapsed,
			data,
			eeg_channels,
			sampling_rate,
			left_ch,
			right_ch,
			state,
			plot_ctx,
			config,
		)
		time.sleep(config.step_sec)


def main() -> None:
	config = CONFIG
	if config.enable_dev_logger:
		BoardShim.enable_dev_board_logger()

	board = BoardShim(config.board_id, build_input_params(config))
	plot_ctx = setup_live_plot() if config.enable_live_plot else None
	state = RuntimeState()

	# Start HTTP server to expose score
	start_score_server(state, port=8000)

	should_stop = {"flag": False}

	def _stop_handler(signum, frame):
		_ = signum, frame
		should_stop["flag"] = True

	signal.signal(signal.SIGINT, _stop_handler)

	print("Preparing Muse session...")
	try:
		board.prepare_session()
		board.start_stream()

		sampling_rate = BoardShim.get_sampling_rate(config.board_id)
		eeg_channels = BoardShim.get_eeg_channels(config.board_id)
		left_ch, right_ch = pick_frontal_channels(eeg_channels)

		run_stream_loop(
			board,
			sampling_rate,
			eeg_channels,
			left_ch,
			right_ch,
			state,
			plot_ctx,
			should_stop,
			config,
		)
	except KeyboardInterrupt:
		pass
	finally:
		print("\nShutting down BrainFlow session...")
		try:
			board.stop_stream()
		except Exception:
			pass
		try:
			board.release_session()
		except Exception:
			pass
		if config.enable_live_plot:
			finalize_live_plot()

	print("Done.")


if __name__ == "__main__":
	main()
