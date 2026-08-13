# Bundled FFmpeg

Place FFmpeg command-line binaries here so CapsWriter can extract audio from
MP4/MOV/MKV/MP3/WAV and other media files without requiring users to edit PATH.

Expected layout:

```text
tools/
  ffmpeg/
    bin/
      ffmpeg.exe
      ffprobe.exe
```

Use an LGPL-compatible Windows build when distributing CapsWriter, and keep the
FFmpeg license files/notices with the release package.

Recommended sources:

- Official FFmpeg download page: https://ffmpeg.org/download.html
- Windows builds linked by FFmpeg, such as gyan.dev or BtbN builds

