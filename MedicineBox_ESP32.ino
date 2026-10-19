/* ============================================================
 *  MedicineBox_ESP32.ino  —  نسخة ESP32 (حسب التوصيل الجديد)
 *  ----------------------------------------------------------
 *  مشروع: علبة الدواء الذكية لمتابعة وتذكير المسنين
 *  الجزء : كود ESP32 (بدلاً من NodeMCU ESP8266)
 *
 *  التوصيل الفعلي على ESP32:
 *    - حساسات IR:   خانة1→GPIO32 ، خانة2→GPIO33 ، خانة3→GPIO25 ، خانة4→GPIO26
 *    - شاشة OLED:   SDA→GPIO21 ، SCL→GPIO22   (I2C عتادي — الأرجل الرسمية)
 *    - 4 ليدات خضراء بطريقة Charlieplexing على 3 أرجل فقط:
 *         GPIO27 (سلكان) ، GPIO14 (أربعة أسلاك) ، GPIO13 (سلكان)
 *         LED1: أنود→27  كاثود→14
 *         LED2: أنود→14  كاثود→27
 *         LED3: أنود→13  كاثود→14
 *         LED4: أنود→14  كاثود→13
 *         (كل ليد مع مقاومة 220Ω على التسلسل)
 *    - البزر: موصول بين GPIO27 و GPIO13  ( + على 27 ، − على 13 )
 *         لا يوجد أي ليد بين 27 و13 مباشرة، لذلك نستطيع تشغيل
 *         البزر بجعل GPIO14 معلّقاً (INPUT) ثم قيادة 27/13.
 *
 *  الوظائف (نفس النسخة السابقة تماماً):
 *    1) الاتصال بـ Wi-Fi.
 *    2) قراءة 4 حساسات IR لكشف رفع العلبة من الخانات.
 *    3) إضاءة ليد الخانة المطلوبة (Charlieplex) حسب أوامر السيرفر.
 *    4) صوت تنبيه عند حلول موعد الدواء، وصوت مختلف (أسرع/أحدّ)
 *       عند التأخر — ولمدة محدودة فقط، ليس بشكل دائم.
 *    5) عرض الحالة على شاشة OLED.
 *    6) إبلاغ السيرفر عند رفع العلبة (تأكيد أخذ الدواء).
 *    7) Polling دوري للسيرفر لمعرفة الخانات النشطة ونوع التنبيه.
 *
 *  الإعداد في Arduino IDE:
 *    - Boards Manager: ثبّتي حزمة "esp32" (by Espressif Systems)
 *      ويُفضَّل إصدار 2.0.6 أو أحدث (لدعم دالة tone).
 *    - اللوحة: Tools → Board → ESP32 Dev Module
 *      (أو اسم لوحتك إن كانت مختلفة مثل DOIT ESP32 DEVKIT V1)
 *    - المكتبات من Library Manager:
 *        ArduinoJson (v6.x أو أحدث)
 *        Adafruit SSD1306  +  Adafruit GFX Library
 *      (WiFi.h و HTTPClient.h تأتيان مع حزمة ESP32 نفسها)
 *
 *  ملاحظات ESP32:
 *    - لا توجد مشكلة إقلاع مع الشاشة هنا: GPIO21/22 أرجل حرة تماماً،
 *      فلا حاجة لفصل أي سلك أثناء رفع الكود (وداعاً لمشكلة D8).
 *    - تجنّبنا أرجل الإقلاع الحساسة (0، 2، 12، 15) والأرجل 34–39
 *      (دخل فقط وبلا Pullup داخلي) — التوزيع الحالي آمن بالكامل.
 * ============================================================ */

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

/* ============================================================
 * إعدادات يجب تعديلها حسب بيئتك
 * ============================================================ */

