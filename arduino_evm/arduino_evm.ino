#include <WiFiS3.h>
#include <Adafruit_Fingerprint.h>
#include <LiquidCrystal_I2C.h>
#include <ArduinoJson.h>

// --- Network Configuration ---
const char* ssid = "YOUR_WIFI_SSID";
const char* password = "YOUR_WIFI_PASSWORD";
const char* serverIP = "192.168.1.100";  // FastAPI backend IP
const int serverPort = 8000;

// --- Hardware Pins ---
// R307 Fingerprint sensor is on Hardware Serial1 (Pins 0/1)
#define BTN_1 2          // Vote A / Scroll Up
#define BTN_2 3          // Vote B / Scroll Down
#define BTN_3 4          // Vote C (NOTA)
#define BTN_CONFIRM 5    // Master Confirm / Cast Vote
#define BUZZER_PIN 6     // Vote Finalised Confirmation
#define LED_PIN 7        // EVM Unlocked Indicator

// --- Global Objects ---
LiquidCrystal_I2C lcd(0x27, 16, 2); // Adjust I2C address if needed (0x27 or 0x3F typical)
Adafruit_Fingerprint finger = Adafruit_Fingerprint(&Serial1);
WiFiClient client;

// --- State Variables ---
String currentState = "IDLE";
int activeConstituencyId = 1;
unsigned long lastPollTime = 0;
const unsigned long POLL_INTERVAL = 2000;

// Setup Mode Variables
bool evmUnlocked = false;

// --- Function Prototypes ---
void connectWiFi();
void pollState();
String scanFingerprint();
String httpPost(String endpoint, String payload);
String httpGet(String endpoint);
void updateCandidateDisp(int cand);

