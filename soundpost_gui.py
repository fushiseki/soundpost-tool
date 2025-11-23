import mimetypes
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import requests
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from ttkthemes import ThemedTk

# ==== Configuration ====
DEFAULT_TARGET_MB = 4
DEFAULT_CONTAINER = "mp4"
CATBOX_UPLOAD_URL = "https://catbox.moe/user/api.php"
REQUEST_TIMEOUT = 30
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024  # 200MB safety cap
SUPPORTED_VIDEO_TYPES = {"video/mp4", "video/webm"}
SUPPORTED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}


# ==== Utility helpers ====
def detect_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or ""


def ensure_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        subprocess.run(["ffprobe", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def run_ffmpeg(args):
    subprocess.run(args, check=True)


def probe_duration(path: Path) -> float:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        raise RuntimeError("Could not determine media duration.")


def safe_tempdir() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix="soundpost_")


def extract_sound_url(filename: str) -> str | None:
    match = re.search(r"\[sound\s*=\s*(.*?)\]", filename, flags=re.IGNORECASE)
    if match:
        url = urllib.parse.unquote(match.group(1).strip())
        if not url.startswith("http"):
            url = "https://" + url
        return url
    return None


def download_audio(url: str, dest_path: Path):
    with requests.get(url, stream=True, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"}) as resp:
        if not resp.ok:
            raise RuntimeError(f"Failed to download audio ({resp.status_code}).")
        content_length = resp.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_DOWNLOAD_BYTES:
            raise RuntimeError("Audio file too large to download safely.")
        written = 0
        with dest_path.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if chunk:
                    fh.write(chunk)
                    written += len(chunk)
                    if written > MAX_DOWNLOAD_BYTES:
                        raise RuntimeError("Download exceeded safety limit.")
    mime = resp.headers.get("Content-Type", "")
    if not (mime.startswith("audio/") or mime == "application/octet-stream"):
        raise RuntimeError(f"Unexpected content type: {mime}")
    if dest_path.stat().st_size < 1024:
        raise RuntimeError("Downloaded audio is unexpectedly small.")


def upload_to_catbox(audio_path: Path) -> str:
    with audio_path.open("rb") as f:
        resp = requests.post(
            CATBOX_UPLOAD_URL,
            files={"fileToUpload": f},
            data={"reqtype": "fileupload"},
            timeout=REQUEST_TIMEOUT,
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Catbox upload failed ({resp.status_code}).")
    body = resp.text.strip()
    if not body.startswith("http"):
        raise RuntimeError("Catbox returned an unexpected response.")
    return body


def convert_audio_to_aac(input_audio: Path, output_audio: Path):
    run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_audio),
            "-vn",
            "-acodec",
            "aac",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-b:a",
            "192k",
            str(output_audio),
        ]
    )


def convert_audio_to_opus(input_audio: Path, output_audio: Path):
    run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_audio),
            "-vn",
            "-c:a",
            "libopus",
            "-b:a",
            "160k",
            str(output_audio),
        ]
    )


def build_video_from_image(image_path: Path, audio_path: Path, output_path: Path, container: str):
    duration = probe_duration(audio_path)
    video_codec = "libx264" if container == "mp4" else "libvpx-vp9"
    run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-framerate",
            "2",
            "-t",
            str(duration),
            "-i",
            str(image_path),
            "-i",
            str(audio_path),
            "-c:v",
            video_codec,
            "-tune",
            "stillimage",
            "-c:a",
            "copy",
            "-shortest",
            str(output_path),
        ]
    )


def mux_video_and_audio(video_path: Path, audio_path: Path, output_path: Path, container: str):
    # Always re-encode to avoid container mismatches and to normalize streams.
    video_codec = "libx264" if container == "mp4" else "libvpx-vp9"
    audio_codec = "aac" if container == "mp4" else "libopus"
    run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            video_codec,
            "-c:a",
            audio_codec,
            "-shortest",
            str(output_path),
        ]
    )


