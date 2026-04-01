# Smart EVM — Biometric Command Center

<div align="center">
  <img src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Arduino-00979D?style=for-the-badge&logo=Arduino&logoColor=white" alt="Arduino" />
  <img src="https://img.shields.io/badge/SQLite-003B57?style=for-the-badge&logo=sqlite&logoColor=white" alt="SQLite" />
</div>

<br/>

**Smart EVM** is a highly secure, biometric-enabled Electronic Voting Machine (EVM) architecture. It is designed to modernize election platforms by strictly decoupling voter biometric identities from anonymous ballot storage, effectively solving the classic digital voting challenge: ensuring absolute identity verification without compromising ballot secrecy.

The system relies on an **Arduino Uno R4 WiFi** edge device acting as a dynamic hardware terminal, interacting with a robust, centralized **FastAPI** state-machine over a local network.

---

## 🚀 Key Features

- **Strict Ballot Anonymity**: Voter identity (fingerprint hash) and candidate selection are transmitted via completely separate HTTP `POST` requests. The active `votes` table intentionally lacks a `voter_id` column.
- **Hardware-Enforced Security Actions**: High-risk activities (e.g., adding/deleting administrators, unlocking the EVM after a power loss) require a physical, biometric fingerprint scan *on the actual EVM device* by a pre-enrolled admin. Web credentials alone are insufficient.
- **Flawless Auto-Resume State Machine**: Power outages mid-voting drop the EVM straight into a secure lockdown. Re-authentication dynamically fetches the currently active local constituency to prevent human error mid-election.
- **Election Lifecycle Management**: A dedicated command-center UI allows web admins to smoothly freeze active polls, fetch lightweight results securely onto the EVM LCD, and wipe active votes to an `ArchivedElection` ledger.
- **Modern Hardware UI**: Non-blocking C++ loops using `millis()` ensure smooth scrolling during LCD setup modes and instantaneous responses for 4 integrated physical push buttons (Candidate A, B, NOTA, and Confirm).

---

## 🛠 Tech Stack

**Backend & Web Dashboard:**
- Python 3.10+, FastAPI, Uvicorn (ASGI Server)
- SQLAlchemy (ORM) & SQLite (Database)
- Jinja2 (HTML Templates), TailwindCSS (Styling)
- JWT (JSON Web Tokens) & HTTPOnly Cookies, passlib + bcrypt

**IoT Edge Device (Arduino):**
- Arduino Uno R4 WiFi (`WiFiS3.h`)
- R307 Optical Fingerprint Sensor (`Adafruit_Fingerprint.h`)
- 16x2 I2C LCD Display (`LiquidCrystal_I2C.h`)
- ArduinoJson v7 (Strict, zero-overhead payload parsing)

---

## 🔌 Hardware Wiring Guide

| Component | Arduino Uno R4 Pin | Notes |
| :--- | :--- | :--- |
| **R307 Fingerprint** | `TX -> Pin 0` (RX), `RX -> Pin 1` (TX) | Uses `Serial1` (Hardware Serial) |
| **I2C LCD (16x2)** | `SDA -> A4`, `SCL -> A5` | Standard I2C configuration (Address `0x27`) |
| **Button 1 (Up/Cand A)** | `Digital Pin 2` | Configured as `INPUT_PULLUP` (Active LOW) |
| **Button 2 (Down/Cand B)**| `Digital Pin 3` | Configured as `INPUT_PULLUP` (Active LOW) |
| **Button 3 (NOTA)** | `Digital Pin 4` | Configured as `INPUT_PULLUP` (Active LOW) |
| **Confirm Button** | `Digital Pin 5` | Configured as `INPUT_PULLUP` (Active LOW) |
| **Buzzer** | `Digital Pin 6` | Emits 1-sec confirmation beep |
| **Green LED** | `Digital Pin 7` | Illuminates when EVM is actively taking a vote |

---

## 🖥 Deployment & Setup

### 1. Backend Server Setup
Ensure Python 3.10+ is installed.

```bash
# Clone the repository
cd smart_evm

# Create and activate a Virtual Environment
python -m venv venv
# Windows:
.\venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# Install Dependencies
pip install fastapi uvicorn sqlalchemy jinja2 python-multipart passlib[bcrypt] python-jose

# Run the Command Center
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
A fresh SQLite database (`evm.db`) will be automatically generated. Open your browser and navigate to `http://localhost:8000/`.

### 2. Genesis Mode (Bootstrapping the System)
Because the system is zero-trust, you cannot manage the platform until a physical hardware Admin operates the EVM.
1. When navigating to the dashboard, you will be redirected to the `/setup` Genesis creation pipeline.
2. Enter your desired Web Dashboard credentials.
3. The global state will switch to **Genesis**. Place your thumb on the EVM fingerprint scanner to lock in your identity as the first Super Admin. 

### 3. Edge Device Flashing
Configure the firmware before flashing it to your Uno R4 board:
1. Open `arduino_evm/arduino_evm.ino` in the Arduino IDE.
2. Ensure you have installed `WiFiS3`, `Adafruit Fingerprint Sensor Library`, `LiquidCrystal I2C`, and `ArduinoJson` (Must be version 7+).
3. Modify the network lines at the top of the sketch to match your WiFi and the Local IP/Port of the computer hosting your FastAPI server.
```cpp
const char* ssid = "YOUR_WIFI_SSID";
const char* password = "YOUR_WIFI_PASSWORD";
const char* serverIP = "192.168.1.100";  // Point this to your FastAPI host
```
4. Flash the code to the Arduino.

---

## 🔒 Security Architecture

**1. Isolate the Identity from the Ballot**  
If a server breach happens, bad actors can only see a list of anonymous fingerprints (`voters` table) and a bucket of grouped anonymous tallies (`votes` table). Because the two HTTP `POST` requests are transmitted asynchronously during the hardware voting phase without crossover variables, proving *who* voted for *whom* is mathematically impossible.

**2. Physical Overrides**  
Web-UI button clicks representing high-risk mutations drop the server into an `AUTH_ADMIN` suspension state. The action strictly refuses to execute logically in the backend ORM until an authorized fingerprint hash is verified over the network via a `POST /api/evm/verify_admin` pulse from the edge device.

---

## 📝 License
Proprietary / Closed Source. Designed for high-integrity electoral environments.
