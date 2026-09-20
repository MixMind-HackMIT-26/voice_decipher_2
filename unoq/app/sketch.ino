/* MixMind on the Arduino UNO Q -- the microcontroller (STM32) half.
 *
 * THIS is what runs on the machine. The Pi does not talk to this sketch
 * directly: it sends HTTP to the UNO Q's Linux side (app/main.py), which
 * calls in here over the App Lab Bridge. unoq/sketch-uart-legacy is the
 * earlier 3-wire UART plan, kept only so nobody re-tries it.
 *
 * Provided over the Bridge:
 *   pour(ch, ms)              run pump ch (1-6) for ms   -> ms, or -1
 *   pourTo(ch, dg, maxMs)     run pump ch until the cup gains dg tenths of
 *                             a gram, or maxMs elapses   -> dg delivered
 *                                                           -2 = no scale
 *   stop()                    every channel off, now     -> 0
 *   lcRaw()                   averaged raw counts        (calibration)
 *   lcTare()                  this is now zero           -> 0
 *   lcScale(cpdg)             counts per tenth of a gram -> cpdg
 *   lcWeigh()                 tenths of a gram on the scale
 *
 * WIRING
 *   Relays   pumps 1-6 on D2-D7, active LOW. OFF is INPUT (high impedance),
 *            never HIGH: this board drives 3.3 V into opto-LED anodes fed
 *            from 5 V, and 3.3 V "off" leaves ~1.7 V across the LED, which
 *            holds the relay half on. Floating the pin is the fix.
 *   HX711    VCC -> 3.3V   *** NOT 5V ***   DT is an output that follows
 *            VCC, and this board's pins are 3.3 V. On 5 V you are putting
 *            5 V into a 3.3 V input.
 *            GND -> GND     DT -> D9     SCK -> D10
 *   Cell     the four thin wires into the HX711's screw terminals:
 *            red E+   black E-   white A-   green A+
 *            (swapped white/green just makes the reading go negative,
 *             which lcScale handles -- it is not damage.)
 */
#include <Arduino_RouterBridge.h>

const int CH[6]     = {2, 3, 4, 5, 6, 7};   // pumps 1-6
const int PIN_DT    = 9;
const int PIN_SCK   = 10;
const long MAX_MS   = 30000;   // a units bug must not empty a bottle
const long MAX_DG   = 800;     // 80 g: more than any single ingredient
const long RUNAWAY_DG = 150;   // cup lifted mid-pour: 15 g lighter, stop
const int  SETTLE_MS  = 250;   // let the last drops land before measuring

// A real reading is 24-bit signed, so it can never be this. Returning 0 for
// "the chip did not answer" was indistinguishable from a chip answering zero,
// which is exactly what an empty tared scale reads.
const long LC_NONE = 2147483647L;

long lcOffset = 0;
long lcCountsPerDg = 0;        // 0 = not calibrated -> closed loop refused

/* ---------------------------------------------------------- the relays */
void chOn (int p) { pinMode(p, OUTPUT); digitalWrite(p, LOW); }
void chOff(int p) { pinMode(p, INPUT); }
void allOff()     { for (int i = 0; i < 6; i++) chOff(CH[i]); }

/* ----------------------------------------------------------- the HX711 */
bool lcReady() { return digitalRead(PIN_DT) == LOW; }

bool lcWait(unsigned long ms) {
  unsigned long t0 = millis();
  while (!lcReady()) {
    if (millis() - t0 > ms) return false;
    delay(1);
  }
  return true;
}

// One 24-bit two's-complement sample, plus the 25th pulse that selects
// channel A at gain 128 for the next conversion.
long lcOnce() {
  long v = 0;
  for (int i = 0; i < 24; i++) {
    digitalWrite(PIN_SCK, HIGH);
    delayMicroseconds(1);
    v = (v << 1) | (digitalRead(PIN_DT) ? 1 : 0);
    digitalWrite(PIN_SCK, LOW);
    delayMicroseconds(1);
  }
  digitalWrite(PIN_SCK, HIGH); delayMicroseconds(1);
  digitalWrite(PIN_SCK, LOW);  delayMicroseconds(1);
  if (v & 0x800000L) v |= ~0xFFFFFFL;      // sign-extend 24 -> 32 bits
  return v;
}

