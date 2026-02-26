# ytplaylist_record.py

Realtime YouTube playlist audio recorder. Plays each track through your speakers via `mpv` while simultaneously recording from your PipeWire/PulseAudio monitor, auto-splitting and naming files from playlist metadata.

## Requirements

```bash
sudo apt install mpv ffmpeg
pip install yt-dlp --break-system-packages
```

## Usage

```bash
python3 ytplaylist_record.py <playlist_url> [output_dir]
```

**Example:**
```bash
python3 ytplaylist_record.py "https://www.youtube.com/playlist?list=XXXXXXX" /mnt/data/Media/Music/trucker
```

If `output_dir` is omitted, files are saved to the current directory.

## Adding to Your Repo

```bash
cp ytplaylist_record.py ~/path/to/Home_Automation/
git add ytplaylist_record.py
git commit -m "Add YouTube playlist realtime audio recorder"
git push
```

## Notes

- Files are named automatically as `01 - Track Name.mp3`
- Already-recorded files are skipped on re-run
- Press `Ctrl+C` to stop early safely
