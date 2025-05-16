from ttkthemes import ThemedTk
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
import subprocess
import requests
import re
import os
import shutil
import sys
import tempfile
import urllib.parse

# === Utility functions ===

def ffmpeg_installed():
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True)
        subprocess.run(['ffprobe', '-version'], capture_output=True)
        return True
    except Exception:
        return False

def extract_sound_url(filename):
    match = re.search(r'\[sound\s*=\s*(.*?)\]', filename, flags=re.IGNORECASE)
    if match:
        url = urllib.parse.unquote(match.group(1).strip())
        if not url.startswith("http"):
            url = "https://" + url
        return url
    return None

def download_audio(url, dest_path):
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers)
    if not r.ok:
        raise RuntimeError(f"Failed to download audio. Status: {r.status_code}")
    content_type = r.headers.get("Content-Type", "")
    audio_exts = (".mp3", ".aac", ".m4a", ".wav", ".ogg", ".flac")
    if "audio" not in content_type:
        # Accept application/octet-stream for audio-like files
        if content_type == "application/octet-stream" and str(dest_path).lower().endswith(audio_exts):
            pass  # Accept it!
    else:
        raise RuntimeError(f"Expected audio content, got: {content_type}")
    dest_path.write_bytes(r.content)
    if dest_path.stat().st_size < 1024:
        raise RuntimeError("Downloaded file is too small to be a valid audio file.")

def convert_audio_to_aac(input_audio, output_audio):
    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(input_audio),
        "-vn",
        "-acodec", "aac",
        "-ac", "2",
        "-ar", "44100",
        "-b:a", "192k",
        str(output_audio)
    ], check=True)

def mux_video_with_aac_audio(video_path, audio_path, output_path):
    # If input video is webm, re-encode video to mp4 first
    video_ext = video_path.suffix.lower()
    if video_ext == ".webm":
        # Temporary .mp4 video file
        mp4_video = video_path.with_name("__temp_video.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-i", str(video_path),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-an",  # remove audio
            str(mp4_video)
        ], check=True)
        # Now mux the audio and the new .mp4 video
        subprocess.run([
            "ffmpeg", "-y", "-i", str(mp4_video),
            "-i", str(audio_path),
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            str(output_path)
        ], check=True)
        # Clean up temp file
        if mp4_video.exists():
            mp4_video.unlink()
    else:
        # Already mp4, just mux as before
        subprocess.run([
            "ffmpeg", "-y", "-i", str(video_path),
            "-i", str(audio_path),
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            str(output_path)
        ], check=True)

def get_script_directory():
    return Path(__file__).resolve().parent

def extract_audio_ffmpeg(video_path, output_audio_path):
    subprocess.run([
        "ffmpeg", "-y", "-i", str(video_path), "-vn", "-acodec", "libmp3lame",
        str(output_audio_path)
    ], check=True)

def upload_to_catbox(audio_path):
    url = 'https://catbox.moe/user/api.php'
    with open(audio_path, 'rb') as f:
        files = {'fileToUpload': f}
        data = {'reqtype': 'fileupload'}
        response = requests.post(url, files=files, data=data)
    if response.status_code != 200:
        raise RuntimeError("Catbox upload failed.")
    return response.text.strip()

def compress_and_strip_audio(input_path, output_path, target_size_mb=4):
    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-an",
        "-c:v", "libx264",
        "-crf", "28",
        "-preset", "fast",
        "-movflags", "+faststart",
        str(output_path)
    ], check=True)
    if output_path.stat().st_size > target_size_mb * 1024 * 1024:
        print("⚠️ CRF output too large, retrying with bitrate-based compression...")
        duration_cmd = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(input_path)],
            capture_output=True, text=True
        )
        duration = float(duration_cmd.stdout.strip())
        target_bitrate = int((target_size_mb * 8 * 1024 * 1024) / duration)
        target_bitrate = max(100_000, min(target_bitrate, 1_500_000))
        subprocess.run([
            "ffmpeg", "-y", "-i", str(input_path),
            "-an", "-c:v", "libx264",
            "-b:v", f"{target_bitrate}",
            "-preset", "fast",
            "-movflags", "+faststart",
            str(output_path)
        ], check=True)

# === GUI Logic ===

