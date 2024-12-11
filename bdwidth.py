import logging
import math
import statistics
import serial

from . import bus
from . import filament_switch_sensor

MEASUREMENT_INTERVAL_MM = 10

CHECK_RUNOUT_TIMEOUT = 1.0
TIMER_READ_ANGLE = 0.5
BDWIDTH_CHIP_ADDR = 0x06
BDWIDTH_I2C_SPEED = 100000
MAX_LEN = 10
BDWIDTH_REGS = {
     '_measure_data' : 22,
     
    '_zmco'   : 0x00,
    '_zpos_hi' : 0x01,
     '_zpos_lo' : 0x02,
     '_mpos_hi' : 0x03,
     '_mpos_lo' : 0x04,
     '_mang_hi' : 0x05,
     '_mang_lo' : 0x06,
     '_conf_hi' : 0x07,
     '_conf_lo' : 0x08,
     '_raw_ang_hi' : 0x0c,
     '_raw_ang_lo' : 0x0d,
     '_ang_hi' : 0x0e,
     '_ang_lo' : 0x0f,
    
     '_agc' : 0x1a,
     '_mag_hi' : 0x1b,
     '_mag_lo' : 0x1c,
     '_burn' : 0xff
}


#[bdwidth]
##i2c_software_scl_pin:PB10
##i2c_software_sda_pin:PB11
#port:usb
#serial:/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
#extruder:extruder
#detection_length : 4.0
#pause_on_runout: True
#read_interval:1
#  in seconds
#measurement_delay: 70
#   The distance from sensor to the melting chamber/hot-end in
#   millimeters (mm). The filament between the sensor and the hot-end
#   will be treated as the default_nominal_filament_diameter. Host
#   module works with FIFO logic. It keeps each sensor value and
#   position in an array and POP them back in correct position. This
#   parameter must be provided.

#default_nominal_filament_diameter: 1.75 # (mm)
#   Maximum allowed filament diameter difference as mm.
#max_difference: 0.05
#enable: False
#   Sensor enabled or disabled after power on. The default is to
#   disable.
#min_diameter: 1.0
#   Minimal diameter for trigger virtual filament_switch_sensor.
#max_diameter:
#   Maximum diameter for triggering virtual filament_switch_sensor.
#   The default is default_nominal_filament_diameter + max_difference.
#logging: False
#   Out diameter to terminal and klipper.log can be turn on|of by
#   command.