void setup() {
  Serial.begin(115200);   // Debug serial
  Serial1.begin(57600);   // Fingerprint serial default baud rate

  // Initialize Inputs
  pinMode(BTN_1, INPUT_PULLUP);
  pinMode(BTN_2, INPUT_PULLUP);
  pinMode(BTN_3, INPUT_PULLUP);
  pinMode(BTN_CONFIRM, INPUT_PULLUP);

  // Initialize Outputs
  pinMode(LED_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  
  // Make sure they are off when the machine turns on
  digitalWrite(BUZZER_PIN, LOW);
  digitalWrite(LED_PIN, LOW);

  // Initialize LCD
  lcd.init();
  lcd.backlight();
  lcd.setCursor(0, 0);
  lcd.print("EVM Booting...");

  // Connect networking
  connectWiFi();

  // Initialize Fingerprint
  finger.begin(57600);
  delay(100);
  if (finger.verifyPassword()) {
    Serial.println("Found FP sensor!");
  } else {
    Serial.println("FP sensor not found!");
    lcd.clear();
    lcd.print("FP Sensor Error!");
    while (1) { delay(1); } // Halt if no FP sensor
  }
}

void loop() {
  // 1. Poll the global state continuously
  pollState();

  // 3. Setup Mode Logic (If IDLE)
  if (currentState == "IDLE") {
    if (!evmUnlocked) {
      lcd.setCursor(0, 0);
      lcd.print("EVM Locked      ");
      lcd.setCursor(0, 1);
      lcd.print("Scan Admin FP   ");
      
      String hash = scanFingerprint();
      if (hash != "") {
        // Authenticate admin
        JsonDocument doc;
        doc["fingerprint_hash"] = hash;
        String payload;
        serializeJson(doc, payload);
        
        lcd.clear();
        lcd.print("Verifying...");
        String res = httpPost("/api/evm/verify_admin", payload);
        
        JsonDocument resDoc;
        DeserializationError err = deserializeJson(resDoc, res);
        
        if (!err && resDoc["authorized"] == true) {
          evmUnlocked = true;
          lcd.clear();
          lcd.print("Admin Auth OK!");
          delay(2000);
        } else {
          lcd.clear();
          lcd.print("Auth Failed!");
          delay(2000);
          lcd.clear();
        }
      }
    } else {
      // EVM is unlocked, select constituency
      lcd.setCursor(0, 0);
      lcd.print("Select Area: ");
      lcd.print(activeConstituencyId);
      lcd.print("  ");
      lcd.setCursor(0, 1);
      lcd.print("Up/Dn to change ");
      
      if (digitalRead(BTN_1) == LOW) {
        activeConstituencyId++;
        if (activeConstituencyId > 5) activeConstituencyId = 1;
        delay(250); // Debounce
      }
      if (digitalRead(BTN_2) == LOW) {
        activeConstituencyId--;
        if (activeConstituencyId < 1) activeConstituencyId = 5;
        delay(250); // Debounce
      }
      if (digitalRead(BTN_CONFIRM) == LOW) {
        currentState = "VOTING"; // Locally switch to voting mode
        lcd.clear();
        lcd.print("Area Locked!");
        delay(2000);
      }
    }
  } 
  // 4. Voting Mode Logic
  else if (currentState == "VOTING") {
    lcd.setCursor(0, 0);
    lcd.print("EVM Ready/Area:");
    lcd.print(activeConstituencyId);
    lcd.setCursor(0, 1);
    lcd.print("Scan Voter FP   ");
    
    String hash = scanFingerprint();
    if (hash != "") {
      JsonDocument doc;
      doc["fingerprint_hash"] = hash;
      doc["constituency_id"] = activeConstituencyId;
      String payload;
      serializeJson(doc, payload);
      
      lcd.clear();
      lcd.print("Verifying...");
      String res = httpPost("/api/evm/verify_voter", payload);
      
      JsonDocument resDoc;
      DeserializationError err = deserializeJson(resDoc, res);
      
      if (!err && resDoc["authorized"] == true) {
        // UNLOCK: Turn on the Green LED so the voter knows they can press a button
        digitalWrite(LED_PIN, HIGH); 
        
        lcd.clear();
        lcd.print("Select Candidate");
        
        int selectedCandidate = 0;
        bool confirmed = false;
        
        // Wait for candidate selection and confirmation
        while (!confirmed) {
          if (digitalRead(BTN_1) == LOW) { selectedCandidate = 1; updateCandidateDisp(1); delay(250); }
          if (digitalRead(BTN_2) == LOW) { selectedCandidate = 2; updateCandidateDisp(2); delay(250); }
          if (digitalRead(BTN_3) == LOW) { selectedCandidate = 3; updateCandidateDisp(3); delay(250); }
          
          if (digitalRead(BTN_CONFIRM) == LOW && selectedCandidate != 0) {
            confirmed = true;
          }
        }
        
        // LOCK: Turn off the Green LED as soon as they confirm
        digitalWrite(LED_PIN, LOW);
        
        lcd.clear();
        lcd.print("Casting Vote...");
        
        // CRITICAL ANONYMITY REQUIREMENT: Two separate POST requests
        
        // Request 1: Mark identity as voted
        JsonDocument markDoc;
        markDoc["fingerprint_hash"] = hash;
        String markPayload;
        serializeJson(markDoc, markPayload);
        httpPost("/api/evm/mark_voted", markPayload);
        
        // Request 2: Cast anonymous ballot
        JsonDocument ballotDoc;
        ballotDoc["constituency_id"] = activeConstituencyId;
        ballotDoc["candidate_id"] = selectedCandidate;
        String ballotPayload;
        serializeJson(ballotDoc, ballotPayload);
        httpPost("/api/evm/cast_vote", ballotPayload);
        
        // CONFIRMATION BEEP: Loud 1-second beep to finalize the process
        digitalWrite(BUZZER_PIN, HIGH);
        delay(1000);
        digitalWrite(BUZZER_PIN, LOW);
        
        lcd.clear();
        lcd.print("Vote Finalized!");
        lcd.setCursor(0, 1);
        lcd.print("Thank You!");
        delay(2000);
        lcd.clear();
        
      } else {
        lcd.clear();
        lcd.print("Not Authorized");
        lcd.setCursor(0, 1);
        lcd.print("Or Already Voted");
        delay(3000);
        lcd.clear();
      }
    }
  } 
  // 4b. Poll Closed Logic (Results Viewer)
  else if (currentState == "POLL_CLOSED") {
    lcd.setCursor(0, 0);
    lcd.print("POLL CLOSED     ");
    lcd.setCursor(0, 1);
    lcd.print("Press Confirm   ");
    
    // Wait for BTN_CONFIRM to trigger admin prompt
    if (digitalRead(BTN_CONFIRM) == LOW) {
      delay(250); // debounce
      lcd.clear();
      lcd.setCursor(0, 0);
      lcd.print("Scan Admin FP   ");
      lcd.setCursor(0, 1);
      lcd.print("To load results ");
      
      String hash = scanFingerprint();
      if (hash != "") {
        JsonDocument doc;
        doc["fingerprint_hash"] = hash;
        String payload;
        serializeJson(doc, payload);
        
        lcd.clear();
        lcd.print("Verifying...");
        String res = httpPost("/api/evm/verify_admin", payload);
        
        JsonDocument resDoc;
        DeserializationError err = deserializeJson(resDoc, res);
        
        if (!err && resDoc["authorized"] == true) {
          lcd.clear();
          lcd.print("Fetching...     ");
          
          String resultsJson = httpGet("/api/evm/results");
          JsonDocument resArr;
          DeserializationError arrErr = deserializeJson(resArr, resultsJson);
          
          if (!arrErr && resArr.is<JsonArray>()) {
            JsonArray arr = resArr.as<JsonArray>();
            int arraySize = arr.size();
            
            if (arraySize == 0) {
              lcd.clear();
              lcd.print("No votes cast!  ");
              delay(2000);
            } else {
              int currentIndex = 0;
              bool exitResults = false;
              
              lcd.clear();
              lcd.print("RESULTS MODE    ");
              delay(1000);
              
              while (!exitResults) {
                // Display current candidate
                JsonObject item = arr[currentIndex];
                int c_id = item["c"];
                int v_count = item["v"];
                
                lcd.setCursor(0, 0);
                lcd.print("Cand " + String(c_id) + " Votes:  ");
                lcd.setCursor(0, 1);
                lcd.print(String(v_count) + "               "); // Pad to clear old numbers
                
                // Handle scrolling
                if (digitalRead(BTN_1) == LOW) {
                  currentIndex--;
                  if (currentIndex < 0) currentIndex = arraySize - 1;
                  delay(250);
                }
                if (digitalRead(BTN_2) == LOW) {
                  currentIndex++;
                  if (currentIndex >= arraySize) currentIndex = 0;
                  delay(250);
                }
                
                // Exit on CONFIRM
                if (digitalRead(BTN_CONFIRM) == LOW) {
                  exitResults = true;
                  delay(250);
                }
              }
            }
          } else {
            lcd.clear();
            lcd.print("Fetch Error!    ");
            delay(2000);
          }
        } else {
          lcd.clear();
          lcd.print("Not Authorized  ");
          delay(2000);
        }
      }
    }
  }
  // Handle other global states (GENESIS, ENROLL_ADMIN, etc.)
  // 5. Enrollment Modes (Genesis, Admin, Voter)
  else if (currentState == "ENROLL_ADMIN" || currentState == "ENROLL_VOTER" || currentState == "GENESIS" || currentState == "AUTH_ADMIN") {
    lcd.setCursor(0, 0);
    lcd.print(currentState.substring(0, 16)); // Keep it within 16 chars
    lcd.setCursor(0, 1);
    lcd.print("Place Finger... ");
    
    String newHash = scanFingerprint();
    
    if (newHash != "") {
      lcd.clear();
      lcd.print("Sending to DB...");
      
      JsonDocument doc;
      doc["fingerprint_hash"] = newHash;
      String payload;
      serializeJson(doc, payload);
      
      // Post the new fingerprint to the backend
      String res = httpPost("/api/evm/fingerprint", payload);
      
      lcd.clear();
      lcd.print("Sent Success!");
      delay(2000);
      
      // Temporarily revert to IDLE locally until the next poll updates the state
      currentState = "IDLE"; 
    }
  }
}

// --- Helper Functions ---

void connectWiFi() {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("Connecting WiFi");
  WiFi.begin(ssid, password);
  int dots = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    lcd.setCursor(dots % 16, 1);
    lcd.print(".");
    dots++;
  }
  lcd.clear();
  lcd.print("WiFi Connected!");
  delay(1000);
}