// ---- معلومات شبكة الواي فاي ----
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// ---- عنوان السيرفر (IP الحاسوب الذي يُشغّل Python) ----
// في PowerShell: ipconfig  ← خذ IPv4 Address
const char* SERVER_HOST   = "192.168.1.121";
const int   SERVER_PORT   = 5000;
const char* API_KEY       = "CHANGE_THIS_TO_A_LONG_RANDOM_SECRET";

// ---- نوع البزر ----
// 1 = بزر Passive (يحتاج تردداً — يتغيّر نغمةً فعلاً)
// 0 = بزر Active  (يصدر صوتاً ثابتاً بمجرد التغذية — يتغيّر الإيقاع فقط)
#define PASSIVE_BUZZER 1

/* ============================================================
 * فترات التوقيت (ميلي ثانية)
 * ============================================================ */
const unsigned long POLL_INTERVAL_MS   = 2000;  // فحص أوامر السيرفر
const unsigned long SENSOR_CHECK_MS    = 60;    // قراءة الحساسات
const unsigned long OLED_UPDATE_MS     = 500;   // تحديث الشاشة
const unsigned long LED_SLICE_US       = 2000;  // زمن عرض كل ليد (Charlieplex)

// مدة الصوت الكلية لكل نوع تنبيه (بعدها يصمت البزر ويبقى الليد مضاءً)
const unsigned long BUZZ_DURATION_DUE_MS  = 20000;  // 20 ثانية عند حلول الموعد
const unsigned long BUZZ_DURATION_LATE_MS = 30000;  // 30 ثانية عند التأخر

// نمط صوت "حان الموعد": نغمة هادئة متقطعة
const unsigned int  DUE_FREQ_HZ   = 1200;
const unsigned long DUE_ON_MS     = 200;
const unsigned long DUE_OFF_MS    = 800;

// نمط صوت "متأخر": نغمة أعلى وأسرع (إلحاح)
const unsigned int  LATE_FREQ_HZ  = 2300;
const unsigned long LATE_ON_MS    = 120;
const unsigned long LATE_OFF_MS   = 160;

/* ============================================================
 * تخطيط الأرجل (Pins) — ESP32
 * ============================================================ */

// حساسات IR: خانة 1..4  (كلها تدعم INPUT_PULLUP)
const int IR_PINS[4] = { 32, 33, 25, 26 };

// أرجل الـ Charlieplexing الثلاثة
const int CP_A = 27;   // سلكان  (+ البزر أيضاً)   ← كانت D3
const int CP_B = 14;   // أربعة أسلاك (الوسطى)     ← كانت D4
const int CP_C = 13;   // سلكان  (− البزر أيضاً)   ← كانت D7

// جدول الليدات: لكل ليد {أنود, كاثود, الرجل المعلّقة}
// حسب توصيلكِ الفعلي:
// LED1: 27→14 | LED2: 14→27 | LED3: 13→14 | LED4: 14→13
const int LED_ANODE[4]   = { CP_A, CP_B, CP_C, CP_B };
const int LED_CATHODE[4] = { CP_B, CP_A, CP_B, CP_C };
const int LED_HIZ[4]     = { CP_C, CP_C, CP_A, CP_A };

// البزر بين 27 و 13 (لا ليد بينهما مباشرة)
const int BUZZ_PLUS  = CP_A;   // GPIO27
const int BUZZ_MINUS = CP_C;   // GPIO13

// الشاشة OLED (I2C عتادي — الأرجل الافتراضية للـ ESP32)
const int OLED_SDA = 21;
const int OLED_SCL = 22;
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_ADDR 0x3C
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
bool oledOk = false;

/* ============================================================
 * متغيرات الحالة
 * ============================================================ */

// حالة كل خانة كما يراها السيرفر
bool active[4]      = { false, false, false, false };  // الليد مضاء؟
char slotMode[4][6] = { "", "", "" , "" };             // "due" أو "late"
int  slotSeq[4]     = { 0, 0, 0, 0 };                  // رقم تسلسلي لكل تنبيه

