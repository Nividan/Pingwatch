# Pingwatch

## Project Overview
   Pingwatch is a Python-based network monitoring platform designed to help users monitor the availability and responsiveness of their networked devices.
   It leverages ping requests to evaluate device status over time and provide notifications and logging capabilities.

## Features
   - Real-time device monitoring
   - Configurable ping intervals
   - Historical logging of device statuses
   - Notification support via email or messaging apps
   - Simple web dashboard for visual representation of device status

## Technologies Used
   - Backend: Python 3.x
   - Web Server: Python's built-in http.server module
   - Database: SQLite
   - Frontend: HTML, CSS, JavaScript
   - System Tray Integration: pystray, Pillow
   - Network Probing: Uses standard Python libraries (socket, urllib, subprocess ) for various network checks.


## Installation
1. **Clone the repository:**  
   ```bash
   git clone https://github.com/Nividan/Pingwatch.git
   ```
2. **Navigate into the project directory:**  
   ```bash
   cd Pingwatch
   ```
3. **Install the required dependencies and start the server**
   ****  
   Installing requirements and starting the server with the console
   ```bash
   ./start.bat
   ```
   **or**
   
   Installing requirements, then start the server without the console
   ```bash
   pip install -r requirements.txt
   python pingwatch.pyw (no console)
   ```

## Usage
   To start monitoring, run the following command:
   ```bash
   python pingwatch.pyw (no console)
   ```
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
**Drowio type NTM**
<img width="2200" height="1209" alt="image" src="https://github.com/user-attachments/assets/f42cb4f3-4167-4c91-b6d2-df635ad7c4ef" />


## Architecture
   Pingwatch follows a modular architecture:
   - **Ping Module:** Handles sending and receiving ping requests.
   - **Logging Module:** Records the status of each monitored device to a log file.
   - **Notification Module:** Sends alerts based on the device status changes.
   - **Web Interface:** Visual representation of monitored devices' statuses.

This architecture allows for easy extension, as new modules can be added without major changes to the existing codebase.