def strip_audio_and_compress(video_path: Path, output_path: Path, container: str, target_mb: int):
    video_codec = "libx264" if container == "mp4" else "libvpx-vp9"
    def encode_crf(crf_value: int):
        args = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-an",
            "-c:v",
            video_codec,
            "-crf",
            str(crf_value),
            "-preset",
            "fast",
        ]
        if container == "mp4":
            args.extend(["-movflags", "+faststart"])
        args.append(str(output_path))
        run_ffmpeg(args)

    def encode_bitrate(bitrate: int):
        # Constrain the maxrate/bufsize to keep the output under the requested cap.
        args = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-an",
            "-c:v",
            video_codec,
            "-b:v",
            str(bitrate),
            "-maxrate",
            str(bitrate),
            "-bufsize",
            str(bitrate * 2),
            "-preset",
            "fast",
        ]
        if container == "mp4":
            args.extend(["-movflags", "+faststart"])
        args.append(str(output_path))
        run_ffmpeg(args)

    max_bytes = target_mb * 1024 * 1024
    encode_crf(30)
    if output_path.stat().st_size <= max_bytes:
        return

    duration = probe_duration(video_path)
    if duration <= 0:
        raise RuntimeError("Could not determine video duration for size targeting.")

    target_bitrate = int((target_mb * 8 * 1024 * 1024) / duration)
    target_bitrate = max(120_000, min(target_bitrate, 1_500_000))

    # Try up to three bitrate passes, tightening bitrate if we overshoot.
    bitrate = int(target_bitrate * 0.94)  # leave headroom for container overhead
    for _ in range(3):
        encode_bitrate(bitrate)
        size = output_path.stat().st_size
        if size <= max_bytes:
            return
        # Scale bitrate down based on how much we exceeded the cap.
        reduction = max_bytes / size
        bitrate = max(120_000, int(bitrate * reduction * 0.9))

    raise RuntimeError("Compressed video still exceeds the target size.")


def clean_stem(name: str) -> str:
    return re.sub(r"\[sound=.*?\]", "", name, flags=re.IGNORECASE).strip()


@dataclass
class JobConfig:
    source_path: Path
    mode: str  # "inject" or "extract"
    preserve_original: bool
    target_mb: int
    container: str