// حساسات: آخر قراءة (منطق: LOW = العلبة موجودة، HIGH = رُفعت)
int previousSensor[4] = { LOW, LOW, LOW, LOW };

// جلسة الصوت الحالية (بزر واحد مشترك — نشغّل أعلى أولوية)
struct BuzzSession {
  bool          running    = false;
  bool          isLate     = false;   // نوع الصوت
  unsigned long startedAt  = 0;       // بداية الجلسة
  unsigned long phaseAt    = 0;       // بداية الطور الحالي
  bool          phaseOn    = false;   // هل البزر يصدر صوتاً الآن؟
} buzz;

// مؤقتات عامة
unsigned long lastPollAt   = 0;
unsigned long lastSensorAt = 0;
unsigned long lastOledAt   = 0;
unsigned long lastLedSwapUs = 0;
int  ledCursor = 0;                 // أي ليد يُعرض الآن (تعدد إرسال)
String serverTime = "--:--:--";     // آخر وقت وصل من السيرفر
bool oledBlink = false;             // للوميض على الشاشة أثناء التنبيه

/* ============================================================
 *  أدوات الـ Charlieplexing والبزر
 * ============================================================ */

// تعليق الأرجل الثلاث (كل الليدات مطفأة، البزر حر)
void cpAllRelease() {
  pinMode(CP_A, INPUT);
  pinMode(CP_B, INPUT);
  pinMode(CP_C, INPUT);
}

// إظهار ليد واحد (يُستدعى بسرعة بالتناوب → تراها العين كلها مضاءة)
void cpShowLed(int i) {
  pinMode(LED_HIZ[i], INPUT);                // الرجل الثالثة معلّقة
  pinMode(LED_ANODE[i], OUTPUT);
  pinMode(LED_CATHODE[i], OUTPUT);
  digitalWrite(LED_CATHODE[i], LOW);
  digitalWrite(LED_ANODE[i], HIGH);
}

// دخول وضع البزر: GPIO14 معلّق حتى لا يضيء أي ليد، ثم قيادة 27/13
// ملاحظة مهمة: توليد النغمة يتم عبر نظام LEDC الأصلي في ESP32
// (وليس دالة tone) لأنها الطريقة المضمونة على كل إصدارات حزمة esp32.
const int BUZZ_LEDC_CH = 4;   // قناة LEDC مخصصة للبزر (للحزم القديمة)

void buzzToneStart(unsigned int freq) {
#if PASSIVE_BUZZER
  #if defined(ESP_ARDUINO_VERSION_MAJOR) && (ESP_ARDUINO_VERSION_MAJOR >= 3)
    // حزمة esp32 إصدار 3.x
    ledcAttach(BUZZ_PLUS, freq, 10);
    ledcWriteTone(BUZZ_PLUS, freq);
  #else
    // حزمة esp32 إصدار 2.x
    ledcSetup(BUZZ_LEDC_CH, freq, 10);
    ledcAttachPin(BUZZ_PLUS, BUZZ_LEDC_CH);
    ledcWriteTone(BUZZ_LEDC_CH, freq);
  #endif
#else
  pinMode(BUZZ_PLUS, OUTPUT);
  digitalWrite(BUZZ_PLUS, HIGH);
#endif
}

void buzzToneStop() {
#if PASSIVE_BUZZER
  #if defined(ESP_ARDUINO_VERSION_MAJOR) && (ESP_ARDUINO_VERSION_MAJOR >= 3)
    ledcWriteTone(BUZZ_PLUS, 0);
    ledcDetach(BUZZ_PLUS);
  #else
    ledcWriteTone(BUZZ_LEDC_CH, 0);
    ledcDetachPin(BUZZ_PLUS);
  #endif
#endif
}

void buzzerPhaseOn(bool late) {
  pinMode(CP_B, INPUT);                      // كل الليدات خارج الدارة
  pinMode(BUZZ_MINUS, OUTPUT);
  digitalWrite(BUZZ_MINUS, LOW);
  buzzToneStart(late ? LATE_FREQ_HZ : DUE_FREQ_HZ);
}

