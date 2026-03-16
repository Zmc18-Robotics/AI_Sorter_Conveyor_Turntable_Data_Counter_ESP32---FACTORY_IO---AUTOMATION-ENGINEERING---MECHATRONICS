/*
 * Conveyor Item Counter - ESP32 Firmware FIXED
 * Wiring:
 * - LCD I2C  : SDA -> GPIO 21, SCL -> GPIO 22
 * - Buzzer   : GPIO 19
 * - Button   : GPIO 16 (INPUT_PULLUP)
 * Library: LiquidCrystal_I2C by Frank de Brabander
 */

#include <Wire.h>
#include <LiquidCrystal_I2C.h>

LiquidCrystal_I2C lcd(0x27, 16, 2);

const int PIN_BUZZER = 19;
const int PIN_BUTTON = 16;

int totalCount      = 0;
bool buttonPressed  = false;
unsigned long lastDebounce = 0;
int lastButtonState = HIGH;

String buf = "";

byte arrowUp[8] = {
  0b00100, 0b01110, 0b11111,
  0b00100, 0b00100, 0b00100,
  0b00100, 0b00000
};

void setup() {
  Serial.begin(115200);
  delay(1000);

  pinMode(PIN_BUZZER, OUTPUT);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  digitalWrite(PIN_BUZZER, LOW);

  Wire.begin(21, 22);
  lcd.init();
  lcd.backlight();
  lcd.createChar(0, arrowUp);

  updateLCD();

  tone(PIN_BUZZER, 1000, 150);
  delay(200);
  noTone(PIN_BUZZER);

  Serial.println("READY");
}

void loop() {
  // Baca serial byte per byte
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      buf.trim();
      if (buf.length() > 0) {
        processCommand(buf);
      }
      buf = "";
    } else if (c != '\r') {
      buf += c;
    }
  }

  // Push button reset
  int reading = digitalRead(PIN_BUTTON);
  if (reading != lastButtonState) lastDebounce = millis();
  if ((millis() - lastDebounce) > 50) {
    if (reading == LOW && !buttonPressed) {
      buttonPressed = true;
      totalCount = 0;
      updateLCD();
      tone(PIN_BUZZER, 800, 100); delay(150);
      tone(PIN_BUZZER, 800, 100); delay(150);
      noTone(PIN_BUZZER);
      Serial.println("RESET:0");
    } else if (reading == HIGH) {
      buttonPressed = false;
    }
  }
  lastButtonState = reading;
}

void processCommand(String cmd) {
  Serial.print("RX: ");
  Serial.println(cmd);

  if (cmd.startsWith("COUNT:")) {
    int newCount = cmd.substring(6).toInt();
    if (newCount > totalCount) {
      totalCount = newCount;
      updateLCD();
      tone(PIN_BUZZER, 1000, 150);
      delay(200);
      noTone(PIN_BUZZER);
    } else if (newCount == 0) {
      totalCount = 0;
      updateLCD();
    }
    Serial.print("OK:");
    Serial.println(totalCount);
  }
}

void updateLCD() {
  lcd.setCursor(0, 0);
  lcd.print("Total barang :  ");
  lcd.setCursor(0, 1);
  String s = String(totalCount);
  lcd.print(s);
  for (int i = s.length(); i < 15; i++) lcd.print(" ");
  lcd.setCursor(15, 1);
  if (totalCount > 0) lcd.write(byte(0));
  else lcd.print(" ");
}