# ==== Application ====
class SoundpostApp:
    def __init__(self, root: ThemedTk):
        self.root = root
        self.ui_thread = threading.current_thread()
        self.root.title("4chan Soundpost Tool")
        self.root.geometry("640x560")
        self.root.minsize(600, 520)
        try:
            self.root.set_theme("equilux")
        except Exception:
            pass

        self.video_path: Path | None = None
        self._build_ui()

        if not ensure_ffmpeg():
            messagebox.showerror("ffmpeg missing", "ffmpeg/ffprobe not found in PATH. Please install them to continue.")
            self.root.destroy()

    # ---- UI setup ----
    def _build_ui(self):
        style = ttk.Style()
        style.configure("TFrame", background="#1e1e1e")
        style.configure("TLabelframe", background="#1e1e1e", foreground="#f0f0f0")
        style.configure("TLabelframe.Label", background="#1e1e1e", foreground="#f0f0f0")
        style.configure("TLabel", background="#1e1e1e", foreground="#f0f0f0")
        style.configure("TRadiobutton", background="#1e1e1e", foreground="#f0f0f0")
        style.configure("TCheckbutton", background="#1e1e1e", foreground="#f0f0f0")
        style.configure("TButton", padding=6)
        style.configure("TSpinbox", arrowsize=12)
        style.configure("TCombobox", arrowsize=12)

        root_frame = ttk.Frame(self.root)
        root_frame.pack(fill="both", expand=True)

        file_frame = ttk.LabelFrame(root_frame, text="Step 1: Choose a source")
        file_frame.pack(fill="x", padx=16, pady=(16, 8))

        file_inner = ttk.Frame(file_frame)
        file_inner.pack(fill="x", padx=8, pady=4)

        self.video_label = ttk.Label(file_inner, text="No file selected.")
        self.video_label.grid(row=0, column=0, sticky="w")

        ttk.Button(file_inner, text="Browse...", command=self.select_source).grid(row=0, column=1, sticky="e", padx=(8, 0))
        file_inner.columnconfigure(0, weight=1)

        options_frame = ttk.LabelFrame(root_frame, text="Step 2: Choose action and options")
        options_frame.pack(fill="x", padx=16, pady=8)

        self.mode = tk.StringVar(value="inject")
        mode_frame = ttk.Frame(options_frame)
        mode_frame.pack(fill="x", padx=8, pady=(6, 4))
        ttk.Radiobutton(mode_frame, text="Inject [sound] audio into file", variable=self.mode, value="inject").grid(
            row=0, column=0, sticky="w", pady=2
        )
        ttk.Radiobutton(
            mode_frame,
            text="Extract audio, upload, and tag file",
            variable=self.mode,
            value="extract",
        ).grid(row=1, column=0, sticky="w", pady=2)

        config_frame = ttk.Frame(options_frame)
        config_frame.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(config_frame, text="Target max file size (MB):").grid(row=0, column=0, sticky="w")
        self.target_mb = tk.IntVar(value=DEFAULT_TARGET_MB)
        ttk.Spinbox(config_frame, from_=1, to=100, width=6, textvariable=self.target_mb).grid(
            row=0, column=1, sticky="w", padx=(8, 0)
        )
        ttk.Label(config_frame, text="Output container:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.container = tk.StringVar(value=DEFAULT_CONTAINER)
        ttk.Combobox(
            config_frame, values=["mp4", "webm"], textvariable=self.container, width=8, state="readonly"
        ).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        config_frame.columnconfigure(0, weight=1)

        self.preserve_original = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="Preserve original file", variable=self.preserve_original).pack(
            anchor="w", padx=8, pady=(6, 4)
        )

        action_frame = ttk.Frame(root_frame)
        action_frame.pack(fill="x", padx=16, pady=(8, 8))
        ttk.Button(action_frame, text="Run", width=14, command=self.run).pack(side="left", padx=(0, 8))
        ttk.Button(action_frame, text="Quit", width=14, command=self.root.quit).pack(side="left")

        ttk.Separator(root_frame, orient="horizontal").pack(fill="x", pady=(2, 0))

        status_frame = ttk.LabelFrame(root_frame, text="Status")
        status_frame.pack(side="bottom", fill="both", expand=True, padx=16, pady=(8, 12))

        log_container = ttk.Frame(status_frame)
        log_container.pack(fill="both", expand=True, padx=6, pady=4)

        self.status_text = tk.Text(
            log_container,
            height=10,
            wrap="word",
            font=("Segoe UI", 10),
            relief="flat",
            borderwidth=0,
            bg="#111",
            fg="#eee",
            insertbackground="#eee",
        )
        self.status_text.grid(row=0, column=0, sticky="nsew")
        self.status_text.insert("end", "Status log will appear here.\n")
        self.status_text.config(state="disabled")

        scrollbar = ttk.Scrollbar(log_container, command=self.status_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.status_text.config(yscrollcommand=scrollbar.set)
        log_container.columnconfigure(0, weight=1)
        log_container.rowconfigure(0, weight=1)

        ttk.Button(status_frame, text="Copy Status", width=14, command=self.copy_status).pack(
            anchor="e", padx=8, pady=(0, 4)
        )

    # ---- UI helpers ----
    def log(self, message: str):
        def append():
            self.status_text.config(state="normal")
            self.status_text.insert("end", message + "\n")
            self.status_text.config(state="disabled")
            self.status_text.see("end")
            print(message)

        self._call_in_ui(append)

    def copy_status(self):
        text = self.status_text.get("1.0", "end").strip()
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()

    def select_source(self):
        file_path = filedialog.askopenfilename(
            filetypes=[
                ("Supported", "*.mp4 *.webm *.png *.jpg *.jpeg *.webp *.mp3 *.wav *.ogg *.m4a *.flac"),
                ("All files", "*.*"),
            ]
        )
        if file_path:
            self.video_path = Path(file_path)
            self.video_label.config(text=f"Selected: {self.video_path.name}")
            self.log(f"Ready: {self.video_path.name}")

    def run(self):
        if not self.video_path:
            messagebox.showerror("Error", "No file selected.")
            return
        config = JobConfig(
            source_path=self.video_path,
            mode=self.mode.get(),
            preserve_original=self.preserve_original.get(),
            target_mb=max(1, int(self.target_mb.get() or DEFAULT_TARGET_MB)),
            container=self.container.get(),
        )
        threading.Thread(target=self._run_job, args=(config,), daemon=True).start()

    def _run_job(self, config: JobConfig):
        try:
            self._set_busy(True)
            self.log("Starting job...")
            if config.mode == "inject":
                self._process_inject(config)
            else:
                self._process_extract(config)
            self.log("Job completed.")
        except Exception as exc:
            self.log(f"Error: {exc}")
            self._show_error(str(exc))
        finally:
            self._set_busy(False)

    def _set_busy(self, busy: bool):
        def toggle():
            cursor = "wait" if busy else ""
            self.root.config(cursor=cursor)
            for child in self.root.winfo_children():
                try:
                    child.configure(state="disabled" if busy else "normal")
                except tk.TclError:
                    pass
            self.root.update_idletasks()

        self._call_in_ui(toggle, wait=True)

    def _call_in_ui(self, func, wait: bool = False):
        if threading.current_thread() is self.ui_thread:
            return func()
        if wait:
            done = threading.Event()
            result = {}

            def wrapper():
                result["value"] = func()
                done.set()

            self.root.after(0, wrapper)
            done.wait()
            return result.get("value")
        self.root.after(0, func)

    def _show_info(self, message: str):
        self._call_in_ui(lambda: messagebox.showinfo("Success", message))

    def _show_error(self, message: str):
        self._call_in_ui(lambda: messagebox.showerror("Error", message))

    def _confirm_overwrite(self, path: Path) -> bool:
        return bool(
            self._call_in_ui(lambda: messagebox.askyesno("File exists", f"'{path.name}' exists. Overwrite?"), wait=True)
        )

    # ---- Processing ----
    def _process_inject(self, config: JobConfig):
        sound_url = extract_sound_url(config.source_path.name)
        if not sound_url:
            raise RuntimeError("No [sound=URL] tag found in filename.")

        mime = detect_mime(config.source_path)
        is_video = mime in SUPPORTED_VIDEO_TYPES
        is_image = mime in SUPPORTED_IMAGE_TYPES
        if not (is_video or is_image):
            raise RuntimeError("Source must be a video or image when injecting audio.")
        self.log(f"Injecting audio into {'video' if is_video else 'image'}: {config.source_path.name}")

        with safe_tempdir() as temp_dir:
            tmpdir = Path(temp_dir)
            downloaded_audio = tmpdir / "downloaded_audio"
            converted_audio = tmpdir / ("audio.aac" if config.container == "mp4" else "audio.opus")
            self.log(f"Downloading audio from: {sound_url}")
            download_audio(sound_url, downloaded_audio)
            self.log("Converting audio to match container...")
            if config.container == "mp4":
                convert_audio_to_aac(downloaded_audio, converted_audio)
            else:
                convert_audio_to_opus(downloaded_audio, converted_audio)

            output_suffix = f".{config.container}"
            final_stem = clean_stem(config.source_path.stem)
            final_output = config.source_path.with_name(f"{final_stem}{output_suffix}")

            temp_output = tmpdir / f"output{output_suffix}"
            if is_image:
                self.log("Rendering still image to video with audio...")
                build_video_from_image(config.source_path, converted_audio, temp_output, config.container)
            else:
                self.log("Muxing audio and video with normalization...")
                mux_video_and_audio(config.source_path, converted_audio, temp_output, config.container)

            if final_output.exists():
                if not self._confirm_overwrite(final_output):
                    self.log("Operation cancelled: existing file not overwritten.")
                    return
            if not config.preserve_original and config.source_path.exists():
                config.source_path.unlink()
            shutil.move(str(temp_output), str(final_output))
            self.log(f"Created: {final_output.name}")
            self._show_info(f"Created: {final_output.name}")

    def _process_extract(self, config: JobConfig):
        mime = detect_mime(config.source_path)
        if mime not in SUPPORTED_VIDEO_TYPES:
            raise RuntimeError("Extraction mode requires a video file.")
        self.log(f"Extracting audio from: {config.source_path.name}")

        with safe_tempdir() as temp_dir:
            tmpdir = Path(temp_dir)
            audio_out = tmpdir / "extracted.mp3"
            silent_video = tmpdir / f"silent.{config.container}"

            self.log("Running ffmpeg to extract audio...")
            run_ffmpeg(["ffmpeg", "-y", "-i", str(config.source_path), "-vn", "-acodec", "libmp3lame", str(audio_out)])
            if not audio_out.exists() or audio_out.stat().st_size < 1024:
                raise RuntimeError("Audio extraction failed.")

            self.log("Uploading audio to Catbox...")
            catbox_url = upload_to_catbox(audio_out)
            self.log(f"Audio uploaded: {catbox_url}")

            self.log("Stripping audio and recompressing video to target size...")
            strip_audio_and_compress(config.source_path, silent_video, config.container, config.target_mb)

            base_name = clean_stem(config.source_path.stem)
            quoted_url = urllib.parse.quote(catbox_url, safe="")
            new_name = f"{base_name} [sound={quoted_url}].{config.container}"
            final_output = config.source_path.with_name(new_name)

            if final_output.exists():
                if not self._confirm_overwrite(final_output):
                    self.log("Operation cancelled: existing file not overwritten.")
                    return
            if not config.preserve_original and config.source_path.exists():
                config.source_path.unlink()
            shutil.move(str(silent_video), str(final_output))
            self.log(f"Created: {final_output.name}\n[sound] tag points to uploaded audio.")
            self._show_info(f"Created: {final_output.name}")


def main():
    if not ensure_ffmpeg():
        print("ffmpeg/ffprobe not found in PATH. Install them before running the GUI.")
        sys.exit(1)
    root = ThemedTk(theme="equilux")
    app = SoundpostApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
