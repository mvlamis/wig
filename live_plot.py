from collections import deque
from dataclasses import dataclass, field

import matplotlib.pyplot as plt


@dataclass
class PlotContext:
	fig: any
	ax_score: any
	ax_features: any
	score_line: any
	beta_alpha_line: any
	faa_line: any
	t: deque = field(default_factory=deque)
	score: deque = field(default_factory=deque)
	beta_alpha: deque = field(default_factory=deque)
	faa: deque = field(default_factory=deque)


def setup_live_plot() -> PlotContext:
	plt.ion()
	fig, (ax_score, ax_features) = plt.subplots(2, 1, figsize=(10, 7), sharex=True)

	score_line, = ax_score.plot([], [], color="tab:red", linewidth=2, label="Discomfort/Anxiety")
	ax_score.set_ylabel("Score (0-100)")
	ax_score.set_ylim(0, 100)
	ax_score.grid(True, alpha=0.3)
	ax_score.legend(loc="upper left")

	beta_alpha_line, = ax_features.plot([], [], color="tab:blue", linewidth=1.5, label="Beta/Alpha")
	faa_line, = ax_features.plot([], [], color="tab:green", linewidth=1.5, label="FAA")
	ax_features.set_xlabel("Time (s)")
	ax_features.set_ylabel("Feature Value")
	ax_features.grid(True, alpha=0.3)
	ax_features.legend(loc="upper left")

	fig.suptitle("Muse 2 Live Anxiety Estimation")
	fig.tight_layout()

	return PlotContext(
		fig=fig,
		ax_score=ax_score,
		ax_features=ax_features,
		score_line=score_line,
		beta_alpha_line=beta_alpha_line,
		faa_line=faa_line,
	)


def update_live_plot(
	plot_ctx: PlotContext,
	elapsed: float,
	score: float,
	beta_alpha: float,
	faa: float,
	plot_history_sec: float,
	epsilon: float,
) -> None:
	plot_ctx.t.append(elapsed)
	plot_ctx.score.append(score)
	plot_ctx.beta_alpha.append(beta_alpha)
	plot_ctx.faa.append(faa)

	while plot_ctx.t and (elapsed - plot_ctx.t[0]) > plot_history_sec:
		plot_ctx.t.popleft()
		plot_ctx.score.popleft()
		plot_ctx.beta_alpha.popleft()
		plot_ctx.faa.popleft()

	t_vals = list(plot_ctx.t)
	score_vals = list(plot_ctx.score)
	beta_alpha_vals = list(plot_ctx.beta_alpha)
	faa_vals = list(plot_ctx.faa)

	plot_ctx.score_line.set_data(t_vals, score_vals)
	plot_ctx.beta_alpha_line.set_data(t_vals, beta_alpha_vals)
	plot_ctx.faa_line.set_data(t_vals, faa_vals)

	if t_vals:
		x_min = max(0.0, t_vals[-1] - plot_history_sec)
		x_max = max(plot_history_sec, t_vals[-1])
		plot_ctx.ax_score.set_xlim(x_min, x_max)
		plot_ctx.ax_features.set_xlim(x_min, x_max)

	if beta_alpha_vals and faa_vals:
		feature_min = min(min(beta_alpha_vals), min(faa_vals))
		feature_max = max(max(beta_alpha_vals), max(faa_vals))
		pad = max(0.05, 0.1 * (feature_max - feature_min + epsilon))
		plot_ctx.ax_features.set_ylim(feature_min - pad, feature_max + pad)

	plot_ctx.fig.canvas.draw_idle()
	plt.pause(0.001)


def finalize_live_plot() -> None:
	plt.ioff()
	plt.show()