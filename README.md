# Ping Monitoring Script

This repository contains a Python script for monitoring the status of network devices by sending ping requests.

## Description

The `Ping_Monitoring.py` script periodically pings a list of IP addresses and logs the results. This can be useful for monitoring the availability and responsiveness of network devices.

## Requirements

- Python 3.x
- `os` module (standard library)
- `subprocess` module (standard library)

## Installation

1. Clone this repository to your local machine:
    ```bash
    git clone https://github.com/yourusername/PingMonitoring.git
    cd PingMonitoring
    ```

2. Ensure you have Python 3 installed on your machine.

## Usage

1. Open the script and modify the list of IP addresses to monitor:
    ```python
    ips = ['192.168.1.1', '8.8.8.8', '1.1.1.1']
    ```

2. Run the script:
    ```bash
    python Ping_Monitoring.py
    ```

3. The script will output the status of each IP address to the console.

## Contributing

Contributions are welcome! Please fork this repository and submit a pull request for any improvements or bug fixes.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
