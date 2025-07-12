import logging
import math
import statistics
import serial
import os


from . import bus
from . import filament_switch_sensor


BDWIDTH_CHIP_ADDR = 3
BDWIDTH_I2C_SPEED = 100000
BDWIDTH_REGS = {
     '_version' : 0x6,
     '_measure_data' : 0x16

}

class BDWidthMotionSensor:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.port = config.get("port")

        # if config.get("resistance1", None) is None:
        if "i2c" in self.port:  
            self.i2c = bus.MCU_I2C_from_config(config, BDWIDTH_CHIP_ADDR, BDWIDTH_I2C_SPEED)
        elif "usb" in self.port:
            self.usb_port = config.get("serial")
            baudrate = 500000
            self.usb = serial.Serial(self.usb_port, baudrate,timeout=1)
        self.gcode = self.printer.lookup_object('gcode')
        self.extruder_name = config.get('extruder')
        self.check_on_print_start = config.getboolean(
            "check_on_print_start", False)
        try: 
            self.runout_helper = filament_switch_sensor.RunoutHelper(config)
        except Exception as e:
            self.runout_helper = filament_switch_sensor.RunoutHelper(config,self)
        self.get_status = self.runout_helper.get_status
        self.extruder = None
        self.estimated_print_time = None
        # Initialise internal state
        self.filament_runout_pos = None
        self.filament_present = True
        
        self.nominal_filament_dia = config.getfloat(
            'default_nominal_filament_diameter', above=1.0)
        self.sensor_to_nozzle_length = config.getfloat('sensor_to_nozzle_length', above=0.)
   
        self.runout_delay_length = config.getfloat('runout_delay_length', 7., above=0.)

        self.flowrate_adjust_length = config.getfloat('flowrate_adjust_length', 5., above=1.)

        self.is_active =config.get('enable')    
        self.min_diameter=config.getfloat('min_diameter', 1.0)
        self.linear_motion=config.getfloat('motion_linear_coefficient', 42.8)
        self.max_diameter=config.getfloat('max_diameter', 1.9)
        self.sample_time=config.getfloat('sample_time', 1.0) # in second
        self.is_log =config.getboolean('logging', False)
        self.is_debug =config.getboolean('debug_info', False)
        self.raw_width = 0
        self.lastFilamentWidthReading = 0
        self.lastMotionReading = 0
        self.actual_total_move = 0
        self.filament_array = []
        if self.is_log == True:
        
           # logging.basicConfig(handlers=[logging.FileHandler(filename=self.get_log_path()+"bdwidth.log", 
            #                                     encoding='utf-8', mode='a+')],
           #         format="%(asctime)s  %(message)s", 
           #         datefmt="%F %A %T", 
           #         level=logging.INFO)
            self.logerb=self.get_logger(self.get_log_path()+"bdwidth.log.csv")
                    

        # Register commands and event handlers
        self.printer.register_event_handler('klippy:ready',
                                            self._handle_ready)
        self.printer.register_event_handler("klippy:shutdown", self._shutdown)
        
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
        self.gcode.register_command('ENABLE_FILAMENT_WIDTH_INFO',
                                    self.cmd_info_enable)
        self.gcode.register_command('DISABLE_FILAMENT_WIDTH_INFO',
                                    self.cmd_info_disable)
    
    def get_logger(self,name):
        logger = logging.getLogger("2")
        fh = logging.FileHandler(name, mode='a+', encoding='utf-8')
        ch = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s,%(message)s',"%m/%d %H:%M:%S")
        logger.setLevel(logging.DEBUG)
        fh.setFormatter(formatter)
        ch.setFormatter(formatter)
        logger.addHandler(fh)
        logger.addHandler(ch)
        return logger


    def get_log_path(self):
        #result=subprocess.run(["ps", "-ef"], check=True, text=True, capture_output=True)
        os.system("ps -ef > /tmp/logd")
        with open("/tmp/logd", "r") as f:
            result = f.read()
            result=str(result).split(" ")
            for c_path in result:
                if '/klippy.log' in c_path:
               # folders['logs'] =  os.path.dirname(c_path) + '/'     
                    return os.path.dirname(c_path) + '/'
        return '/tmp/'

    def log_file(self,mes_str):
        if self.is_log == True:
            self.logerb.info(mes_str)

    
    def update_filament_array(self, last_epos):
        # Fill array
        if len(self.filament_array) > 0:
            # Get last reading position in array & calculate next
            # reading position
            next_reading_position = (self.filament_array[-1][0]
                                     + self.flowrate_adjust_length)
            if next_reading_position <= (last_epos + self.sensor_to_nozzle_length):
                self.filament_array.append([last_epos + self.sensor_to_nozzle_length,
                                            self.lastFilamentWidthReading])
                if self.is_debug == True:
                    self.gcode.respond_info("Width:%.3f" % (self.lastFilamentWidthReading))                             
        else:
            # add first item to array
            self.filament_array.append([self.sensor_to_nozzle_length + last_epos,
                                        self.lastFilamentWidthReading])
            #if self.is_debug == True:
             #   self.gcode.respond_info("add first item to array.lastFilamentWidthReading:%.3f" % (self.lastFilamentWidthReading))                             

    def Read_bdwidth(self):
        self.bdw_data = ''
         
        buffer = bytearray()
        if "usb" == self.port:
            if self.usb.is_open:
                self.usb.write('\n'.encode())
                self.usb.timeout = 0.01
                data = self.usb.read(5)
                if data:
                    for byte in data:
                        buffer.append(byte)
        elif "i2c" == self.port: 
            buffer = self.read_register('_measure_data', 5)
        if len(buffer) >= 5 and b'\x0a' in buffer:
            self.raw_width = ((buffer[1] << 8) + buffer[0])&0xffff
            self.lastMotionReading = ((buffer[3] << 8) + buffer[2])&0xffff
            if self.lastMotionReading>32767 :
                self.lastMotionReading = self.lastMotionReading - 65535
            self.lastMotionReading = -self.lastMotionReading # change the default dir
            
            self.lastFilamentWidthReading = self.raw_width*0.00525
            self.actual_total_move = self.actual_total_move + self.lastMotionReading
            if self.lastMotionReading !=0:
                self.log_file(str(round(self.lastFilamentWidthReading,3))+'mm,'+str(round(self.actual_total_move/self.linear_motion,1))+'mm,'+str(self.actual_total_move))
        else:
            for i in buffer:
                self.gcode.respond_info("%d"%i)
            self.gcode.respond_info("bdwidth sensor read data error")
            return False
        #if self.is_debug == True:
        #    self.gcode.respond_info("bdwidth, port:%s, width:%.3f mm (%d),motion:%d" % (self.port,self.lastFilamentWidthReading,
         #                                        self.raw_width,self.lastMotionReading))          
        return True


    def width_process(self,eventtime,last_epos):
    # width process Update extrude factor      
        # Check runout
        try:
            self.runout_helper.note_filament_present(eventtime, True)
        except Exception as e:
            self.runout_helper.note_filament_present(True)
            pass
        
        # Does filament exists
       # if self.is_debug == True:
        #    self.gcode.respond_info(" width:%.4fmm, pending_position:%f,last_epos:%f" % (self.lastFilamentWidthReading,self.filament_array[0][0],last_epos))
        if self.lastFilamentWidthReading >= self.min_diameter and self.lastFilamentWidthReading <= self.max_diameter:
            self.filament_present = True
            try:
                self.runout_helper.note_filament_present(eventtime, True)
            except Exception as e:
                self.runout_helper.note_filament_present(True)
                pass
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
                        if self.is_debug == True:
                            self.gcode.respond_info("M221 S:%.3f ; width:%.3f" %  (percentage,filament_width))
                    else:
                        self.gcode.run_script("M221 S100")
        else:
            if self.filament_present == True:
                self.gcode.respond_info("filament width is out of range: %0.3fmm [%0.3f,%0.3f]!!!"%(self.lastFilamentWidthReading,
                                                                       self.min_diameter,self.max_diameter))
                self.filament_present = False                                                       
            #self.runout_helper.note_filament_present(eventtime, False)
            try:
                self.runout_helper.note_filament_present(eventtime, False)
            except Exception as e:
                self.runout_helper.note_filament_present(False)
                pass
            
            self.gcode.run_script("M221 S100")
            self.filament_array = []

    def motion_process(self,eventtime):
         # motion process
        if self.lastMotionReading!=0:
         #   self.gcode.respond_info("port:%s, width:%.3f mm (%d),motion:%d" % (self.port,self.lastFilamentWidthReading,
         #                                    self.raw_width,self.lastMotionReading))
            self._update_filament_runout_pos(eventtime)
        else:
            
            extruder_pos = self._get_extruder_pos(eventtime)
            #self.gcode.respond_info("epos:%0.1f filament_runout_pos:%0.1f,actual_total_move:%d" % (extruder_pos, 
            #                            self.filament_runout_pos,self.actual_total_move))
            # Check for filament runout
            if extruder_pos > (self.filament_runout_pos-5):
                self.gcode.respond_info("Rounout: because extruder_postion:%0.1f > filament_runout_pos:%0.1f, (actual_total_move:%d)" % (extruder_pos, 
                                            self.filament_runout_pos,self.actual_total_move))
                self.gcode.respond_info("If the trigger is incorrect, you can increase the runout_delay_length or check the flow rate in the gcode file")
               # self.runout_helper.note_filament_present(eventtime, False)
                try:
                    self.runout_helper.note_filament_present(eventtime, False)
                except Exception as e:
                    self.runout_helper.note_filament_present(False)
                    pass
                self._update_filament_runout_pos(eventtime) 
            
          #  self._update_filament_runout_pos(eventtime)  

    
    def extrude_factor_update_event(self, eventtime):
        if 'disable' in self.is_active:     
            return eventtime + self.sample_time
            
        if self.Read_bdwidth() == True:
            last_epos = self.toolhead.get_position()[3]
            # Update filament array for lastFilamentWidthReading
            self.update_filament_array(last_epos)
            if 'width' in self.is_active or 'all' in self.is_active:
                self.width_process(eventtime,last_epos)
            if 'motion' in self.is_active or 'all' in self.is_active:    
                self.motion_process(eventtime) 
           
        else:
            return eventtime + 10

        return eventtime + self.sample_time



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
                self.runout_delay_length)
    def _handle_ready(self):
        
        self.toolhead = self.printer.lookup_object('toolhead')
        self.extruder = self.printer.lookup_object(self.extruder_name)
        self.estimated_print_time = (
                self.printer.lookup_object('mcu').estimated_print_time)
        self._update_filament_runout_pos()
        
        self.reactor.update_timer(self.extrude_factor_update_timer,  # width sensor
                                  self.reactor.NOW)        

    def _shutdown(self):
        self.reactor.update_timer(self.extrude_factor_update_timer,  
                                  self.reactor.NEVER)      
    def _handle_not_printing(self, print_time):

        return

    def _get_extruder_pos(self, eventtime=None):
        if eventtime is None:
            eventtime = self.reactor.monotonic()
        print_time = self.estimated_print_time(eventtime)
        return self.extruder.find_past_position(print_time)

    
            
    def cmd_M407(self, gcmd):
        response = ""
        if "usb" == self.port:
            self.usb.write('G00;'.encode())
            response += self.usb.readline().decode('ascii').strip()
        elif "i2c" == self.port: 
            response += self.read_register('_version', 15).decode('utf-8')
           
        
        if self.lastFilamentWidthReading > 0:
            response += (" Filament dia (measured mm): "
                         + str(self.lastFilamentWidthReading)
                         +" Motion:" + str(self.lastMotionReading))
        else:
            response += " Filament NOT present"
        gcmd.respond_info(response+":"+ self.is_active )

    def cmd_ClearFilamentArray(self, gcmd):
        self.filament_array = []
        gcmd.respond_info("Filament width measurements cleared!")
        # Set extrude multiplier to 100%
        self.gcode.run_script_from_command("M221 S100")

    def cmd_M405(self, gcmd):
       # cmd_bd = gcmd.get('enable', None)
      #  if cmd_bd is not None:
      #      self.is_active = cmd_bd
        self.is_active = 'all'
        response = "bdwidth sensor status:" + self.is_active
        self.reactor.update_timer(self.extrude_factor_update_timer,  # width sensor
                                  self.reactor.NOW)   
        gcmd.respond_info(response)


    def cmd_M406(self, gcmd):
        response = "Filament width sensor Turned Off"
        self.is_active = 'disable'
        # Stop extrude factor update timer
        self.reactor.update_timer(self.extrude_factor_update_timer,
                                  self.reactor.NEVER)
        # Clear filament array
        self.filament_array = []
        # Set extrude multiplier to 100%
        self.gcode.run_script_from_command("M221 S100")
        gcmd.respond_info(response)
        
    def sensor_get_status(self, eventtime):
        return {
            "runout_distance": float(self.runout_helper.runout_distance),
            "runout_elapsed": float(self.runout_helper.runout_elapsed),
            "check_on_print_start": bool(self.check_on_print_start),
        }      
        
    def get_status(self, eventtime):
        return {'Diameter': self.self.lastFilamentWidthReading,
                'Raw':self.raw_width,
                'Motion':self.lastMotionReading,
                'active':self.is_active}
                
    def cmd_info_enable(self, gcmd):
        self.is_debug = True
        gcmd.respond_info("Filament width debug inforamtion Turned On")

    def cmd_info_disable(self, gcmd):
        self.is_debug = False
        gcmd.respond_info("Filament width debug inforamtion Turned Off")
    def cmd_bdwidth_screen_off(self, gcmd):
        buffer = bytearray()
        if "usb" == self.port:
            if self.usb.is_open:
                self.usb.write('\n'.encode())
                self.usb.timeout = 0.01
                data = self.usb.read(5)
                if data:
                    for byte in data:
                        buffer.append(byte)
        elif "i2c" == self.port: 
            buffer = self.read_register('_measure_data', 5)

    def cmd_bdwidth_screen_on(self, gcmd):
        response = ""
        if "usb" == self.port:
            self.usb.write('G00;'.encode())
            response += self.usb.readline().decode('ascii').strip()
        elif "i2c" == self.port: 
            response += self.read_register('_version', 15).decode('utf-8')

def load_config(config):
    return BDWidthMotionSensor(config)
