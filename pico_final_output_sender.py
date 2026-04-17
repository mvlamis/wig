# poll the Muse/GSR API and send final outputs to the matching Pico peripherals

import asyncio
import json
import struct
from urllib.request import urlopen

from bleak import BleakClient, BleakScanner

SERVICE_UUID = "8ab3d6f0-4c07-4fe0-a22f-3e5ca9e7f100"
OUTPUT_VALUE_UUID = "8ab3d6f0-4c07-4fe0-a22f-3e5ca9e7f102"
API_URL = "http://localhost:8000/api/scores"
TARGET_NAMES = {
    "left": "RPi-Pico-left",
    "right": "RPi-Pico-right",
}
POLL_INTERVAL_SEC = 0.25
SCAN_TIMEOUT_SEC = 10.0
RECONNECT_DELAY_SEC = 2.0


def clamp_percent(value: float) -> int:
    return max(0, min(100, int(round(value))))


async def fetch_scores() -> dict:
    def _load() -> dict:
        with urlopen(API_URL, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    return await asyncio.to_thread(_load)


async def find_device(participant: str):
    target_name = TARGET_NAMES[participant]
    devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT_SEC)
    for device in devices:
        name = (device.name or "").strip()
        if name == target_name:
            return device
    return None


async def connect_and_send(participant: str) -> None:
    while True:
        try:
            device = await find_device(participant)
            if device is None:
                print(f"[{participant}] Pico not found; rescanning...")
                await asyncio.sleep(RECONNECT_DELAY_SEC)
                continue

            print(f"[{participant}] Connecting to {device.address} ({device.name})...")
            async with BleakClient(device) as client:
                print(f"[{participant}] Connected.")

                last_sent = None
                while client.is_connected:
                    payload = await fetch_scores()
                    participant_payload = payload.get("participants", {}).get(participant, {})
                    bang_down = clamp_percent(participant_payload.get("bang_down_percent", 50.0))

                    if bang_down != last_sent:
                        await client.write_gatt_char(OUTPUT_VALUE_UUID, struct.pack("<H", bang_down), response=True)
                        print(f"[{participant}] sent bang_down={bang_down}%")
                        last_sent = bang_down

                    await asyncio.sleep(POLL_INTERVAL_SEC)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[{participant}] Sender error: {exc}")

        await asyncio.sleep(RECONNECT_DELAY_SEC)


async def main() -> None:
    print("Starting final-output sender for left and right Pico devices")
    await asyncio.gather(
        connect_and_send("left"),
        connect_and_send("right"),
    )


if __name__ == "__main__":
    asyncio.run(main())
