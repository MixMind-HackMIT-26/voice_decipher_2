/* MixMind on the Arduino UNO Q -- the microcontroller (STM32) half.
 *
 * The Raspberry Pi sends one text line at a time over a 3-wire UART:
 *     Pi GPIO14 (TX, pin 8)  ->  UNO Q D0 (RX)
 *     Pi GPIO15 (RX, pin 10) <-  UNO Q D1 (TX)
 *     Pi GND    (pin 6)      --  UNO Q GND
 * Both sides are 3.3 V logic: no level shifter. That is Serial1 here -- on
 * the UNO Q, USB-C belongs to the Linux side and plain Serial goes to the
 * App Lab console, so neither can carry this link.
 *
 *   P<n> <ms>   run pump n (1-6) for ms      -> DONE (blocks for ms)
 *   M <ms>      run the stirrer (channel 7)  -> DONE (blocks for ms)
 *   X           every channel off, now       -> DONE
 *   ?           version                      -> MIXMIND v3 UNOQ
 * Anything else, or ms outside 0-30000       -> ERR, nothing moves.
 *
 * Every command is also echoed to the App Lab console, and the onboard LED
 * is lit while a channel runs -- so you can watch it work with no relays.
 */

const long BAUD   = 115200;
const int  CH[7]  = {2, 3, 4, 5, 6, 7, 8};   // pumps 1-6 on D2-D7, stirrer on D8
const bool ON     = LOW;     // cheap relay boards are active LOW
const bool OFF    = HIGH;    // swap these two if yours is backwards
const long MAX_MS = 30000;   // a units bug must not empty a bottle

void allOff() {
  for (int i = 0; i < 7; i++) digitalWrite(CH[i], OFF);
  digitalWrite(LED_BUILTIN, HIGH);          // UNO Q onboard LED: HIGH is off
}

void setup() {
  // digitalWrite BEFORE pinMode: no pump twitches on while the board boots.
  for (int i = 0; i < 7; i++) { digitalWrite(CH[i], OFF); pinMode(CH[i], OUTPUT); }
  pinMode(LED_BUILTIN, OUTPUT);
  allOff();
  Serial.begin(115200);                     // App Lab console, for watching
  Serial1.begin(BAUD);                      // the Pi
  Serial1.println("READY");
  Serial.println("MixMind UNO Q ready on Serial1 @ 115200");
}

void reply(const char *r, const String &cmd) {
  Serial1.println(r);
  Serial.print(cmd); Serial.print("  ->  "); Serial.println(r);
}

void fire(int i, long ms, const String &cmd) {
  if (ms < 0 || ms > MAX_MS) { reply("ERR", cmd); return; }
  digitalWrite(CH[i], ON);  digitalWrite(LED_BUILTIN, LOW);
  delay(ms);
  digitalWrite(CH[i], OFF); digitalWrite(LED_BUILTIN, HIGH);
  reply("DONE", cmd);
}

void loop() {
  if (!Serial1.available()) return;
  String c = Serial1.readStringUntil('\n');
  c.trim();
  if (c.length() == 0) return;

  char k = c.charAt(0);
  int sp = c.indexOf(' ');

  if (k == '?') {
    reply("MIXMIND v3 UNOQ", c);
  } else if (k == 'X') {
    allOff();
    reply("DONE", c);
  } else if (k == 'P') {                    // P3 2400 -> pump 3 for 2400 ms
    int ch = c.substring(1, sp < 0 ? c.length() : sp).toInt() - 1;
    if (sp < 0 || ch < 0 || ch > 5) { reply("ERR", c); return; }
    fire(ch, c.substring(sp + 1).toInt(), c);
  } else if (k == 'M') {                    // M 6000 -> stir six seconds
    if (sp < 0) { reply("ERR", c); return; }
    fire(6, c.substring(sp + 1).toInt(), c);
  } else {
    reply("ERR", c);
  }
}
