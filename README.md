# NIFTY 50 Live Feed Server (`nifty-feed-server`)

An ultra-lightweight, zero-bloat microservice that connects to the **Angel One SmartAPI WebSocket 2.0 (`SmartWebSocketV2`)**, caches and filters the master instrument list, streams live Nifty 50 Spot and nearest-expiry Option Chain ticks, and broadcasts a thread-safe JSON snapshot over a high-performance HTTP server.

Designed specifically for low-latency algorithmic trading bots, local web dashboards, and mobile devices (running on Linux, Windows, macOS, or Android via Termux) with a resident RAM footprint under **25 MB**.

---

## Key Architectural Features

* **Fast-Cache Architecture:** Downloads and parses the 30 MB `OpenAPIScripMaster.json` once, filters exclusively for nearest-expiry `NFO OPTIDX NIFTY` options, and saves a tiny **15 KB fast-cache** (`filtered_nifty_scrips.json`). Subsequent restarts boot in **0.01 seconds**.
* **Chunked WebSocket Subscriptions:** Automatically slices Nifty option chain subscriptions into strict batches of **50 tokens** with 50ms pacing intervals to prevent socket buffer drops and rate-limit disconnects.
* **Pre-Populated Strike Chain:** Pre-allocates all sorted option strikes into memory (`LATEST_PRICES`) with `ltp: None` before market open, ensuring zero missing strikes in downstream strategies.
* **REST Spot Seed:** Immediately queries Nifty Spot LTP via REST upon authentication so spot data is populated even during pre-market hours before WebSocket ticks arrive.
* **Thread-Safe HTTP Broadcaster:** Uses Python's native `ThreadingHTTPServer` on `[http://127.0.0.1:5555/](http://127.0.0.1:5555/)` with permissive CORS headers, enabling simultaneous polling by multiple local Python bots and browser UIs without blocking.
* **Auto-Healing Reconnect Loop:** Automatically detects socket drops, re-establishes connectivity, and refreshes session JWT tokens after repeated disconnections.

---

## Repository File Structure

```text
nifty-feed-server/
│
├── .env.example              # Credentials and networking configuration template
├── .gitignore                # Ignores .env, bytecode, and cached master files
├── LICENSE                   # MIT Open Source License
├── README.md                 # Complete operational guide and API documentation
├── requirements.txt          # Minimal broker connection dependencies
│
├── config.py                 # Native .env loader & parameter constants
├── scrip_master.py           # Master JSON downloader, filter & 15 KB fast-cache
├── websocket_feed.py         # Angel One WebSocket 2.0 connection, chunks & auto-healer
├── broadcaster.py            # Threaded HTTP JSON streaming server (Port 5555)
│
├── index.html                # Standalone vanilla option chain visualizer
└── server.py                 # Main entry point (Orchestrates microservice)

```

---

## Installation & Setup

### 1. Requirements

* Python 3.9 or higher.
* An active Angel One SmartAPI account with API Key and TOTP secret enabled.

### 2. Clone and Install Dependencies

```bash
git clone https://github.com/your-username/nifty-feed-server.git
cd nifty-feed-server

python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt

```

### 3. Configure Environment Variables

Copy the template and fill in your Angel One credentials:

```bash
cp .env.example .env
nano .env  # Or use notepad .env on Windows

```

Your `.env` file should look like this:

```env
ANGEL_API_KEY=your_smartapi_key_here
ANGEL_CLIENT_CODE=your_client_id_here
ANGEL_PIN=your_mpin_here
ANGEL_TOTP_SECRET=your_base32_totp_secret_here

FEED_HOST=127.0.0.1
FEED_PORT=5555
CACHE_HOURS=12

```

---

## Operating Instructions

### Run the Feed Server

```bash
python3 server.py

```

*(The server will authenticate via TOTP, parse the scrip master, subscribe to all active Nifty options, and start broadcasting on port `5555`)*

### View the Live Option Chain UI

Open `index.html` directly in any web browser (Chrome, Edge, Brave, or Android browser). It polls `[http://127.0.0.1:5555/](http://127.0.0.1:5555/)` every 500ms, auto-identifies the ATM strike, and displays Calls (Left) vs Puts (Right) in real time.

---

## HTTP JSON API Specification

Downstream Python bots or browser clients can fetch live market state by making a standard `GET` request:

* **Endpoint:** `[http://127.0.0.1:5555/](http://127.0.0.1:5555/)`
* **Method:** `GET`
* **Response Format:** `application/json` (CORS enabled)

### Example JSON Payload Response

```json
{
  "spot": {
    "symbol": "NIFTY",
    "ltp": 24560.75,
    "token": "99926000",
    "updated": "09:32:15"
  },
  "contracts": {
    "24500": {
      "CE": {
        "token": "54210",
        "symbol": "NIFTY29OCT2624500CE",
        "ltp": 142.50
      },
      "PE": {
        "token": "54211",
        "symbol": "NIFTY29OCT2624500PE",
        "ltp": 98.20
      }
    }
  }
}

```

---

## Commands to Push to GitHub

If you are setting up this repository for the first time on GitHub:

```bash
git init
git add .
git commit -m "feat: initial production release of nifty-feed-server"
git branch -M main
git remote add origin https://github.com/<your-username>/nifty-feed-server.git
git push -u origin main

```

---

## License

Distributed under the MIT License. See `LICENSE` for more information.