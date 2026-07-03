# YT Downloader v7

## First-time setup (after cloning) and Running

The bundled Python runtime, ffmpeg, and yt-dlp are **not** committed to
this repo (too large for git). Run setup once:

```
start.bat
```

This downloads Python 3.13 (embeddable), installs the packages in
`requirements.txt`, and fetches ffmpeg — takes a few minutes, needs
internet access. Safe to re-run; it skips anything already present.

Opens the dashboard at http://127.0.0.1:5000

## Notes

- Downloads are saved to `<your Windows Downloads folder>/YT Downloader/`
- Cookie sync (for age-restricted/private videos) is optional — use the
  browser extension in `extension/`, not required for normal use.
