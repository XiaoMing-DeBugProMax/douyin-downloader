# FFmpeg distribution notice

This application distributes `ffmpeg.exe`, `ffprobe.exe`, and their required shared libraries as separate programs for local media inspection and audio stream copy.

- Supplier build: BtbN/FFmpeg-Builds `autobuild-2026-08-01-13-21`
- FFmpeg identity: `n8.1.2-34-g9b6c8969e0`
- FFmpeg source commit: `9b6c8969e05b4f0b29f0f85cd501be6b3e582e6b`
- Build-script source commit: `a99e8230eae00d1cee38f23076a7a1f55cd984e2`
- Binary archive and SHA-256: recorded in `manifest.json`
- Final executable/DLL hashes, version output, and build configuration: generated as `audit.json` during packaging

The selected build is the supplier's LGPL variant. Packaging rejects builds whose reported configuration contains `--enable-gpl` or `--enable-nonfree`. The license files supplied with the pinned binary archive are included with the application under `third-party/ffmpeg/licenses`.

Corresponding source and build scripts are available from the exact immutable URLs in `manifest.json`. No project patch is applied to FFmpeg or the supplier build scripts.

FFmpeg is a trademark of Fabrice Bellard, originator of the FFmpeg project. This notice is not legal advice.