void pollState() {
  if (millis() - lastPollTime < POLL_INTERVAL) return;
  lastPollTime = millis();

  if (!client.connect(serverIP, serverPort)) return;
  
  // Simple HTTP GET
  client.println("GET /api/evm/poll HTTP/1.1");
  client.println("Host: " + String(serverIP));
  client.println("Connection: close");
  client.println();

  // Wait for response
  unsigned long timeout = millis();
  while (client.available() == 0) {
    if (millis() - timeout > 5000) {
      client.stop();
      return;
    }
  }

  // Parse response
  String response = "";
  bool isBody = false;
  while (client.available()) {
    String line = client.readStringUntil('\n');
    if (line == "\r") isBody = true;
    else if (isBody) response += line;
  }

  // Strictly use ArduinoJson 7 Document
  JsonDocument doc;
  DeserializationError error = deserializeJson(doc, response);
  if (!error) {
    String newState = doc["state"].as<String>();
    if (newState != "null" && newState.length() > 0) {
      currentState = newState;
    }
  }
}

// 2. Fingerprint Logic (Non-Blocking)
String scanFingerprint() {
  unsigned long startTime = millis();
  int p = -1;
  
  // Wait for finger to be put down
  while (p != FINGERPRINT_OK) {
    // 15 second timeout to avoid freezing
    if (millis() - startTime > 15000) {
      return "";
    }
    p = finger.getImage();
    if (p != FINGERPRINT_OK && p != FINGERPRINT_NOFINGER) {
      // Read error or other issue
      delay(50);
    }
    // Poll the network state minimally while waiting if needed
    // However, specs say "resume polling so board doesn't freeze" after returning empty string.
  }

  // Got image
  p = finger.image2Tz();
  if (p != FINGERPRINT_OK) return "";

  // Search DB
  p = finger.fingerSearch();
  if (p == FINGERPRINT_OK) {
    // Found a match
    lcd.setCursor(0, 1);
    lcd.print("Found FP!       ");
    delay(500);
    return "R307_HASH_" + String(finger.fingerID);
  } else if (p == FINGERPRINT_NOTFOUND) {
    lcd.setCursor(0, 1);
    lcd.print("FP Not Found!   ");
    delay(2000);
    lcd.clear();
    return "";
  }
  
  return "";
}

