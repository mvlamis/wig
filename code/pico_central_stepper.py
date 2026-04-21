import asyncio
import os
import struct

from bleak import BleakClient, BleakScanner

OUTPUT_VALUE_UUID = "8ab3d6f0-4c07-4fe0-a22f-3e5ca9e7f102"

PICO_NAME_PREFIX = os.getenv("WIG_PICO_NAME_PREFIX", "RPi-Pico-")
LEFT_ADDRESS = os.getenv("WIG_PICO_LEFT_ADDRESS", "").strip()
RIGHT_ADDRESS = os.getenv("WIG_PICO_RIGHT_ADDRESS", "").strip()

SCAN_TIMEOUT_SEC = 10.0

ClientMap = dict[str, BleakClient]


def clamp_percent(value: float) -> int:
    return max(1, min(100, int(round(value))))


def participant_from_name(name: str | None) -> str | None:
    if not name or not name.startswith(PICO_NAME_PREFIX):
        return None

    suffix = name[len(PICO_NAME_PREFIX) :].strip().lower()
    if suffix in ("left", "right"):
        return suffix
    return None


def encode_percent(percent: int) -> bytes:
    return struct.pack("<H", clamp_percent(percent))


async def find_device_for_participant(participant: str):
    target_address = LEFT_ADDRESS if participant == "left" else RIGHT_ADDRESS
    if target_address:
        return await BleakScanner.find_device_by_address(target_address, timeout=SCAN_TIMEOUT_SEC)

    devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT_SEC)
    for device in devices:
        if participant_from_name(device.name) == participant:
            return device
    return None


async def connect_participant(participant: str, clients: ClientMap, ready_events: dict[str, asyncio.Event], stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        device = await find_device_for_participant(participant)
        if device is None:
            print(f"[{participant}] Peripheral not found; retrying...")
            await asyncio.sleep(2.0)
            continue

        print(f"[{participant}] Connecting to {device.address} ({device.name})...")
        client = BleakClient(device)
        try:
            await client.connect()
            clients[participant] = client
            ready_events[participant].set()
            print(f"[{participant}] Connected.")

            while client.is_connected and not stop_event.is_set():
                await asyncio.sleep(1.0)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[{participant}] Connection error: {exc}")
        finally:
            ready_events[participant].clear()
            clients.pop(participant, None)
            if client.is_connected:
                try:
                    await client.disconnect()
                except Exception:
                    pass

        if not stop_event.is_set():
            await asyncio.sleep(2.0)


async def write_percent_to_participant(participant: str, percent: int, clients: ClientMap) -> None:
    client = clients.get(participant)
    if client is None or not client.is_connected:
        print(f"[{participant}] Not connected")
        return

    target_percent = clamp_percent(percent)
    try:
        await client.write_gatt_char(
            OUTPUT_VALUE_UUID,
            encode_percent(target_percent),
            response=True,
        )
        print(f"[{participant}] Sent stepper target {target_percent}%")
    except Exception as exc:
        print(f"[{participant}] Failed to send {target_percent}%: {exc}")


async def write_targets(targets: dict[str, int], clients: ClientMap) -> None:
    tasks = [write_percent_to_participant(participant, percent, clients) for participant, percent in targets.items()]
    await asyncio.gather(*tasks)


def parse_startup_targets() -> list[str]:
    raw_value = input("Connect which picos? [left/right/both]: ").strip().lower()
    if raw_value in {"left", "right"}:
        return [raw_value]
    return ["left", "right"]


async def wait_for_initial_connections(selected_participants: list[str], ready_events: dict[str, asyncio.Event]) -> None:
    print("Waiting for initial BLE connections...")
    await asyncio.gather(*(ready_events[participant].wait() for participant in selected_participants))


async def command_loop(selected_participants: list[str], clients: ClientMap) -> None:
    print("Starting wireless stepper central")

    while True:
        try:
            raw_line = await asyncio.to_thread(input, "wig> ")
        except EOFError:
            return

        line = raw_line.strip()
        if not line:
            continue

        lowered = line.lower()
        if lowered in {"quit", "exit", "q"}:
            return

        parts = line.split()
        if len(parts) == 1:
            try:
                percent = clamp_percent(float(parts[0]))
            except ValueError:
                print("Enter a percent or a command like 'left 30'.")
                continue
            await write_targets({participant: percent for participant in selected_participants}, clients)
            continue

        if len(parts) == 2:
            target, value = parts[0].lower(), parts[1]
            try:
                percent = clamp_percent(float(value))
            except ValueError:
                print("Percent must be a number between 1 and 100.")
                continue

            if target in {"left", "right"}:
                if target not in selected_participants:
                    print(f"[{target}] Not selected at startup")
                    continue
                await write_targets({target: percent}, clients)
                continue

            if target in {"both", "all"}:
                await write_targets({participant: percent for participant in selected_participants}, clients)
                continue

        print("Unrecognized command. Type 'help' for options.")


async def main() -> None:
    selected_participants = parse_startup_targets()
    clients: ClientMap = {}
    ready_events = {participant: asyncio.Event() for participant in selected_participants}
    stop_event = asyncio.Event()
    connection_tasks = [
        asyncio.create_task(connect_participant(participant, clients, ready_events, stop_event))
        for participant in selected_participants
    ]
    try:
        await wait_for_initial_connections(selected_participants, ready_events)
        await command_loop(selected_participants, clients)
    except asyncio.CancelledError:
        raise
    finally:
        stop_event.set()
        for task in connection_tasks:
            task.cancel()
        await asyncio.gather(*connection_tasks, return_exceptions=True)
        for client in clients.values():
            try:
                await client.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())