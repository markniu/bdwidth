# Filament monitor Sensor bdwidth

- Through the generated CSV file, we can intuitively see the diameter of the entire filament roll in the line chart in any position
- Improve print quality with automatic flow rate adjustment and stability with the runout/clog feature.

#### bdwidth sensor is an optical width and motion sensor for 3D printer.
I developed a unique algorithm that uses light diffraction to automatically compensate for filament shadows on the CCD sensor, even when the filament moves at different distances and angles, we still can get the right width.

  Just power it, then you can measure your filament motion&diameter.  
 

<img  width="550"  src="https://static.wixstatic.com/media/0d0edf_54bec8b6d2c345c9acff65f798d85c5d~mv2.jpg/v1/fill/w_1374,h_802,al_c,q_85,usm_0.66_1.00_0.01/0d0edf_54bec8b6d2c345c9acff65f798d85c5d~mv2.jpg"/>

1. Flow compensation:  adjust the flow rate in real time

2. Jam/Runout: Pause the printer while jam or runout (laser optical tracking chip)
 
3. Width Accuracy: +/- 0.01mm (high resolution 0.005mm CCD sensor chip)
 
4. Connection: USB or I2C, Low power 5V*49mA = 0.245W

5. No calibration required

6. firmware update from the usb (Only needs updating when we release a new bdwidth.hex)
   
7. No mechanical contact with the filament, no wear due to the use of optical components



## Quick start

#### 1.Plug the bdwidth sensor into the USB port or I2C port(it can be any two gpios) on the 3D printer mainboard 


#### 2.Install software module
```
cd  ~
git clone https://github.com/markniu/bdwidth.git
chmod 777 ~/bdwidth/klipper/install.sh
~/bdwidth/klipper/install.sh
```

#### 3.Configure Klipper

add the following section into your klipper config file,

here we connect the bdwidth to the usb port

```
[bdwidth fila_width_0]
port:usb
#   usb or i2c 
#i2c_software_scl_pin:PA8
#i2c_software_sda_pin:PA14
#   needed if the port is i2c
serial:/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
#   needed if the port is usb
default_nominal_filament_diameter: 1.75 # (mm)
enable: all
#  disable or enable the sensor after power on.
#   the value should be one of width/motion/all/disable 
#   width(only enable the width function)
#   motion(only enable the motion function)
#   all(enable both the width and motion)
#   disable(disable both the width and motion)
min_diameter: 1.0
#   Minimal allowed diameter for flow rate adjust and runout.
max_diameter: 2.0
#   Maximum allowed diameter for flow rate adjust and runout.
#   The default is default_nominal_filament_diameter + max_difference.
extruder:extruder
runout_delay_length : 8.0  
#    (mm) increase this value if the print speed is high or with high flowrate 
#    else it may trigger the runout and pause
flowrate_adjust_length : 5  # (mm)
pause_on_runout: True
sample_time:2
#  in seconds, should be > 0.3s
sensor_to_nozzle_length: 750
#   The distance from sensor to the melting chamber/hot-end in
#   millimeters (mm). The filament between the sensor and the hot-end
#   will be treated as the default_nominal_filament_diameter. Host
#   module works with FIFO logic. It keeps each sensor value and
#   position in an array and POP them back in correct position. This
#   parameter must be provided.
#   how to measure:https://github.com/markniu/bdwidth/blob/main/doc/lengthToNozzle.jpg


logging: True
#   Out data to the file bdwidth.log.csv,
#   the data format: date time,diameter,total used filament,raw data of total used filament. 
#   for example:
#   7/12 23:20:18,1.764mm,1462.5mm,62594

debug_info: True
#   Output diameter to terminal/console and klipper.log 



```


### 4. Gcode for bdwidth:
```
SET_BDWIDTH NAME=xxx COMMAND=ENABLE  ;enable the bdwidth 
SET_BDWIDTH NAME=xxx COMMAND=DISABLE  ;disable the bdwidth 
SET_BDWIDTH NAME=xxx COMMAND=QUERY    ;read one data from bdwidth
```
note: the `xxx` is the name of bdwidth in the section [bdwidth xxx]
for example: [bdwidth fila_width_0] in the printer.cfg then we can run this command to enable it.
```
  SET_BDWIDTH NAME=fila_width_0 COMMAND=ENABLE
```

> The bdwidth is not enabled by default, we need to enable it with command, and Add the enable or disable command into the start G-code in the slicer.
> for example:SET_BDWIDTH NAME=fila_width_0 COMMAND=ENABLE



#### Wiki:https://pandapi3d.cn/
#### My store: [https://www.pandapi3d.com](https://www.pandapi3d.com)
#### [Test Video1](https://www.youtube.com/watch?v=Cj5bzoDzowE)  , [Test Video2](https://www.youtube.com/watch?v=vu5LtXh5HZw) 