class BDWidthMotionSensor:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.port = config.get("port")#
        # if config.get("resistance1", None) is None:
        if "i2c" in self.port:  
            self.i2c = bus.MCU_I2C_from_config(config, BDWIDTH_CHIP_ADDR, BDWIDTH_I2C_SPEED)
        elif "usb" in self.port:
            self.usb_port = config.get("serial")
            baudrate = 500000
            self.usb = serial.Serial(self.usb_port, baudrate,timeout=1)
        self.gcode = self.printer.lookup_object('gcode')
        self.extruder_name = config.get('extruder')
         
        self.runout_helper = filament_switch_sensor.RunoutHelper(config)
        self.get_status = self.runout_helper.get_status
        self.extruder = None
        self.estimated_print_time = None
        # Initialise internal state
        self.filament_runout_pos = None

        self.nominal_filament_dia = config.getfloat(
            'default_nominal_filament_diameter', above=1.0)
        self.measurement_delay = config.getfloat('measurement_delay', above=0.)
        self.measurement_max_difference = config.getfloat('max_difference',
                                                          above=0.)
        self.max_diameter = (self.nominal_filament_dia
                             + self.measurement_max_difference)
        self.min_diameter = (self.nominal_filament_dia
                             - self.measurement_max_difference)
 
        self.detection_length = config.getfloat(
            'detection_length', 7., above=0.)

        self.is_active =config.getboolean('enable', False)    
        self.runout_dia_min=config.getfloat('runout_min_diameter', 1.0)
        self.runout_dia_max=config.getfloat('runout_max_diameter', 1.9)
        self.read_interval=config.getfloat('read_interval', 1.0) # in second
        self.is_log =config.getboolean('logging', False)
        
        self.lastFilamentWidthReading = 0
        self.lastMotionReading = 0
        self.actual_total_move = 0
        self.filament_array = []
        # Register commands and event handlers
        self.printer.register_event_handler('klippy:ready',
                                            self._handle_ready)

        self.printer.register_event_handler('idle_timeout:ready',
                                            self._handle_not_printing)
        self.printer.register_event_handler('idle_timeout:idle',
                                            self._handle_not_printing)

        self.filament_array = []                                     
        self.extrude_factor_update_timer = self.reactor.register_timer(
            self.extrude_factor_update_event)
            
    #def handle_connect(self):
        #self.reactor.update_timer(self.sample_timer, self.reactor.NOW)
        self.extruder_pos_old = 0
        self.angel_to_len_old = 0
        self.gcode.register_command('QUERY_FILAMENT_WIDTH', self.cmd_M407)
        self.gcode.register_command('RESET_FILAMENT_WIDTH_SENSOR',
                                        self.cmd_ClearFilamentArray)
        self.gcode.register_command('DISABLE_FILAMENT_WIDTH_SENSOR',
                                        self.cmd_M406)
        self.gcode.register_command('ENABLE_FILAMENT_WIDTH_SENSOR',
                                        self.cmd_M405)
        self.gcode.register_command('ENABLE_FILAMENT_WIDTH_LOG',
                                    self.cmd_log_enable)
        self.gcode.register_command('DISABLE_FILAMENT_WIDTH_LOG',
                                    self.cmd_log_disable)

    def update_filament_array(self, last_epos):
        # Fill array
        if len(self.filament_array) > 0:
            # Get last reading position in array & calculate next
            # reading position
            next_reading_position = (self.filament_array[-1][0]
                                     + MEASUREMENT_INTERVAL_MM)
            if next_reading_position <= (last_epos + self.measurement_delay):
                self.filament_array.append([last_epos + self.measurement_delay,
                                            self.lastFilamentWidthReading])
                if self.is_log == True:
                    self.gcode.respond_info("self.lastFilamentWidthReading:%.3f" % (self.lastFilamentWidthReading))                             
        else:
            # add first item to array
            self.filament_array.append([self.measurement_delay + last_epos,
                                        self.lastFilamentWidthReading])
            if self.is_log == True:
                self.gcode.respond_info("add first item to array.lastFilamentWidthReading:%.3f" % (self.lastFilamentWidthReading))                             


    #W:0310;M:-005;
    def Read_bdwidth(self):
         
        self.bdw_data = ''
        if "usb" == self.port:
            self.usb.write('G01;'.encode())
            self.bdw_data = self.usb.readline().decode('ascii').strip()
        if "i2c" == self.port: 
            self.bdw_data = self.read_register('_measure_data', 20)
        if self.is_log == True:
            self.gcode.respond_info("port:%s, measure data:%s" % (self.port,self.bdw_data))
        if len(self.bdw_data) > 8:
            self.lastFilamentWidthReading = int(self.bdw_data.split(';')[0].split(':')[1]) * 0.00525
            self.lastMotionReading = int(self.bdw_data.split(';')[1].split(':')[1])
            self.actual_total_move = self.actual_total_move + self.lastMotionReading
        else:
            return 1
        
        #if self.is_log == True:
        #   self.gcode.respond_info("width:%.4fmm, Motion:%d" % (self.lastFilamentWidthReading,self.lastMotionReading))    
        return 0
    def extrude_factor_update_event(self, eventtime):
        if self.is_active == False:     
            return eventtime + self.read_interval
            
        if self.Read_bdwidth() == 0:
            # width process Update extrude factor
            pos = self.toolhead.get_position()
            last_epos = pos[3]
            # Update filament array for lastFilamentWidthReading
            self.update_filament_array(last_epos)
            # Check runout
            self.runout_helper.note_filament_present(True)
            #self.runout_helper.note_filament_present(
            #    self.runout_dia_min <= self.lastFilamentWidthReading <= self.runout_dia_max)
            # Does filament exists
           # if self.is_log == True:
            #    self.gcode.respond_info(" width:%.4fmm, pending_position:%f,last_epos:%f" % (self.lastFilamentWidthReading,self.filament_array[0][0],last_epos))
            if self.lastFilamentWidthReading > 0.5:
                if len(self.filament_array) > 0:
                    # Get first position in filament array
                    pending_position = self.filament_array[0][0]
                    if pending_position <= last_epos:
                        # Get first item in filament_array queue
                        item = self.filament_array.pop(0)
                        filament_width = item[1]
                        if ((filament_width <= self.max_diameter)
                            and (filament_width >= self.min_diameter)):
                            percentage = round(self.nominal_filament_dia**2
                                               / filament_width**2 * 100,2)
                            self.gcode.run_script("M221 S" + str(percentage))
                            self.gcode.respond_info("M221 S:%.3f  filament_width:%.3f" %  (percentage,filament_width))
                        else:
                            self.gcode.run_script("M221 S100")
            else:
                self.gcode.run_script("M221 S100")
                self.filament_array = []

            # motion process
            if self.lastMotionReading!=0:
                self._update_filament_runout_pos(eventtime)
            else:
                extruder_pos = self._get_extruder_pos(eventtime)
                #self.gcode.respond_info("epos:%0.1f filament_runout_pos:%0.1f,actual_total_move:%d" % (extruder_pos, 
                #                            self.filament_runout_pos,self.actual_total_move))
                # Check for filament runout
                if extruder_pos > self.filament_runout_pos+0.1:
                    self.gcode.respond_info("rounout Emotor:%0.1f filament:%0.1f,motion:%d" % (extruder_pos, 
                                                    self.filament_runout_pos,self.actual_total_move))
                    self.runout_helper.note_filament_present(False)
                    return
                self.runout_helper.note_filament_present(True)
                self._update_filament_runout_pos(eventtime)  
        else:
            return eventtime + 10

        return eventtime + self.read_interval



    def read_register(self, reg_name, read_len):
        # read a single register
        regs = [BDWIDTH_REGS[reg_name]]
        params = self.i2c.i2c_read(regs, read_len)
        return bytearray(params['response'])

    def write_register(self, reg_name, data):
        if type(data) is not list:
            data = [data]
        reg = BDWIDTH_REGS[reg_name]
        data.insert(0, reg)
        self.i2c.i2c_write(data)
        


    def compare_float(self, a, b, precision):
        if abs(a - b) <= precision:
            return True
        return False

    def _update_filament_runout_pos(self, eventtime=None):
        if eventtime is None:
            eventtime = self.reactor.monotonic()
        self.filament_runout_pos = (
                self._get_extruder_pos(eventtime) +
                self.detection_length)
    def _handle_ready(self):
        
        self.toolhead = self.printer.lookup_object('toolhead')
        self.extruder = self.printer.lookup_object(self.extruder_name)
        self.estimated_print_time = (
                self.printer.lookup_object('mcu').estimated_print_time)
        self._update_filament_runout_pos()
        
        self.reactor.update_timer(self.extrude_factor_update_timer,  # width sensor
                                  self.reactor.NOW)        


        

    def _handle_not_printing(self, print_time):

        return

    def _get_extruder_pos(self, eventtime=None):
        if eventtime is None:
            eventtime = self.reactor.monotonic()
        print_time = self.estimated_print_time(eventtime)
        return self.extruder.find_past_position(print_time)

    
            
    def cmd_M407(self, gcmd):
        response = ""
        if self.lastFilamentWidthReading > 0:
            response += ("Filament dia (measured mm): "
                         + str(self.lastFilamentWidthReading))
        else:
            response += "Filament NOT present"
        gcmd.respond_info(response)

    def cmd_ClearFilamentArray(self, gcmd):
        self.filament_array = []
        gcmd.respond_info("Filament width measurements cleared!")
        # Set extrude multiplier to 100%
        self.gcode.run_script_from_command("M221 S100")

    def cmd_M405(self, gcmd):
        response = "Filament width sensor Turned On"
        if self.is_active:
            response = "Filament width sensor is already On"
        else:
            self.is_active = True
            # Start extrude factor update timer
            self.reactor.update_timer(self.extrude_factor_update_timer,
                                      self.reactor.NOW)
        gcmd.respond_info(response)

    def cmd_M406(self, gcmd):
        response = "Filament width sensor Turned Off"
        if not self.is_active:
            response = "Filament width sensor is already Off"
        else:
            self.is_active = False
            # Stop extrude factor update timer
            self.reactor.update_timer(self.extrude_factor_update_timer,
                                      self.reactor.NEVER)
            # Clear filament array
            self.filament_array = []
            # Set extrude multiplier to 100%
            self.gcode.run_script_from_command("M221 S100")
        gcmd.respond_info(response)
        
    def get_status(self, eventtime):
        return {'Diameter': self.diameter,
                'Raw':(self.lastFilamentWidthReading+
                 self.lastFilamentWidthReading2),
                'is_active':self.is_active}
                
    def cmd_log_enable(self, gcmd):
        self.is_log = True
        gcmd.respond_info("Filament width logging Turned On")

    def cmd_log_disable(self, gcmd):
        self.is_log = False
        gcmd.respond_info("Filament width logging Turned Off")

def load_config(config):
    return BDWidthMotionSensor(config)
