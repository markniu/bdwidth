import sys
import serial
import matplotlib.pyplot as plt
from matplotlib.widgets import Button
import time


import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import numpy.fft as nf


initial_xlim = 0
initial_ylim = 0

width_list = [0,0,0,0,0]
width_index = 0

def setup_serial(port, baudrate):
    ser = serial.Serial(port=port, baudrate=baudrate, timeout=1)
    return ser

def read_serial(ser):
    buffer = bytearray()
    global width_index
    # start with 4096 to fix the ylim
    numbers = [4096]

    try:
        if ser.is_open:
            while True:
                data = ser.read(ser.in_waiting)
                if data:
                    for byte in data:
                        buffer.append(byte)

                        if len(buffer) >= 2 and buffer[-2:] == b'\xff\xff':
                            # Process the buffer contents as two-byte integers
                            numbers.clear()
                            width = 0
                            sum = 0
                            sum1 = 0
                            sum2 = 0
                            max_width = 0
                            for i in range(0, len(buffer) - 2, 2):
                                two_bytes = ((buffer[i+1] << 8) + buffer[i ])&0xffff
                                # prevent the graph from getting blown out by glitches
                                if two_bytes > 4096:
                                    two_bytes = 4096
                                numbers.append(two_bytes)
                            #print(len(numbers))
                            # try to reject nonsense values caused by ADC or UART glitches
                            if len(numbers) > 2540 and len(numbers) < 2600 :
                                # add 0 to fix the ylim
                                numbers.append(0)
                                update_graph(numbers)
                            buffer.clear()
    except KeyboardInterrupt:
        print("keyboard interrupt -- closing the serial connection")
        ser.close()

def fft_tran(T,sr):
    complex_ary = nf.fft(sr)
    y_ = nf.ifft(complex_ary).real
    fft_freq = nf.fftfreq(y_.size, T[1] - T[0])
    fft_pow = np.abs(complex_ary)  # 复数的摸-Y轴
    return fft_freq, fft_pow

def update_graph(numbers):
    global initial_xlim, initial_ylim
    if len(numbers)<=0:
        return
    current_xlim = ax_plot.get_xlim()
    current_ylim = ax_plot.get_ylim()


    ax_plot.clear()
    x = list(range(len(numbers)))
    y = numbers

    ax_plot.plot(x, y, "b.-")

    if current_xlim != initial_xlim or current_ylim != initial_ylim:
        ax_plot.set_xlim(current_xlim)
        ax_plot.set_ylim(current_ylim)

    plt.draw()
    plt.pause(update_interval)

if __name__ == "__main__":
    port = sys.argv[1]
    baudrate = 500000 #int(sys.argv[2])
    update_interval = 0.1

    ser = setup_serial(port, baudrate)

    fig, ax_plot = plt.subplots()
    plt.title("pixel amplitude vs index")
    plt.xlabel("index")
    plt.ylabel("amplitude")

    numbers = []

    update_graph(numbers)

    initial_xlim = ax_plot.get_xlim()
    initial_ylim = ax_plot.get_ylim()
    ser.write("D01;".encode())
    read_serial(ser)