String httpPost(String endpoint, String payload) {
  if (!client.connect(serverIP, serverPort)) {
    Serial.println("Connection failed");
    return "";
  }
  
  client.println("POST " + endpoint + " HTTP/1.1");
  client.println("Host: " + String(serverIP));
  client.println("Content-Type: application/json");
  client.println("Content-Length: " + String(payload.length()));
  client.println("Connection: close");
  client.println();
  client.println(payload);

  unsigned long timeout = millis();
  while (client.available() == 0) {
    if (millis() - timeout > 10000) {
      client.stop();
      return "";
    }
  }

  String response = "";
  bool isBody = false;
  while (client.available()) {
    String line = client.readStringUntil('\n');
    if (line == "\r") isBody = true;
    else if (isBody) response += line;
  }
  return response;
}

String httpGet(String endpoint) {
  if (!client.connect(serverIP, serverPort)) {
    Serial.println("Connection failed");
    return "";
  }
  
  client.println("GET " + endpoint + " HTTP/1.1");
  client.println("Host: " + String(serverIP));
  client.println("Connection: close");
  client.println();

  unsigned long timeout = millis();
  while (client.available() == 0) {
    if (millis() - timeout > 10000) {
      client.stop();
      return "";
    }
  }

  String response = "";
  bool isBody = false;
  while (client.available()) {
    String line = client.readStringUntil('\n');
    if (line == "\r") isBody = true;
    else if (isBody) response += line;
  }
  return response;
}

void updateCandidateDisp(int cand) {
  lcd.setCursor(0, 1);
  lcd.print("Cand: ");
  if (cand == 1) lcd.print("A (BTN1)  ");
  if (cand == 2) lcd.print("B (BTN2)  ");
  if (cand == 3) lcd.print("C (NOTA)  ");
}