// إسكات البزر وإعادة الأرجل لوضعها الحر (تستأنف الليدات عملها)
void buzzerPhaseOff() {
  buzzToneStop();
  cpAllRelease();
}

// بدء جلسة صوت جديدة (due أو late)
void startBuzzSession(bool late) {
  buzz.running   = true;
  buzz.isLate    = late;
  buzz.startedAt = millis();
  buzz.phaseAt   = 0;      // يبدأ الطور فوراً
  buzz.phaseOn   = false;
  Serial.print("[Buzz] بدء صوت ");
  Serial.println(late ? "متأخر (سريع/حاد)" : "الموعد (هادئ)");
}

void stopBuzzSession() {
  if (buzz.running || buzz.phaseOn) {
    buzz.running = false;
    buzz.phaseOn = false;
    buzzerPhaseOff();
    Serial.println("[Buzz] توقف الصوت");
  }
}

// تُستدعى في كل لفّة: تدير أطوار الصوت والمدة الكلية
void updateBuzzer() {
  if (!buzz.running) return;
  unsigned long now = millis();
  unsigned long total = buzz.isLate ? BUZZ_DURATION_LATE_MS
                                    : BUZZ_DURATION_DUE_MS;

  // انتهت المدة الكلية → صمت (الليد يبقى مضاءً من طرف السيرفر)
  if (now - buzz.startedAt >= total) {
    stopBuzzSession();
    return;
  }

  unsigned long onMs  = buzz.isLate ? LATE_ON_MS  : DUE_ON_MS;
  unsigned long offMs = buzz.isLate ? LATE_OFF_MS : DUE_OFF_MS;

  if (buzz.phaseOn) {
    if (now - buzz.phaseAt >= onMs) {
      buzz.phaseOn = false;
      buzz.phaseAt = now;
      buzzerPhaseOff();
    }
  } else {
    if (buzz.phaseAt == 0 || now - buzz.phaseAt >= offMs) {
      buzz.phaseOn = true;
      buzz.phaseAt = now;
      buzzerPhaseOn(buzz.isLate);
    }
  }
}

// تحديث عرض الليدات (يُستدعى باستمرار، ويتوقف مؤقتاً أثناء الصفير)
void updateLeds() {
  if (buzz.phaseOn) return;   // أثناء الصفير الأرجل مشغولة بالبزر

  // اجمع الليدات المطلوب إضاءتها
  int lit[4]; int n = 0;
  for (int i = 0; i < 4; i++) if (active[i]) lit[n++] = i;

  if (n == 0) { cpAllRelease(); return; }

  unsigned long nowUs = micros();
  if (nowUs - lastLedSwapUs < LED_SLICE_US) return;
  lastLedSwapUs = nowUs;

  ledCursor = (ledCursor + 1) % n;
  cpShowLed(lit[ledCursor]);
}

/* ============================================================
 *  setup()
 * ============================================================ */
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println();
  Serial.println("==============================================");
  Serial.println("  Smart Medicine Box v2 — ESP32 Boot");
  Serial.println("==============================================");

  // الحساسات
  for (int i = 0; i < 4; i++) pinMode(IR_PINS[i], INPUT_PULLUP);

  // أرجل الليدات/البزر تبدأ معلّقة
  cpAllRelease();

  // فحص البزر عند الإقلاع: نغمتان قصيرتان (هادئة ثم حادة)
  // إن سمعتِهما فالبزر وتوصيله سليمان 100%
  Serial.println("[Buzz] فحص البزر...");
  buzzerPhaseOn(false); delay(150); buzzerPhaseOff(); delay(120);
  buzzerPhaseOn(true);  delay(150); buzzerPhaseOff();

  // الشاشة (I2C عتادي على 21/22)
  Wire.begin(OLED_SDA, OLED_SCL);
  oledOk = display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR);
  if (oledOk) {
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    display.setCursor(0, 0);
    display.println("Smart Medicine Box");
    display.println("Booting... (ESP32)");
    display.display();
  } else {
    Serial.println("[OLED] لم يتم العثور على الشاشة (تحقق من SDA/SCL "
                   "او العنوان 0x3C)");
  }

  connectWiFi();
}

