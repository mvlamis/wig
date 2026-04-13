import signal

from brainflow.board_shim import BoardShim

from muse_api import start_score_server
from muse_config import CONFIG
from muse_state import create_runtime_state
from muse_worker import build_input_params_for_participant, run_stream_loop


def main() -> None:
    config = CONFIG
    if config.enable_dev_logger:
        BoardShim.enable_dev_board_logger()

    state = create_runtime_state(config)
    start_score_server(
        state,
        config.api_host,
        config.api_port,
        config.api_default_participant,
    )

    should_stop = {"flag": False}

    def _stop_handler(signum, frame):
        _ = signum, frame
        should_stop["flag"] = True

    signal.signal(signal.SIGINT, _stop_handler)

    boards: dict[str, BoardShim] = {}

    print("Preparing Muse session")
    try:
        for participant_id in config.participants:
            try:
                board = BoardShim(
                    config.board_id,
                    build_input_params_for_participant(config, participant_id),
                )
                board.prepare_session()
                board.start_stream()
                boards[participant_id] = board
                print(f"[{participant_id}] Muse stream started")
            except Exception as exc:
                print(f"[{participant_id}] Muse connection failed: {exc}")

        if not boards:
            raise RuntimeError("No Muse headbands could be started.")

        run_stream_loop(
            boards,
            state,
            should_stop,
            config,
        )
    except KeyboardInterrupt:
        pass
    finally:
        print("\nShutting down BrainFlow session...")
        for board in boards.values():
            try:
                board.stop_stream()
            except Exception:
                pass
            try:
                board.release_session()
            except Exception:
                pass

    print("Done.")


if __name__ == "__main__":
    main()
