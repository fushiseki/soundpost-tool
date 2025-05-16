# 4chan Soundpost Tool

A simple GUI tool for creating and extracting 4chan-style soundposts.

## Features

- Extracts and uploads audio to catbox.moe
- Injects sound from catbox.moe into silent video files
- Fully dark mode GUI, mnyes
- Simple and cross-platform

## Requirements

- Python 3.8 or newer ([Download Python](https://www.python.org/downloads/))
- [ffmpeg](https://ffmpeg.org/download.html) and ffprobe (must be available in your system PATH)
- Python dependencies listed in `requirements.txt`

## Installation

1. **Install Python 3.8+** if you don't have it:  
   [https://www.python.org/downloads/](https://www.python.org/downloads/)

2. **Install required Python packages:**  
   Open a terminal/command prompt in this folder and run:
   pip install -r requirements.txt


3. **Install ffmpeg:**  
- Download from [https://ffmpeg.org/download.html](https://ffmpeg.org/download.html)  
- On Windows: extract the ZIP, then add the `bin` folder to your system PATH.  
  (See [How to add ffmpeg to Windows PATH](https://www.geeksforgeeks.org/how-to-install-ffmpeg-on-windows/).)

- On Mac:  
  ```
  brew install ffmpeg
  ```
- On Linux (Debian/Ubuntu):  
  ```
  sudo apt-get install ffmpeg
  ```

## Usage

1. **Run the tool:**
    python soundpost_gui.py

2. **Use the GUI!**

---

## Troubleshooting

- If you get an error about ffmpeg/ffprobe not found, make sure they are installed and available in your system PATH.
- If you get an error about missing Python modules, make sure you ran `pip install -r requirements.txt`.