/* ============================================================
 *  loop()
 * ============================================================ */
void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWiFi();

  unsigned long now = millis();

  // 1) الحساسات
  if (now - lastSensorAt >= SENSOR_CHECK_MS) {
    lastSensorAt = now;
    checkSensors();
  }

  // 2) السيرفر
  if (now - lastPollAt >= POLL_INTERVAL_MS) {
    lastPollAt = now;
    pollServer();
  }

  // 3) الصوت (أطوار + مدة محدودة)
  updateBuzzer();

  // 4) الليدات (Charlieplex متواصل)
  updateLeds();

  // 5) الشاشة
  if (now - lastOledAt >= OLED_UPDATE_MS) {
    lastOledAt = now;
    updateOled();
  }
}

/* ============================================================
 *  connectWiFi()
 * ============================================================ */
void connectWiFi() {
  Serial.print("[Wi-Fi] الاتصال بـ ");
  Serial.println(WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 40) {
    delay(500);
    Serial.print(".");
    tries++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println();
    Serial.print("[Wi-Fi] متصل! IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println();
    Serial.println("[Wi-Fi] فشل — سأحاول لاحقاً.");
  }
}

/* ============================================================
 *  checkSensors() — رفع العلبة = LOW→HIGH
 * ------------------------------------------------------------
 *  - LOW  = العلبة موجودة على الحساس (لم تُؤخذ بعد)
 *  - HIGH = رُفعت العلبة
 *  نُبلِّغ السيرفر عند كل رفع؛ هو وحده يقرّر إن كانت هناك جرعة
 *  مستحقّة ضمن نافذة الانتظار.
 *  إن كان حساسك معكوساً اعكسي الشرط وابدئي previousSensor بـ HIGH.
 * ============================================================ */
void checkSensors() {
  for (int i = 0; i < 4; i++) {
    int reading = digitalRead(IR_PINS[i]);

    if (previousSensor[i] == LOW && reading == HIGH) {
      delay(40);                                  // debounce بسيط
      if (digitalRead(IR_PINS[i]) == HIGH) {
        Serial.print("[Sensor] رُفعت العلبة من الخانة ");
        Serial.println(i + 1);
        reportSensorOpened(i + 1);
        // تغذية راجعة فورية: أطفئ ليد هذه الخانة وأسكت الصوت إن كان
        // هذا التنبيه الوحيد النشط.
        if (active[i]) {
          active[i] = false;
          bool anyLeft = false;
          for (int k = 0; k < 4; k++) if (active[k]) anyLeft = true;
          if (!anyLeft) stopBuzzSession();
        }
      }
    }
    previousSensor[i] = reading;
  }
}

/* ============================================================
 *  reportSensorOpened(slot) — POST /esp/sensor
 * ============================================================ */
void reportSensorOpened(int slot) {
  if (WiFi.status() != WL_CONNECTED) return;

  WiFiClient client;
  HTTPClient http;

  String url = "http://" + String(SERVER_HOST) + ":" +
               String(SERVER_PORT) + "/esp/sensor";

  if (!http.begin(client, url)) {
    Serial.println("[HTTP] begin failed");
    return;
  }

  http.addHeader("Content-Type", "application/json");

  StaticJsonDocument<200> doc;
  doc["key"]   = API_KEY;
  doc["slot"]  = slot;
  doc["event"] = "opened";
  String body;
  serializeJson(doc, body);

  int code = http.POST(body);
  Serial.print("[HTTP] /esp/sensor → ");
  Serial.println(code);
  http.end();
}

/* ============================================================
 *  pollServer() — GET /esp/poll
 * ------------------------------------------------------------
 *  السيرفر يعيد نوع التنبيه لكل خانة ورقماً تسلسلياً:
 *  {
 *    "active_slots": [2],
 *    "alerts": [ {"slot":2, "mode":"due", "seq":3} ],
 *    "time": "18:30:05"
 *  }
 *  mode = "due"  → حان موعد الدواء الآن (صوت هادئ)
 *  mode = "late" → تنبيه مكرر/تأخر (صوت أسرع وأحدّ)
 *  seq  = يزيد مع كل تنبيه جديد → عند تغيّره نعيد تشغيل الصوت
 *         لمدة محدودة، ثم يصمت البزر ويبقى الليد مضاءً.
 * ============================================================ */
void pollServer() {
  if (WiFi.status() != WL_CONNECTED) return;

  HTTPClient http;

  String url = "http://" + String(SERVER_HOST) + ":" +
               String(SERVER_PORT) + "/esp/poll?key=" + String(API_KEY);

  if (!http.begin(url)) return;

  int code = http.GET();
  if (code == 200) {
    String payload = http.getString();
    StaticJsonDocument<512> doc;
    DeserializationError err = deserializeJson(doc, payload);
    if (!err) {
      serverTime = String((const char*)(doc["time"] | "--:--:--"));

      bool newActive[4]  = { false, false, false, false };
      char newMode[4][6] = { "", "", "", "" };
      int  newSeq[4]     = { 0, 0, 0, 0 };

      // الشكل الجديد: alerts
      if (doc.containsKey("alerts")) {
        for (JsonVariant v : doc["alerts"].as<JsonArray>()) {
          int s = v["slot"] | 0;
          if (s >= 1 && s <= 4) {
            newActive[s - 1] = true;
            strlcpy(newMode[s - 1], v["mode"] | "due", 6);
            newSeq[s - 1] = v["seq"] | 1;
          }
        }
      } else {
        // توافق مع الشكل القديم: active_slots فقط
        for (JsonVariant v : doc["active_slots"].as<JsonArray>()) {
          int s = v.as<int>();
          if (s >= 1 && s <= 4) {
            newActive[s - 1] = true;
            strlcpy(newMode[s - 1], "due", 6);
            newSeq[s - 1] = 1;
          }
        }
      }

      // طبّقي التغييرات
      bool anyNewAlert = false;
      bool newAlertIsLate = false;

      for (int i = 0; i < 4; i++) {
        // تنبيه جديد؟ (خانة صارت نشطة، أو تكرر التنبيه seq تغيّر)
        if (newActive[i] &&
            (!active[i] || newSeq[i] != slotSeq[i])) {
          anyNewAlert = true;
          if (strcmp(newMode[i], "late") == 0) newAlertIsLate = true;
          Serial.print("[State] الخانة ");
          Serial.print(i + 1);
          Serial.print(" → تنبيه ");
          Serial.print(newMode[i]);
          Serial.print(" (seq=");
          Serial.print(newSeq[i]);
          Serial.println(")");
        }
        if (active[i] && !newActive[i]) {
          Serial.print("[State] الخانة ");
          Serial.print(i + 1);
          Serial.println(" → OFF");
        }
        active[i]  = newActive[i];
        slotSeq[i] = newSeq[i];
        strlcpy(slotMode[i], newMode[i], 6);
      }

      // إن وصل تنبيه جديد → شغّلي جلسة صوت (المتأخر له الأولوية)
      if (anyNewAlert) startBuzzSession(newAlertIsLate);

      // إن لم يبقَ أي خانة نشطة → أوقفي أي صوت
      bool anyLeft = false;
      for (int i = 0; i < 4; i++) if (active[i]) anyLeft = true;
      if (!anyLeft) stopBuzzSession();

    } else {
      Serial.print("[JSON] error: ");
      Serial.println(err.c_str());
    }
  } else {
    Serial.print("[Poll] HTTP ");
    Serial.println(code);
  }

  http.end();
}

/* ============================================================
 *  updateOled() — عرض الحالة على الشاشة
 * ------------------------------------------------------------
 *  الوضع العادي (لا تنبيهات): حالة الواي فاي + وقت السيرفر + ALL OK
 *  وضع التنبيه (وقت الدواء): عرض كبير وواضح لرقم الخانة:
 *    - إطار حول الشاشة + رسالة أعلى الشاشة
 *    - رقم الخانة بخط ضخم في المنتصف
 *    - عند التأخر (late): الشاشة كلها تومض بعكس الألوان للفت النظر
 *    - عند أكثر من خانة: تُعرض الأرقام كبيرة جنباً إلى جنب
 *  ملاحظة: شاشة SSD1306 أحادية اللون (أبيض/أسود فقط) — لا يمكنها
 *  إظهار ألوان حقيقية، لذلك استعضنا عنها بالحجم الكبير + الوميض
 *  + عكس الألوان، وهي أقوى وسائل لفت الانتباه المتاحة فيها.
 * ============================================================ */
void updateOled() {
  if (!oledOk) return;
  oledBlink = !oledBlink;   // يتبدل مع كل تحديث (كل نصف ثانية)

  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);

  // اجمع الخانات النشطة وحدد إن كان بينها تنبيه تأخر
  int lit[4]; int n = 0; bool anyLate = false;
  for (int i = 0; i < 4; i++) {
    if (!active[i]) continue;
    lit[n++] = i;
    if (strcmp(slotMode[i], "late") == 0) anyLate = true;
  }

  /* ---------- الوضع العادي: لا يوجد أي تنبيه ---------- */
  if (n == 0) {
    display.invertDisplay(false);
    display.setTextSize(1);
    display.setCursor(0, 0);
    display.print("MedBox ");
    display.print(WiFi.status() == WL_CONNECTED ? "WiFi:OK" : "WiFi:--");
    display.setCursor(0, 12);
    display.print("Time ");
    display.print(serverTime);
    display.setTextSize(2);
    display.setCursor(28, 38);
    display.print("ALL OK");
    display.display();
    return;
  }

  /* ---------- وضع التنبيه: وقت الدواء ---------- */
  // عند التأخر: الشاشة كلها تومض بعكس الألوان (أقوى تنبيه بصري)
  display.invertDisplay(anyLate && oledBlink);

  // إطار جميل حول الشاشة
  display.drawRoundRect(0, 0, 128, 64, 6, SSD1306_WHITE);
  display.drawRoundRect(2, 2, 124, 60, 5, SSD1306_WHITE);

  // الرسالة أعلى الشاشة (تومض في وضع الموعد العادي لجذب الانتباه)
  display.setTextSize(1);
  const char* msg = anyLate ? "!! LATE-TAKE NOW !!" : "TIME FOR MEDICINE";
  if (anyLate || oledBlink) {
    int msgW = strlen(msg) * 6;
    display.setCursor((128 - msgW) / 2, 8);
    display.print(msg);
  }

  if (n == 1) {
    // خانة واحدة: كلمة SLOT + الرقم بخط ضخم جداً
    display.setTextSize(2);
    display.setCursor(14, 32);
    display.print("SLOT");
    display.setTextSize(6);               // رقم بحجم 36×48 بكسل
    display.setCursor(74, 15);
    display.print(lit[0] + 1);
  } else {
    // عدة خانات: الأرقام كبيرة جنباً إلى جنب
    display.setTextSize(1);
    display.setCursor((128 - 5 * 6) / 2, 20);
    display.print("SLOTS");
    display.setTextSize(4);               // كل رقم 24×32 بكسل
    int totalW = n * 24 + (n - 1) * 8;
    int x = (128 - totalW) / 2;
    for (int k = 0; k < n; k++) {
      display.setCursor(x, 30);
      display.print(lit[k] + 1);
      x += 32;
    }
  }

  display.display();
}