class SoundpostTool:
    def __init__(self, root):
        self.root = root
        self.root.title("4chan Soundpost Tool")

        self.root.geometry("480x370")
        self.root.minsize(480, 350)
        self.root.resizable(True, True)
        self.root.configure(bg="#000000")  # Absolute black

        # Set theme to Equilux (ultra dark)
        try:
            self.root.set_theme("equilux")
        except Exception:
            pass

        # ==== File Selection Frame ====
        file_frame = ttk.LabelFrame(self.root, text="Step 1: Choose a Video File")
        file_frame.pack(fill='x', padx=16, pady=(16, 8))
        file_frame.config(style='TLabelframe')
        self._set_dark_bg(file_frame)

        self.video_label = ttk.Label(file_frame, text="No video file selected.")
        self.video_label.pack(anchor='w', padx=8, pady=(4,2))

        ttk.Button(file_frame, text="Browse...", command=self.select_video).pack(anchor='e', padx=8, pady=4)

        # ==== Mode and Options Frame ====
        options_frame = ttk.LabelFrame(self.root, text="Step 2: Select Action")
        options_frame.pack(fill='x', padx=16, pady=8)
        options_frame.config(style='TLabelframe')
        self._set_dark_bg(options_frame)

        self.mode = tk.StringVar(value="inject")
        modes = [("Inject audio from soundpost", "inject"),
                 ("Extract audio and prepare soundpost", "extract")]
        for text, value in modes:
            ttk.Radiobutton(options_frame, text=text, variable=self.mode, value=value).pack(anchor='w', padx=8, pady=2)

        self.preserve_original = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="Preserve original video file", variable=self.preserve_original).pack(anchor='w', padx=8, pady=(8, 2))

        # ==== Action Buttons ====
        action_frame = ttk.Frame(self.root)
        action_frame.pack(fill='x', padx=16, pady=(8, 8))
        self._set_dark_bg(action_frame)
        ttk.Button(action_frame, text="Run", command=self.run, width=12).pack(side='left', padx=(0,8))
        ttk.Button(action_frame, text="Quit", command=self.root.quit, width=12).pack(side='left')

        # ==== Separator ====
        ttk.Separator(self.root, orient='horizontal').pack(fill='x', padx=0, pady=(2,0))

        # ==== Status Bar as Scrollable Text ====
        status_frame = ttk.Frame(self.root)
        status_frame.pack(side='bottom', fill='both', expand=True, padx=0, pady=(0,2))
        self._set_dark_bg(status_frame)

        self.status_text = tk.Text(
            status_frame, height=3, wrap='word',
            font=("Segoe UI", 10), relief='flat', borderwidth=0,
            bg="#000000", fg="#eeeeee", insertbackground="#eeeeee"
        )
        self.status_text.pack(side='left', fill='both', expand=True, padx=(2,0))
        self.status_text.insert('end', "Status: Waiting for input.")
        self.status_text.config(state='disabled')

        scrollbar = ttk.Scrollbar(status_frame, command=self.status_text.yview)
        scrollbar.pack(side='right', fill='y')
        self.status_text.config(yscrollcommand=scrollbar.set)

        ttk.Button(status_frame, text="Copy Status", width=12, command=self.copy_status).pack(side='right', padx=(6,6), pady=(0,4))

        # ==== FFMPEG check ====
        if not ffmpeg_installed():
            messagebox.showerror("Error", "ffmpeg/ffprobe not found in PATH. Please install ffmpeg to use this tool.")
            self.root.destroy()

    def _set_dark_bg(self, frame):
        try:
            frame.configure(bg="#000000")
        except Exception:
            pass

    def set_status(self, msg):
        self.status_text.config(state='normal')
        self.status_text.delete("1.0", "end")
        self.status_text.insert("end", str(msg))
        self.status_text.config(state='disabled')
        self.status_text.see("end")
        print(msg)
        self.root.update_idletasks()

    def copy_status(self):
        text = self.status_text.get("1.0", "end").strip()
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()  # now it stays on the clipboard after quit

    def select_video(self):
        file_path = filedialog.askopenfilename(filetypes=[("Video files", "*.webm *.mp4")])
        if file_path:
            self.video_path = Path(file_path)
            self.video_label.config(text=f"Selected: {self.video_path.name}")
            self.set_status(f"Ready to process: {self.video_path.name}")

    def run(self):
        if not hasattr(self, 'video_path') or not self.video_path:
            messagebox.showerror("Error", "No video file selected.")
            return
        try:
            self.root.config(cursor="wait")
            if self.mode.get() == "inject":
                self.inject_audio()
            else:
                self.extract_audio()
        except Exception as e:
            import traceback
            self.set_status(f"Error: {e}")
            print(traceback.format_exc())
            messagebox.showerror("Error", str(e))
        finally:
            self.root.config(cursor="")

    def inject_audio(self):
        self.set_status("Injecting audio...")

        sound_url = extract_sound_url(self.video_path.name)
        if not sound_url:
            raise ValueError("No [sound=URL] tag found in filename (case-insensitive).")

        working_dir = get_script_directory()
        audio_ext = Path(urllib.parse.urlparse(sound_url).path).suffix
        downloaded_audio = working_dir / f"__downloaded{audio_ext}"
        converted_audio = working_dir / "__converted.aac"

        temp_output = self.video_path.with_name("__injected.mp4")
        try:
            download_audio(sound_url, downloaded_audio)
            convert_audio_to_aac(downloaded_audio, converted_audio)
            mux_video_with_aac_audio(self.video_path, converted_audio, temp_output)
            clean_stem = re.sub(r'\[sound=.*?\]', '', self.video_path.stem, flags=re.IGNORECASE).strip()
            final_output = self.video_path.with_name(f"{clean_stem}.mp4")
            if final_output.exists():
                overwrite = messagebox.askyesno(
                    "File exists",
                    f"'{final_output.name}' already exists.\nDo you want to overwrite it?"
                )
                if not overwrite:
                    self.set_status("Operation cancelled by user.")
                    temp_output.unlink(missing_ok=True)
                    return
            if not self.preserve_original.get():
                self.video_path.unlink(missing_ok=True)
            temp_output.replace(final_output)
            self.set_status(f"Created: {final_output.name}")
            messagebox.showinfo("Success", f"Created: {final_output.name}")
        finally:
            for f in [downloaded_audio, converted_audio, temp_output]:
                try:
                    if f.exists():
                        f.unlink()
                except Exception:
                    pass

    def extract_audio(self):
        self.set_status("Extracting audio and preparing soundpost...")
        working_dir = get_script_directory()
        temp_audio = working_dir / "__extracted.mp3"
        temp_video = working_dir / "__stripped.mp4"
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4", dir=working_dir) as tf:
            safe_video_path = Path(tf.name)
            shutil.copy2(self.video_path, safe_video_path)
        try:
            extract_audio_ffmpeg(safe_video_path, temp_audio)
            if not temp_audio.exists() or temp_audio.stat().st_size < 1024:
                raise RuntimeError("Audio extraction failed.")
            catbox_url = upload_to_catbox(temp_audio)
            self.set_status(f"Audio uploaded: {catbox_url}")
            compress_and_strip_audio(safe_video_path, temp_video)
            base = self.video_path.stem
            clean_base = re.sub(r'\[sound=.*?\]', '', base, flags=re.IGNORECASE).strip()
            quoted_url = urllib.parse.quote(catbox_url, safe='')
            new_filename = f"{clean_base} [sound={quoted_url}]{self.video_path.suffix}"
            final_output = self.video_path.with_name(new_filename)
            if final_output.exists():
                overwrite = messagebox.askyesno(
                    "File exists",
                    f"'{final_output.name}' already exists.\nDo you want to overwrite it?"
                )
                if not overwrite:
                    self.set_status("Operation cancelled by user.")
                    temp_video.unlink(missing_ok=True)
                    return
            if not self.preserve_original.get():
                self.video_path.unlink(missing_ok=True)
            temp_video.replace(final_output)
            self.set_status(f"Created: {final_output.name}\n[sound] tag points to audio uploaded to Catbox.")
            messagebox.showinfo("Success", f"Created: {final_output.name}\n[sound] tag points to audio uploaded to Catbox.")
        finally:
            for f in [temp_audio, temp_video, safe_video_path]:
                try:
                    if f.exists():
                        f.unlink()
                except Exception:
                    pass

# === Launch GUI ===
if __name__ == "__main__":
    root = ThemedTk(theme="equilux")
    app = SoundpostTool(root)
    root.mainloop()
