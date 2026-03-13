# PingWatch – Network Monitoring Platform

![Python](https://img.shields.io/badge/python-3.x-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![License](https://img.shields.io/github/license/Nividan/Pingwatch)
![Stars](https://img.shields.io/github/stars/Nividan/Pingwatch?style=social)


## Table of Contents

- [Project Overview](#project-overview)
- [Features](#features)
- [Technologies Used](#technologies-used)
- [Installation](#installation)
- [Usage](#usage)
- [Screenshots](#screenshots)
- [Architecture](#architecture)
- [Core Components](#core-components)
- [Frontend Structure](#frontend-structure)
- [High-Level Flow](#high-level-flow)
- [Project Structure](#project-structure)



## Project Overview

PingWatch is a Python-based network monitoring platform designed to track the availability and health of network devices and services.

The system supports multiple sensor types such as ICMP (ping), HTTP/HTTPS checks, TCP port checks, SNMP, and other network monitoring probes. 
Collected data is displayed in a web-based dashboard that provides event logging, device management, and network topology visualization.


## Features

- 📡 Real-time device monitoring
- 🔎 Multiple sensor types (ICMP, service checks, and custom probes)
- ⏱ Configurable monitoring intervals
- 📜 Historical event logging
- 🚨 Alerting and notification system
- 🌐 Web-based monitoring dashboard
- 🗺 Interactive Network Topology Manager (NTM)

### Supported Sensors

- ICMP (Ping)
- HTTP / HTTPS
- TCP Port
- SNMP

## Technologies Used

- **Backend:** Python 3.x
- **Web Server:** Python's built-in `http.server`
- **Database:** SQLite
- **Frontend:** HTML, CSS, JavaScript
- **System Tray Integration:** pystray, Pillow
- **Network Monitoring:** Uses Python libraries (`socket`, `urllib`, `subprocess`) for multiple sensor types.


## Installation

1. **Clone the repository:**  
   ```bash
   git clone https://github.com/Nividan/Pingwatch.git
   ```
2. **Navigate into the project directory:**  
   ```bash
   cd Pingwatch
   ```
3. **Install the required dependencies and start the server:**
   ```bash
   ./start.bat
   ```

## Usage
   
   To start monitoring, run the following command:
   ```bash
   start.bat (with console)
   ```
   ```bash
   pythonw pingwatch.pyw (without console)
   ```


## Screenshots

### 📡 Network Dashboard
Real-time monitoring of all devices with live status, latency, and connectivity.
<img width="2201" height="671" alt="image" src="https://github.com/user-attachments/assets/91e2237f-a3c8-447c-adbc-5d91e950f63a" />
<img width="2207" height="571" alt="image" src="https://github.com/user-attachments/assets/276fc670-1425-4150-ae9d-21ee33da8565" />

### 🖥 Device Information
View detailed information for every device including IP address, latency, uptime, and custom notes.
<img width="1366" height="863" alt="image" src="https://github.com/user-attachments/assets/06a38bfa-3dd1-431e-8dd1-60873d9624e8" />
<img width="1138" height="563" alt="image" src="https://github.com/user-attachments/assets/3a027022-4a46-4fc2-b2e3-9f017b06a2e8" />
<img width="1071" height="875" alt="image" src="https://github.com/user-attachments/assets/131ceef8-bb9c-4abb-9346-f993f409365f" />
<img width="1054" height="907" alt="image" src="https://github.com/user-attachments/assets/c456c19b-348b-44f8-b68c-3ae9c48438af" />

### 📜 Event Logs
Centralized event logging with timestamps, severity levels, and device filtering.
<img width="2211" height="656" alt="image" src="https://github.com/user-attachments/assets/210e31ec-6367-4e60-bcbd-5257f36f5a5d" />
<img width="625" height="428" alt="image" src="https://github.com/user-attachments/assets/a9a1e8ef-6da1-40c2-b31c-e5a4548f5cbb" />
<img width="2203" height="661" alt="image" src="https://github.com/user-attachments/assets/3a26e38d-6f12-46db-9d46-11f27561d001" />
<img width="1379" height="472" alt="image" src="https://github.com/user-attachments/assets/c5ac9a0e-b959-458c-a568-74af1b8f24cd" />

### 🗺 Network Topology Visualization
NTM provides an interactive topology map where devices, switches, and servers are displayed visually with their connections.

**Monitor Live Device NTM**
<img width="2143" height="1195" alt="image" src="https://github.com/user-attachments/assets/2eff647b-befd-4c4c-b0e6-ee43adb1c713" />

**Draw.io style NTM**
<img width="2200" height="1209" alt="image" src="https://github.com/user-attachments/assets/f42cb4f3-4167-4c91-b6d2-df635ad7c4ef" />



## Architecture

PingWatch follows a modular architecture:

- **Sensor Engine**  
  Handles different monitoring probes such as ICMP checks, service availability checks, and other sensor types.

- **Logging Module**  
  Stores monitoring results and system events.

- **Notification Module**  
  Generates alerts when device states change.

- **Web Interface**  
  Provides the dashboard, topology view, and log viewer.

This modular design allows new monitoring methods and features to be added without major changes to the existing codebase.


## Core Components

- **`server.py`**  
  Main application entry point and HTTP server.  
  Serves the frontend, exposes API endpoints, and connects the UI with the backend logic.

- **`state.py`**  
  Central runtime state manager.  
  Holds devices, sensors, monitoring status, and live results used across the application.

- **`probes.py`**  
  Sensor engine implementation.  
  Contains the actual monitoring probes such as ICMP, HTTP, TCP, SNMP, DNS, TLS, banner checks, and more.

- **`db.py`**  
  SQLite persistence layer.  
  Stores devices, sensors, logs, settings, audit data, and monitoring history.

- **`network_map.py`**  
  Network Topology Manager (NTM) backend.  
  Manages topology pages, nodes, links, and map-related data stored in the database.

- **`auth.py`**  
  Authentication and session management.  
  Handles login, password hashing, roles, and active sessions.

- **`trap_receiver.py`**  
  SNMP trap listener.  
  Receives incoming SNMP traps and forwards them into the monitoring/event pipeline.

- **`smtp_alert.py`**  
  Alerting module for email notifications.  
  Sends alerts when monitored sensors change state.

- **`logger.py`**  
  Central logging module.  
  Handles application logging, event logging, and operational diagnostics.

- **`settings.py` / `config.py`**  
  Configuration and runtime settings layer.  
  Defines constants, runtime options, and shared application settings.

- **`gui.py`**  
  Native desktop status window.  
  Provides a lightweight local GUI and integrates with system tray behavior.

- **`pingwatch.pyw` / `start.bat`**  
  Startup helpers for launching PingWatch with or without a console window.


## Frontend Structure

The frontend is located in the `frontend/` directory and provides the web dashboard and topology interface.

- **`index.html`** — Main dashboard UI
- **`map.html`** — Network Topology Manager view
- **`app.js`** — Frontend bootstrap and shared app logic
- **`dashboard.js`** — Dashboard rendering and live monitoring views
- **`devices.js`** — Device-related UI logic
- **`sensors.js`** — Sensor-related UI logic
- **`events.js`** — Event log viewer UI
- **`forms-device.js`** — Device forms
- **`forms-sensor.js`** — Sensor forms
- **`forms-settings.js`** — Settings forms
- **`forms-users.js`** — User management forms
- **`forms-io.js`** — Import/export related forms
- **`forms-utils.js`** — Shared form helpers
- **`bg.js`** — Background visual effects / UI enhancements
- **`style.css`** — Main application styling


## High-Level Flow

1. The user interacts with the **web dashboard** or **desktop UI**.
2. Requests are handled by **`server.py`** through API endpoints.
3. The backend reads and updates runtime objects in **`state.py`**.
4. Monitoring checks are executed by **`probes.py`**.
5. Results, logs, topology data, and settings are stored through **`db.py`**.
6. Alerts are triggered through **`smtp_alert.py`** when required.
7. SNMP traps can be received asynchronously through **`trap_receiver.py`**.
8. The frontend updates live views such as dashboard panels, event logs, and the Network Topology Manager.

## Project Structure

```
pingwatch/
├── auth.py
├── config.py
├── db.py
├── gui.py
├── logger.py
├── network_map.py
├── pingwatch.pyw
├── probes.py
├── server.py
├── settings.py
├── smtp_alert.py
├── snmp_catalog.py
├── start.bat
├── state.py
├── trap_receiver.py
└── frontend/
    ├── index.html
    ├── map.html
    ├── app.js
    ├── dashboard.js
    ├── devices.js
    ├── events.js
    ├── sensors.js
    ├── forms-device.js
    ├── forms-io.js
    ├── forms-sensor.js
    ├── forms-settings.js
    ├── forms-users.js
    ├── forms-utils.js
    ├── bg.js
    └── style.css
```
