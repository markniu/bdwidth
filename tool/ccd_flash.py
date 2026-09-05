#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
bdwidth - CCD Data Viewer & Firmware Flashing Tool
- Real-time CCD data plotting (baudrate 500000)
- STM32 DFU firmware flashing (115200, Even parity)
- Bootloader mode prompt and power cycle reminder
"""

import sys
import serial
import serial.tools.list_ports
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import time
import struct
import os

# ============================================================
# STM32 Bootloader Class (G431 compatible)
# ============================================================
class STM32BootloaderError(Exception):
    pass

class STM32Bootloader:
    CMD_GET          = 0x00
    CMD_GET_VERSION  = 0x01
    CMD_GET_ID       = 0x02
    CMD_READ_MEMORY  = 0x11
    CMD_GO           = 0x21
    CMD_WRITE_MEMORY = 0x31
    CMD_ERASE        = 0x43
    CMD_EXT_ERASE    = 0x44

    ACK  = 0x79
    NACK = 0x1F

    def __init__(self, port, baudrate=115200, timeout=5.0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.ser = None
        self.chip_id = None
        self.use_ext_erase = True

    def _debug(self, msg):
        print(msg)

    def _open_port(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            self.ser = None
            time.sleep(0.2)
        self.ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=8,
            parity='E',
            stopbits=1,
            timeout=self.timeout
        )
        self.ser.flushInput()
        self.ser.flushOutput()

    def _wait_ack(self, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            b = self.ser.read(1)
            if b:
                if b[0] == self.ACK:
                    return True
                elif b[0] == self.NACK:
                    raise STM32BootloaderError("NACK received")
            time.sleep(0.01)
        raise STM32BootloaderError("Timeout waiting for ACK")

    def connect(self, retries=5):
        self._open_port()
        for attempt in range(retries):
            self.ser.write(b'\x7F')
            time.sleep(0.1)
            resp = self.ser.read(1)
            if resp and resp[0] in (self.ACK, 0x00):
                time.sleep(0.05)
                return True
            self.ser.flushInput()
            time.sleep(0.2)
        self.ser.close()
        self.ser = None
        raise STM32BootloaderError(
            "Bootloader sync failed!\n"
            "Ensure device is in bootloader mode (hold BOOT0 HIGH, reset).\n"
            f"Port: {self.port}, Baudrate: {self.baudrate}, Parity: EVEN"
        )

    def _send_cmd(self, cmd):
        self.ser.write(bytes([cmd, cmd ^ 0xFF]))
        self._wait_ack()

    def get_cmd_list(self):
        self._send_cmd(self.CMD_GET)
        n = self.ser.read(1)
        if not n:
            raise STM32BootloaderError("GET: no length byte")
        n = n[0]
        ver = self.ser.read(1)
        cmds = self.ser.read(n)
        self._wait_ack()
        if self.CMD_EXT_ERASE in cmds:
            self.use_ext_erase = True
        elif self.CMD_ERASE in cmds:
            self.use_ext_erase = False
        return cmds

    def get_id(self):
        self._send_cmd(self.CMD_GET_ID)
        n = self.ser.read(1)
        if not n:
            raise STM32BootloaderError("GET_ID: no length byte")
        n = n[0] + 1
        id_bytes = self.ser.read(n)
        self._wait_ack()
        chip_id = int.from_bytes(id_bytes, 'big')
        self.chip_id = chip_id
        return chip_id

    def erase_all(self):
        if self.use_ext_erase:
            self._send_cmd(self.CMD_EXT_ERASE)
            payload = b'\xFF\xFF\x00'
            self.ser.write(payload)
            deadline = time.time() + 30.0
            while time.time() < deadline:
                b = self.ser.read(1)
                if b:
                    if b[0] == self.ACK:
                        return
                    elif b[0] == self.NACK:
                        raise STM32BootloaderError("Mass erase NACK")
                time.sleep(0.1)
            raise STM32BootloaderError("Mass erase timeout")
        else:
            self._send_cmd(self.CMD_ERASE)
            self.ser.write(b'\xFF\x00')
            self._wait_ack(timeout=30.0)

    def write_memory(self, address, data, progress_callback=None):
        chunk_size = 256
        total = len(data)
        offset = 0
        while offset < total:
            chunk = data[offset: offset + chunk_size]
            clen = len(chunk)
            pad = (4 - clen % 4) % 4
            if pad:
                chunk = chunk + b'\xFF' * pad

            addr = address + offset
            addr_bytes = struct.pack('>I', addr)
            addr_crc = addr_bytes[0] ^ addr_bytes[1] ^ addr_bytes[2] ^ addr_bytes[3]

            self._send_cmd(self.CMD_WRITE_MEMORY)
            self.ser.write(addr_bytes + bytes([addr_crc]))
            self._wait_ack()

            n_byte = len(chunk) - 1
            checksum = n_byte
            for b in chunk:
                checksum ^= b
            self.ser.write(bytes([n_byte]) + chunk + bytes([checksum]))
            self._wait_ack()

            offset += clen
            if progress_callback:
                progress_callback(offset, total)

    def read_memory(self, address, length, progress_callback=None):
        chunk_size = 64
        result = bytearray()
        offset = 0
        while offset < length:
            n = min(chunk_size, length - offset)
            addr = address + offset

            self.ser.reset_input_buffer()
            self.ser.write(b'\x11\xee')
            addr_bytes = struct.pack('>I', addr)
            addr_crc = addr_bytes[0] ^ addr_bytes[1] ^ addr_bytes[2] ^ addr_bytes[3]
            self.ser.write(addr_bytes + bytes([addr_crc]))
            self._wait_ack(timeout=5.0)

            n_minus_1 = n - 1
            self.ser.write(bytes([n_minus_1, n_minus_1 ^ 0xFF]))
            self._wait_ack(timeout=5.0)

            deadline = time.time() + 5.0
            data = bytearray()
            while len(data) < n and time.time() < deadline:
                if self.ser.in_waiting:
                    data.extend(self.ser.read(n - len(data)))
                else:
                    time.sleep(0.001)
            if len(data) != n:
                raise STM32BootloaderError(f"Read incomplete: expected {n}, got {len(data)} at 0x{addr:08X}")
            result.extend(data)

            self._wait_ack(timeout=15.0)

            offset += n
            if progress_callback:
                progress_callback(offset, length)

            time.sleep(0.02)

        return bytes(result)

    def go(self, address):
        addr_bytes = struct.pack('>I', address)
        checksum = addr_bytes[0] ^ addr_bytes[1] ^ addr_bytes[2] ^ addr_bytes[3]
        self._send_cmd(self.CMD_GO)
        self.ser.write(addr_bytes + bytes([checksum]))
        try:
            self._wait_ack(timeout=2.0)
        except:
            pass

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            self.ser = None
            time.sleep(0.2)


# ============================================================
# Main GUI Application
# ============================================================
class CCDViewerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("bdwidth - CCD Data Viewer & Firmware Flashing Tool")
        self.root.geometry("1100x700")

        self.serial_connection = None
        self.is_running = False
        self.data_queue = []
        self.data_lock = threading.Lock()
        self.frame_count = 0

        # Rolling save of the most recent frames as CSV.  One row per
        # frame: the first column is the frame number and the remaining
        # columns are that frame's pixel values, so every frame stays
        # distinguishable in the file.
        self.recent_frames = []
        self.recent_max_frames = 200
        self.last_csv_save = 0.0
        self.csv_save_interval = 1.0
        # With PyInstaller --onefile, __file__ points into the unpacked
        # temp bundle, so use the exe's own folder when frozen.
        if getattr(sys, "frozen", False):
            base_dir = os.path.dirname(os.path.abspath(sys.executable))
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        self.csv_path = os.path.join(base_dir, "ccd_recent_200.csv")

        self.firmware_path = None
        self.firmware_data = None
        self.start_addr = 0x08000000

        self.setup_ui()
        self.setup_plot()
        self.scan_ports()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    # ---------- UI ----------
    def setup_ui(self):
        main_frame = ttk.Frame(self.root, padding="5")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(2, weight=1)

        ctrl = ttk.LabelFrame(main_frame, text="Control Panel", padding="5")
        ctrl.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 5))
        ctrl.columnconfigure(5, weight=1)

        ttk.Label(ctrl, text="Serial Port:").grid(row=0, column=0, padx=(5, 5))
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(ctrl, textvariable=self.port_var, width=15)
        self.port_combo.grid(row=0, column=1, padx=(0, 10))

        self.scan_btn = ttk.Button(ctrl, text="Scan Ports", command=self.scan_ports)
        self.scan_btn.grid(row=0, column=2, padx=(0, 5))

        # 修改按钮文本为 "Get CCD Data"
        self.connect_btn = ttk.Button(ctrl, text="Get CCD Data", command=self.toggle_connection)
        self.connect_btn.grid(row=0, column=3, padx=(0, 5))

        self.status_label = ttk.Label(ctrl, text="Status: Disconnected", foreground="red")
        self.status_label.grid(row=0, column=4, padx=(10, 0))

        upd = ttk.LabelFrame(main_frame, text="Firmware Update", padding="5")
        upd.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 5))
        upd.columnconfigure(5, weight=1)

        self.select_btn = ttk.Button(upd, text="Select Firmware", command=self.select_firmware_file)
        self.select_btn.grid(row=0, column=0, padx=(5, 5))

        self.file_label = ttk.Label(upd, text="No file selected", foreground="gray")
        self.file_label.grid(row=0, column=1, padx=(5, 5))

        self.flash_btn = ttk.Button(upd, text="Flash Firmware", command=self.one_click_flash, state="disabled")
        self.flash_btn.grid(row=0, column=2, padx=(5, 5))

        self.progress_bar = ttk.Progressbar(upd, length=200, mode='determinate')
        self.progress_bar.grid(row=0, column=3, padx=(5, 5))

        self.update_status_label = ttk.Label(upd, text="Status: Idle", foreground="blue")
        self.update_status_label.grid(row=0, column=4, padx=(5, 5))

        self.speed_label = ttk.Label(upd, text="", foreground="green")
        self.speed_label.grid(row=0, column=5, padx=(5, 5))

        plot_container = ttk.Frame(main_frame)
        plot_container.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        plot_container.columnconfigure(0, weight=1)
        plot_container.rowconfigure(0, weight=1)
        self.plot_frame = ttk.LabelFrame(plot_container, text="CCD Data", padding="5")
        self.plot_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.plot_frame.columnconfigure(0, weight=1)
        self.plot_frame.rowconfigure(0, weight=1)

    def setup_plot(self):
        self.fig, self.ax_plot = plt.subplots(figsize=(10, 4))
        self.fig.subplots_adjust(left=0.08, right=0.98, bottom=0.1, top=0.95)
        self.ax_plot.set_title("CCD Pixel Amplitude", fontsize=12)
        self.ax_plot.set_xlabel("Pixel Index")
        self.ax_plot.set_ylabel("Amplitude")
        self.ax_plot.grid(True, alpha=0.3)
        self.ax_plot.set_ylim(0, 4200)
        self.ax_plot.set_xlim(0, 2600)
        self.line, = self.ax_plot.plot([], [], 'b.-', markersize=1.5, linewidth=0.5)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_frame)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        toolbar_frame = ttk.Frame(self.plot_frame)
        toolbar_frame.grid(row=1, column=0, sticky=(tk.W, tk.E))
        self.toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame)
        self.toolbar.update()
        self.plot_frame.rowconfigure(0, weight=1)

    # ---------- Serial ----------
    def scan_ports(self):
        ports = serial.tools.list_ports.comports()
        self.port_combo['values'] = [p.device for p in ports]
        if ports:
            self.port_var.set(ports[0].device)

    def toggle_connection(self):
        if self.serial_connection and self.serial_connection.is_open:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        port = self.port_var.get()
        if not port:
            messagebox.showerror("Error", "Select a serial port")
            return
        try:
            self.serial_connection = serial.Serial(port=port, baudrate=500000, timeout=1)
            self.serial_connection.flushInput()
            self.serial_connection.write(b"D01;")
            time.sleep(0.1)

            self.is_running = True
            self.frame_count = 0
            # 连接后按钮显示 Stop
            self.connect_btn.config(text="Stop")
            self.status_label.config(text="Status: Connected", foreground="green")

            threading.Thread(target=self.read_serial_data, daemon=True).start()
            threading.Thread(target=self.update_loop, daemon=True).start()
        except Exception as e:
            messagebox.showerror("Error", f"Connect failed: {e}")

    def disconnect(self):
        self.is_running = False
        # Flush the latest frames to CSV before stopping.
        self.save_recent_csv()
        if self.serial_connection:
            try:
                self.serial_connection.close()
            except:
                pass
            self.serial_connection = None
        # 断开后按钮恢复为 Get CCD Data
        self.connect_btn.config(text="Get CCD Data")
        self.status_label.config(text="Status: Disconnected", foreground="red")
        self.line.set_data([], [])
        self.canvas.draw_idle()
        with self.data_lock:
            self.data_queue.clear()

    # ---------- Firmware file selection ----------
    def parse_hex_file(self, filepath):
        data_dict = {}
        base_addr = 0
        min_addr = 0xFFFFFFFF
        with open(filepath, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line[0] != ':':
                    continue
                try:
                    bc = int(line[1:3], 16)
                    addr = int(line[3:7], 16)
                    rt = int(line[7:9], 16)
                    dh = line[9:9 + bc * 2]
                except:
                    continue
                if rt == 0x00:
                    abs_addr = base_addr + addr
                    for i in range(0, len(dh), 2):
                        data_dict[abs_addr + i//2] = int(dh[i:i+2], 16)
                    if abs_addr < min_addr:
                        min_addr = abs_addr
                elif rt == 0x01:
                    break
                elif rt == 0x02:
                    base_addr = int(dh, 16) << 4
                elif rt == 0x04:
                    base_addr = int(dh, 16) << 16
        if not data_dict:
            raise ValueError("No data in HEX")
        data = bytearray()
        cur = min_addr
        for addr in sorted(data_dict):
            while cur < addr:
                data.append(0xFF)
                cur += 1
            data.append(data_dict[addr])
            cur = addr + 1
        while data and data[-1] == 0xFF:
            data.pop()
        return data, min_addr

    def select_firmware_file(self):
        path = filedialog.askopenfilename(
            title="Select Firmware",
            filetypes=[("Hex files", "*.hex"), ("Binary files", "*.bin"), ("All", "*.*")]
        )
        if not path:
            return
        self.firmware_path = path
        self.file_label.config(text=os.path.basename(path), foreground="green")
        try:
            ext = os.path.splitext(path)[1].lower()
            if ext == '.hex':
                data, start_addr = self.parse_hex_file(path)
                self.firmware_data = data
                self.start_addr = start_addr
            else:
                with open(path, 'rb') as f:
                    data = f.read()
                self.firmware_data = data
                self.start_addr = 0x08000000
            self.flash_btn.config(state="normal")
            self.update_status_label.config(text="Status: File ready", foreground="green")
        except Exception as e:
            messagebox.showerror("Error", f"Load failed: {e}")
            self.firmware_data = None
            self.flash_btn.config(state="disabled")

    # ---------- DFU Flash ----------
    def one_click_flash(self):
        if not self.firmware_data:
            messagebox.showerror("Error", "Select firmware first")
            return

        if not messagebox.askyesno(
            "Enter Bootloader Mode",
            "Please ensure the device is in bootloader mode:\n\n"
            "1. Press and HOLD the BOOT button\n"
            "2. Power on (or reset) the device\n"
            "3. Release the BOOT button\n\n"
            "Click 'Yes' if you have done this, 'No' to cancel."
        ):
            return

        if self.serial_connection and self.serial_connection.is_open:
            self.disconnect()
            time.sleep(0.5)

        self.flash_btn.config(state="disabled")
        self.select_btn.config(state="disabled")
        self.connect_btn.config(state="disabled")
        self.progress_bar['value'] = 0
        self.speed_label.config(text="")
        self.update_status_label.config(text="Status: Flashing...", foreground="orange")

        threading.Thread(target=self.run_dfu_flash, daemon=True).start()

    def run_dfu_flash(self):
        boot = None
        try:
            port = self.port_var.get()
            boot = STM32Bootloader(port, baudrate=115200, timeout=10.0)
            boot.connect(retries=5)

            boot.get_cmd_list()
            chip_id = boot.get_id()
            print(f"Chip ID: 0x{chip_id:04X}")

            # Erase
            self._update_status("Erasing...", 15)
            boot.erase_all()
            self._update_status("Erase done", 25)

            data = self.firmware_data
            start_addr = self.start_addr
            total = len(data)
            t0 = time.time()

            # Write
            def write_progress(written, total):
                pct = 25 + int(65 * written / total)
                self._update_progress(pct)
                elapsed = time.time() - t0
                speed = written / elapsed / 1024 if elapsed > 0 else 0
                self.root.after(0, lambda: self.speed_label.config(text=f"{speed:.1f} KB/s"))

            self._update_status("Writing...", 30)
            boot.write_memory(start_addr, data, write_progress)
            self._update_progress(90)
            self._update_status("Write done", 90)

            # Jump
            self._update_status("Jumping to user code...", 95)
            boot.go(start_addr)
            time.sleep(1)

            # Complete
            elapsed = time.time() - t0
            speed = total / elapsed / 1024 if elapsed > 0 else 0
            self._update_status("Success!", 100)
            self.root.after(0, lambda: self.update_status_label.config(text="Status: ✓ Flash successful! (power cycle needed)", foreground="green"))
            self.root.after(0, lambda: self.speed_label.config(text=f"Done {elapsed:.1f}s @ {speed:.1f} KB/s"))
            messagebox.showinfo("Success", f"Flashing complete!\nPlease power cycle the device (unplug/replug USB) to restart.\nSize: {total} bytes\nTime: {elapsed:.1f}s")

        except Exception as e:
            self.root.after(0, lambda: self.update_status_label.config(text="Status: Error", foreground="red"))
            self.root.after(0, lambda: self.speed_label.config(text="Failed", foreground="red"))
            messagebox.showerror("Error", str(e))
        finally:
            if boot:
                boot.close()
            self.root.after(0, self._enable_buttons)

    def _update_status(self, msg, pct):
        self.root.after(0, lambda: self.update_status_label.config(text=f"Status: {msg}", foreground="orange"))
        self.root.after(0, lambda: self.progress_bar.config(value=pct))

    def _update_progress(self, val):
        self.root.after(0, lambda: self.progress_bar.config(value=val))

    def _enable_buttons(self):
        self.flash_btn.config(state="normal" if self.firmware_data else "disabled")
        self.select_btn.config(state="normal")
        self.connect_btn.config(state="normal")

    # ---------- CCD data reading ----------
    def read_serial_data(self):
        buffer = bytearray()
        while self.is_running:
            try:
                if self.serial_connection and self.serial_connection.is_open:
                    waiting = self.serial_connection.in_waiting
                    if waiting:
                        data = self.serial_connection.read(waiting)
                        for byte in data:
                            buffer.append(byte)
                            if len(buffer) >= 2 and buffer[-2:] == b'\xff\xff':
                                nums = []
                                for i in range(0, len(buffer)-2, 2):
                                    val = ((buffer[i+1] << 8) + buffer[i]) & 0xFFFF
                                    nums.append(min(val, 4096))
                                if 2540 < len(nums) < 2600:
                                    nums.append(0)
                                    with self.data_lock:
                                        self.data_queue.append(nums)
                                        self.frame_count += 1
                                        # Keep the trailing 0 only for the
                                        # plot ylim; save clean sensor data.
                                        values = nums[:-1]
                                        self.recent_frames.append(
                                            (self.frame_count, values)
                                        )
                                        if len(self.recent_frames) > self.recent_max_frames:
                                            del self.recent_frames[
                                                :len(self.recent_frames)
                                                - self.recent_max_frames
                                            ]
                                    now = time.time()
                                    if now - self.last_csv_save >= self.csv_save_interval:
                                        self.last_csv_save = now
                                        self.save_recent_csv()
                                buffer.clear()
                else:
                    time.sleep(0.01)
            except Exception as e:
                print(f"Read error: {e}")
                time.sleep(0.01)

    def update_loop(self):
        while self.is_running:
            try:
                if self.data_queue:
                    with self.data_lock:
                        nums = self.data_queue.pop(0)
                    self.root.after(0, lambda n=nums: self._draw(n))
            except Exception as e:
                print(f"Update error: {e}")
            time.sleep(0.05)

    def _draw(self, nums):
        if not nums:
            return
        self.line.set_data(range(len(nums)), nums)
        ymax = max(nums) if nums else 4200
        self.ax_plot.set_ylim(0, ymax * 1.1)
        self.canvas.draw_idle()

    def save_recent_csv(self):
        """Write the most recent frames to CSV (overwrite).

        One row per frame: frame number first, then the pixel values of
        that frame.  The file always holds the latest
        `self.recent_max_frames` frames.
        """
        try:
            with self.data_lock:
                rows = [
                    ",".join([str(index)] + [str(v) for v in values])
                    for index, values in self.recent_frames
                ]
            with open(self.csv_path, "w", encoding="ascii", newline="") as fh:
                fh.write("\n".join(rows) + "\n")
        except Exception as e:
            print(f"CSV save error: {e}")

    def on_closing(self):
        self.is_running = False
        self.save_recent_csv()
        if self.serial_connection:
            try:
                self.serial_connection.close()
            except:
                pass
        plt.close('all')
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = CCDViewerGUI(root)
    root.mainloop()