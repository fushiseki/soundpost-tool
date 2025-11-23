# 4chan Soundpost Tool

A simple GUI tool for creating and extracting 4chan-style soundposts with configurable limits and safer defaults.

## Features

- Extracts and uploads audio to catbox.moe with validation
- Injects sound from catbox.moe into video files or still images
- Lets you pick the output container (MP4 or WebM) and a custom size cap
- Uses temporary working directories instead of polluting the repo folder
- Status log keeps a running record of each step for easier troubleshooting
- Fully dark mode GUI, simple and cross-platform

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
   ```
   python soundpost_gui.py
   ```

2. **Choose a source file** (video or still image). If the filename already contains a `[sound=URL]` tag, the Inject mode will download and mux that audio. Extraction mode requires a video input.
   - You can start from a still image to build a video with the tagged audio.

3. **Pick options:**
   - *Target max file size (MB)* controls how aggressively the video is recompressed when stripping audio.
   - *Output container* lets you keep MP4 or WebM workflows consistent with the file extension.
   - *Preserve original file* toggles whether the source is kept alongside the newly produced soundpost.

4. **Click Run** and watch the status log for progress and errors.

---

## Troubleshooting

- If you get an error about ffmpeg/ffprobe not found, make sure they are installed and available in your system PATH.
- If you get an error about missing Python modules, make sure you ran `pip install -r requirements.txt`.
