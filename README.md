# Pingwatch

## Project Overview
Pingwatch is a Python-based network monitoring platform designed to help users monitor the availability and responsiveness of their networked devices. It leverages ping requests to evaluate device status over time and provide notifications and logging capabilities.

## Features
- Real-time device monitoring
- Configurable ping intervals
- Historical logging of device statuses
- Notification support via email or messaging apps
- Simple web dashboard for visual representation of device status

## Installation
1. **Clone the repository:**  
   ```bash
   git clone https://github.com/Nividan/Pingwatch.git
   ```
2. **Navigate into the project directory:**  
   ```bash
   cd Pingwatch
   ```
3. **Install the required dependencies:**  
   ```bash
   pip install -r requirements.txt
   ```

## Usage
To start monitoring, run the following command:
```bash
python pingwatch.py
```

## Configuration
The configuration options are located in `config.yaml`. Here you can define:
- The list of devices to monitor
- The ping interval
- Notification settings

### Example Configuration:
```yaml
devices:
  - name: "Router"
    ip: "192.168.1.1"
  - name: "Server"
    ip: "192.168.1.2"

ping_interval: 60  # seconds
notification:
  enabled: true
  method: "email"
  email: "your-email@example.com"
```

## Architecture
Pingwatch follows a modular architecture:
- **Ping Module:** Handles sending and receiving ping requests.
- **Logging Module:** Records the status of each monitored device to a log file.
- **Notification Module:** Sends alerts based on the device status changes.
- **Web Interface:** Visual representation of monitored devices' statuses.

This architecture allows for easy extension, as new modules can be added without major changes to the existing codebase.