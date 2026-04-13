import asyncio
import json
import os
import socket
import struct
from datetime import datetime

from bleak import BleakClient, BleakScanner

GSR_SERVICE_UUID = "8ab3d6f0-4c07-4fe0-a22f-3e5ca9e7f100"
GSR_VALUE_UUID = "8ab3d6f0-4c07-4fe0-a22f-3e5ca9e7f101"

PICO_NAME_PREFIX = os.getenv("WIG_PICO_NAME_PREFIX", "RPi-Pico-")

LEFT_ADDRESS = os.getenv("WIG_PICO_LEFT_ADDRESS", "").strip()
RIGHT_ADDRESS = os.getenv("WIG_PICO_RIGHT_ADDRESS", "").strip()

UDP_HOST = os.getenv("WIG_GSR_UDP_HOST", "127.0.0.1")
UDP_PORT = int(os.getenv("WIG_GSR_UDP_PORT", "8765"))

RECONNECT_DELAY_SEC = 2.0
SCAN_TIMEOUT_SEC = 10.0


def encode_packet(value: int, participant: str) -> bytes:
    payload = {
        "participant": participant,
        "value": value,
        "timestamp": datetime.now().isoformat(timespec="milliseconds"),
    }
    return json.dumps(payload).encode("utf-8")


def participant_from_name(name: str | None) -> str | None:
    if not name:
        return None
    if not name.startswith(PICO_NAME_PREFIX):
        return None
    suffix = name[len(PICO_NAME_PREFIX):].strip().lower()
    if suffix in ("left", "right"):
        return suffix
    return None


async def find_device_for_participant(participant: str):
    target_address = LEFT_ADDRESS if participant == "left" else RIGHT_ADDRESS
    if target_address:
        return await BleakScanner.find_device_by_address(target_address, timeout=SCAN_TIMEOUT_SEC)

    devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT_SEC)
    for device in devices:
        role = participant_from_name(device.name)
        if role == participant:
            return device
    return None


async def stream_participant(participant: str, udp_sock: socket.socket):
    while True:
        try:
            device = await find_device_for_participant(participant)
            if device is None:
                print(f"[{participant}] Peripheral not found; retrying scan...")
                await asyncio.sleep(RECONNECT_DELAY_SEC)
                continue

            print(f"[{participant}] Connecting to {device.address} ({device.name})...")
            async with BleakClient(device) as client:
                print(f"[{participant}] Connected.")

                def handle_notification(_: int, data: bytearray):
                    if len(data) < 2:
                        return
                    gsr_value = struct.unpack("<H", data[:2])[0]
                    udp_sock.sendto(encode_packet(gsr_value, participant), (UDP_HOST, UDP_PORT))
                    print(f"[{participant}] GSR={gsr_value} -> udp://{UDP_HOST}:{UDP_PORT}")

                await client.start_notify(GSR_VALUE_UUID, handle_notification)
                print(f"[{participant}] Subscribed to GSR notifications")

                while client.is_connected:
                    await asyncio.sleep(1.0)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[{participant}] Central error: {exc}")

        await asyncio.sleep(RECONNECT_DELAY_SEC)


async def run() -> None:
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    print("Starting dual GSR central (left + right)")
    tasks = [
        asyncio.create_task(stream_participant("left", udp_sock)),
        asyncio.create_task(stream_participant("right", udp_sock)),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        udp_sock.close()


if __name__ == "__main__":
    asyncio.run(run())
