from machine import ADC, Pin
import time

adc = ADC(Pin(26))

while True:
    adc_value = adc.read_u16()

    # print "wig" with i repeating as many times as the ADC value divided by 1000
    print("w" + "i" * (adc_value // 1000) + "g")

    time.sleep(0.2)