// n samples averaged. Returns false if the chip never says it is ready,
// which is what an unplugged or unpowered HX711 looks like.
bool lcRead(int n, long *out) {
  long sum = 0;
  for (int i = 0; i < n; i++) {
    if (!lcWait(300)) return false;
    sum += lcOnce();
  }
  *out = sum / n;
  return true;
}

long lcDg() {                              // tenths of a gram, 0 if no scale
  long raw;
  if (lcCountsPerDg == 0 || !lcRead(1, &raw)) return 0;
  return (raw - lcOffset) / lcCountsPerDg;
}

/* ------------------------------------------------------ Bridge surface */
int lcRaw() {
  long raw;
  if (!lcRead(8, &raw)) return (int)LC_NONE;
  return (int)raw;
}

int lcTare() {
  long raw;
  if (!lcRead(12, &raw)) return -1;
  lcOffset = raw;
  return 0;
}

int lcScale(int cpdg) { lcCountsPerDg = cpdg; return cpdg; }

int lcWeigh() {
  if (lcCountsPerDg == 0) return -1;
  long raw;
  if (!lcRead(4, &raw)) return -1;
  return (int)((raw - lcOffset) / lcCountsPerDg);
}

int pour(int ch, int ms) {
  if (ch < 1 || ch > 6)     return -1;
  if (ms < 0 || ms > MAX_MS) return -1;
  chOn(CH[ch - 1]);
  delay(ms);
  chOff(CH[ch - 1]);
  return ms;
}

/* Pour until the cup gets heavier by dg tenths of a gram.
 *
 * This is the whole point of the scale: the machine stops because the drink
 * arrived, not because a stopwatch said it should have. maxMs is the
 * backstop for a blocked tube or an empty bottle, and a cup that gets
 * LIGHTER mid-pour (someone lifted it) stops immediately.
 *
 * Returns what actually landed, which is rarely exactly dg -- the caller
 * logs the difference and it is the honest number.
 */
int pourTo(int ch, int dg, int maxMs) {
  if (ch < 1 || ch > 6)        return -1;
  if (dg <= 0 || dg > MAX_DG)  return -1;
  if (maxMs <= 0 || maxMs > MAX_MS) return -1;
  if (lcCountsPerDg == 0)      return -2;   // no scale: caller pours on time

  long start;
  if (!lcRead(4, &start)) return -2;
  start = (start - lcOffset) / lcCountsPerDg;
  long target = start + dg;

  unsigned long t0 = millis();
  chOn(CH[ch - 1]);
  while (millis() - t0 < (unsigned long)maxMs) {
    long now = lcDg();
    if (now >= target) break;
    if (now < start - RUNAWAY_DG) break;     // the cup left the scale
  }
  chOff(CH[ch - 1]);

  delay(SETTLE_MS);
  long landed;
  if (!lcRead(4, &landed)) return -2;
  landed = (landed - lcOffset) / lcCountsPerDg;
  return (int)(landed - start);
}

int stopAll() { allOff(); return 0; }

void setup() {
  allOff();
  pinMode(PIN_SCK, OUTPUT);
  digitalWrite(PIN_SCK, LOW);
  pinMode(PIN_DT, INPUT);

  Bridge.begin();
  Monitor.begin(115200);       // App Lab console. NOT Serial on this board.
  Bridge.provide("pour",   pour);
  Bridge.provide("pourTo", pourTo);
  Bridge.provide("stop",   stopAll);
  Bridge.provide("lcRaw",   lcRaw);
  Bridge.provide("lcTare",  lcTare);
  Bridge.provide("lcScale", lcScale);
  Bridge.provide("lcWeigh", lcWeigh);
  Monitor.println("MixMind UNO Q ready: 6 pumps, HX711 on D9/D10");
}

void loop() { Bridge.update(); }
