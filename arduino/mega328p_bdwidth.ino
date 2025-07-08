// Ardunio Nano, mega328p
// read data from bdwith sensor via i2c port.
// software i2c, so we can use any 2 free gpio for the SDA and SCL.

#include <Arduino.h>
#include <U8g2lib.h>
#ifdef U8X8_HAVE_HW_SPI
#include <SPI.h>
#endif
#ifdef U8X8_HAVE_HW_I2C
#include <Wire.h>
#endif

#define SDA_PIN 11
#define SCL_PIN 10

#define sensor_addr 3
#define version_addr 0x06
#define width_addr 0x16

char tmp_1[50];

U8G2_SSD1306_128X32_UNIVISION_F_SW_I2C u8g2(U8G2_R0, /* clock=*/ SCL, /* data=*/ SDA, /* reset=*/ U8X8_PIN_NONE);  


void i2c_init() {
  pinMode(SDA_PIN, INPUT_PULLUP);
 // pinMode(SCL_PIN, INPUT_PULLUP);
  pinMode(SCL_PIN, OUTPUT);
  //digitalWrite(SDA_PIN, HIGH);
  //digitalWrite(SCL_PIN, HIGH);
}

void i2c_start() {
  pinMode(SDA_PIN, INPUT_PULLUP);
  digitalWrite(SCL_PIN, HIGH);
  delayMicroseconds(50);

  pinMode(SDA_PIN, OUTPUT); 
  digitalWrite(SDA_PIN, LOW);
  delayMicroseconds(5);
  digitalWrite(SCL_PIN, LOW);
  delayMicroseconds(5);
}

void i2c_stop() {
  pinMode(SDA_PIN, OUTPUT); 
  digitalWrite(SDA_PIN, LOW);
  delayMicroseconds(5);
  digitalWrite(SCL_PIN, HIGH);
  delayMicroseconds(5);
  //digitalWrite(SDA_PIN, HIGH);
  pinMode(SDA_PIN, INPUT_PULLUP);
  delayMicroseconds(5);
}

bool i2c_write(byte data) {
  for (int i = 7; i >= 0; i--) {
   // digitalWrite(SDA_PIN, (data & (1 << i)) ? HIGH : LOW);
    if(data & (1 << i))
        pinMode(SDA_PIN, INPUT_PULLUP);
    else{
        pinMode(SDA_PIN, OUTPUT);
        digitalWrite(SDA_PIN, LOW); 
    }  
    delayMicroseconds(5);
    digitalWrite(SCL_PIN, HIGH);
    delayMicroseconds(5);
    digitalWrite(SCL_PIN, LOW);
    delayMicroseconds(5);
  }
  
  // check ACK
  pinMode(SDA_PIN, INPUT_PULLUP);
  digitalWrite(SCL_PIN, HIGH);
  delayMicroseconds(5);
  bool ack = digitalRead(SDA_PIN) == LOW;
  digitalWrite(SCL_PIN, LOW);
  //pinMode(SDA_PIN, OUTPUT);
  pinMode(SDA_PIN, INPUT_PULLUP);
  return ack;
}

byte i2c_read(bool ack) {
  byte data = 0;
  pinMode(SDA_PIN, INPUT_PULLUP);
  delayMicroseconds(5);
  for (int i = 7; i >= 0; i--) {
    digitalWrite(SCL_PIN, HIGH);
    delayMicroseconds(5);
    if (digitalRead(SDA_PIN)) data |= (1 << i);
    digitalWrite(SCL_PIN, LOW);
    delayMicroseconds(5);
  }
  
  // send ACK/NACK
  
  //digitalWrite(SDA_PIN, ack ? LOW : HIGH);
   if(!ack)
        pinMode(SDA_PIN, INPUT_PULLUP);
    else{
        pinMode(SDA_PIN, OUTPUT);
        digitalWrite(SDA_PIN, LOW); 
    } 

  digitalWrite(SCL_PIN, HIGH);
  delayMicroseconds(5);
  digitalWrite(SCL_PIN, LOW);
  pinMode(SDA_PIN, INPUT_PULLUP);
  
  return data;
}

byte readI2CRegister(byte deviceAddr, byte regAddr,uint8_t *buf, uint8_t len) {

  i2c_start();

  i2c_write(deviceAddr<<1); // write mode
  i2c_write(regAddr);
  i2c_start();
  i2c_write((deviceAddr<<1)|1); // read mode
  uint8_t i=0;
  for(i=0; i<len-1; i++) {
    buf[i] = i2c_read(true); // send ACK
  }
  buf[i] = i2c_read(false);  // don't send ACK for the last byte.

  i2c_stop();
  return 0;
}
void setup(void) {
  u8g2.begin();
  u8g2.clearBuffer();          // clear the internal memory
  i2c_init();
}

void loop(void) {
  uint8_t data[20];     
  u8g2.clearBuffer(); 
  u8g2.setFont(u8g2_font_ncenB08_tr); // choose a suitable font

  
  // read bdwidth version
  readI2CRegister(sensor_addr, version_addr,data,15);
  sprintf(tmp_1,"%s\n",data);
  u8g2.drawStr(0,10,tmp_1);
  // read width and motion data
  readI2CRegister(sensor_addr, width_addr,data,4);
 unsigned long width = ((data[1] << 8) + data[0])&0xffff;
  int motion = ((data[3] << 8) + data[2])&0xffff;
  //sprintf(tmp_1,"W:%d,M:%d\n",width*525,motion);
  width = width*525;
	//sprintf(tmp_1,"W: %d.%d%d%d,M:%d",width/100000,(width%100000)/10000,(width%10000)/1000,(width%1000)/100,motion);
  sprintf(tmp_1,"W: %d.",width/100000 );
  sprintf(tmp_1+strlen(tmp_1),"%d",(width%100000)/10000);
  sprintf(tmp_1+strlen(tmp_1),"%d",(width%10000)/1000);
  sprintf(tmp_1+strlen(tmp_1),"%d mm",(width%1000)/100);
  sprintf(tmp_1+strlen(tmp_1),"  M: %d",motion);
  u8g2.drawStr(0,30,tmp_1);
    
  
  delay(1000);  
  u8g2.sendBuffer();


}

