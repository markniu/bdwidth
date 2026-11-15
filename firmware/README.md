
Download the firmware into the STM32G431CBT6 chip that is on the bdwidth sensor

There are 2 ways:

### Flash with ccd_flash(recommend):

1. Press and Hold on the boot button on the bdwidth sensor

2. Power on the bdwidth sensor with usb cable 

3. Release the boot button

4. Open the [ccd_flash.exe](https://github.com/markniu/bdwidth/tree/main/tool) (or ccd_flash.py)

5. Choose the UART port and firmware file like [bdwidth20260718.hex](https://github.com/markniu/bdwidth/tree/main/firmware) Click Flash Firmware

7. Finish

8. Test
   To reboot the sensor, simply unplug and plug the USB cable. Then open the ccd_flash.exe tool,choose port and click the 'Get CCD Data' button. You will see the plot data from the sensor.

![/ccdflash.jpg](https://pandapi3d.cn/ccdflash.jpg)

### Flash with STM32CubeProgrammer:

1. Press and Hold on the boot button on the bdwidth sensor

2. Power on the bdwidth sensor with usb cable 

3. Release the boot button

4. Open the STM32CubeProgrammer.exe 

5. Choose the UART port and click connect button in the STM32CubeProgrammer

6. Open the firmware file for example: [bdwidth_v1.1.hex](https://github.com/markniu/bdwidth/tree/main/firmware) --> Click Download

7. Finish

video: https://youtu.be/c74Q1chOo8M
