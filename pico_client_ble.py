# Rui Santos & Sara Santos - Random Nerd Tutorials
# Complete project details at https://RandomNerdTutorials.com/raspberry-pi-pico-w-bluetooth-low-energy-micropython/

from micropython import const
import asyncio
import aioble
import bluetooth
import struct
from machine import ADC, Pin

_GSR_SERVICE_UUID = bluetooth.UUID("8ab3d6f0-4c07-4fe0-a22f-3e5ca9e7f100")
_GSR_VALUE_UUID = bluetooth.UUID("8ab3d6f0-4c07-4fe0-a22f-3e5ca9e7f101")
# org.bluetooth.characteristic.gap.appearance.xml (Generic Sensor)
_ADV_APPEARANCE_GENERIC_SENSOR = const(1344)
# How frequently to send advertising beacons.
_ADV_INTERVAL_MS = 250_000
_GSR_PIN = const(26)
_GSR_SAMPLE_INTERVAL_MS = const(200)
# set this device role directly in code: "left" or "right"
_DEVICE_ROLE = "left"
_DEVICE_NAME = f"RPi-Pico-{_DEVICE_ROLE}"

gsr_adc = ADC(Pin(_GSR_PIN))

# Register GATT server.
gsr_service = aioble.Service(_GSR_SERVICE_UUID)
gsr_characteristic = aioble.Characteristic(
    gsr_service, _GSR_VALUE_UUID, read=True, notify=True
)
aioble.register_services(gsr_service)

# Encode raw ADC value as uint16 little-endian.
def _encode_gsr(adc_value):
    return struct.pack("<H", int(adc_value) & 0xFFFF)

# Read GSR ADC and notify subscribers
async def sensor_task():
    while True:
        gsr_value = gsr_adc.read_u16()
        gsr_characteristic.write(_encode_gsr(gsr_value), send_update=True)
        print("GSR:", gsr_value)
        await asyncio.sleep_ms(_GSR_SAMPLE_INTERVAL_MS)
        
# Serially wait for connections. Don't advertise while a central is connected.
async def peripheral_task():
    while True:
        try:
            async with await aioble.advertise(
                _ADV_INTERVAL_MS,
                name=_DEVICE_NAME,
                services=[_GSR_SERVICE_UUID],
                appearance=_ADV_APPEARANCE_GENERIC_SENSOR,
                ) as connection:
                    print(f"{_DEVICE_ROLE} connected from", connection.device)
                    await connection.disconnected()
        except asyncio.CancelledError:
            # Catch the CancelledError
            print("Peripheral task cancelled")
        except Exception as e:
            print("Error in peripheral_task:", e)
        finally:
            # Ensure the loop continues to the next iteration
            await asyncio.sleep_ms(100)

# Run both tasks
async def main():
    print(f"Starting GSR peripheral role={_DEVICE_ROLE}, name={_DEVICE_NAME}")
    t1 = asyncio.create_task(sensor_task())
    t2 = asyncio.create_task(peripheral_task())
    await asyncio.gather(t1, t2)
    
asyncio.run(main())