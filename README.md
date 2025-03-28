# Tango Live Stream Recorder
![image](https://github.com/user-attachments/assets/bf00bdba-01cc-47d4-81bf-8a8fa222579b)

## Overview
This is a Python application for recording live streams from Tango, a live streaming platform. The application provides a user-friendly interface to capture and save live stream videos with various features.

## Features
- Record live streams from Tango
- Save streams with custom output directory
- Set maximum recording duration
- View recording statistics
- System tray integration
- Automatic profile image extraction
- Retry mechanism for stream recording

## Prerequisites
- Python 3.7+
- PyQt5
- FFmpeg
- Requests
- BeautifulSoup4

## Installation

### Dependencies
Install required Python packages:
```bash
pip install PyQt5 requests beautifulsoup4
```

### link to reach tango.php file
https://github.com/MrR00tsuz/tango.me-live-stream-find

### FFmpeg
Ensure FFmpeg is installed and available in your system PATH:
- Windows: Download from [FFmpeg Official](https://ffmpeg.org/download.html)
- macOS: `brew install ffmpeg`
- Linux: `sudo apt-get install ffmpeg`

## Usage
1. Run the script
2. Enter Tango stream URL
3. Select output directory
4. (Optional) Set maximum recording duration
5. Click "Add Stream"

## Configuration Files
- `stream_links.json`: Stores active stream recording information
- `recording_stats.json`: Tracks overall recording statistics


## Troubleshooting
- Ensure stable internet connection
- Check FFmpeg installation
- Verify Tango stream URL

## Contributing
Contributions are welcome! Please submit pull requests or open issues.

## Disclaimer
This tool is for educational purposes. Respect content creators' rights and platform terms of service.
