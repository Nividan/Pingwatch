# Pingwatch

## Project Overview
Pingwatch is a Python-based network monitoring platform designed to help users monitor the availability and responsiveness of their networked devices. It leverages ping requests to evaluate device status over time and provide notifications and logging capabilities.

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

## Architecture
Pingwatch follows a modular architecture:
- **Ping Module:** Handles sending and receiving ping requests.
- **Logging Module:** Records the status of each monitored device to a log file.
- **Notification Module:** Sends alerts based on the device status changes.
- **Web Interface:** Visual representation of monitored devices' statuses.

This architecture allows for easy extension, as new modules can be added without major changes to the existing codebase.
