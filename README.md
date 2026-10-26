# fl2000-re

Userspace reverse-engineering probe (Python) for the **Hagibis USB Display Adapter** on Apple
Silicon. The dongle is a **Fresco Logic FL2000DX** (`1d5c:2000`) plus an **IT66121** HDMI
transmitter — not DisplayLink, and not a native extra macOS display.

## What already works on a MacBook Air M2

- register reads over USB vendor control (`bRequest` 64/65)
- IT66121 on I2C `0x4C` (vendor `0x4954`, device `0x612`)
- EDID from the HDMI sink
- color bars **640×480 @ 60 fps** (RGB565, USB 2.0)
- **extend a real extra macOS desktop** (private `CGVirtualDisplay`), then pump that screen to the
  Hagibis. Drag windows onto the display named **Hagibis**. Both Hagibis HDMIs still show the same
  image.
- **1440×900 @ 60 Hz** (Dell P2016 native) and **1920×1080** / **1920×1080@30** timings from
  Fresco's official `big_table_16bit_r0`. All need a SuperSpeed link. Through the HDMI→VGA adapter
  they lock but flicker at high pixel clocks. The LaunchAgent currently tests **1024×768 at about 50
  Hz** (54 MHz) after the original 65 MHz profile showed partial USB timeouts; the P2016 OSD rejects
  1080p outright.
- **mirror** the Mac screen onto HDMI at **720×480 @ 60 Hz** RGB565 (CEA 480p 16:9). 640×480 4:3
  stretched on the Dell; 800×600 RGB332 never locked.

Plug **one** HDMI cable into the Hagibis. One FL2000 chip = one output.

## Run

```bash
./bin/setup
.venv/bin/python hagibis_re.py dump
.venv/bin/python hagibis_re.py detect
.venv/bin/python hagibis_re.py edid
.venv/bin/python hagibis_re.py bars --seconds 12
.venv/bin/python hagibis_re.py bars --seconds 12 --mode 1440x900
.venv/bin/python hagibis_re.py mirror --seconds 0
.venv/bin/python hagibis_re.py extend --seconds 0 --mode 1440x900 --underscan 1.0
.venv/bin/python hagibis_re.py extend --seconds 0 --mode 1024x768 --underscan 1.0 --virtual 1280x960
```

`--mode` is per command: `mirror`/`extend` default to 720×480, `bars` to 640×480. Native modes need
`--underscan 1.0` (the 0.92 default is analog overscan compensation).

`--virtual WxH` draws the desktop larger and downscales it to the HDMI mode: `1280x960` → `1024x768`
gives a ~20% smaller UI at the stable 65 MHz clock. Keep the same aspect as the mode, or the fit
letterboxes (1280×800 → 1024×768 leaves bars top and bottom). The LaunchAgent uses 1280x960.

Capture follows screen motion as well as the pointer: changing more than 8% of a sampled frame keeps
video active even with a parked mouse. During motion, full-raster resizing uses macOS Accelerate
vImage scaling and sharpening. Other platforms, unavailable native acceleration, letterboxing and
underscan use the BILINEAR/SHARPEN fallback. After 0.5 s without significant changes, the text
LANCZOS/unsharp filter returns. Identical captures reuse fitted and packed pixels. HDMI continues
receiving frames at the mode refresh, including repeated frames. This is adaptive capture, not a
guarantee of 60 distinct video frames per second.

`mirror` clones the Air. `extend` creates a WindowServer display you can drag windows onto, then
clones **that** display to HDMI. `CGVirtualDisplay` is a private API and can break on a macOS
update. Run `extend` from Terminal.app with Screen Recording permission.

The LaunchAgent skips the blocking `screencapture` preview at startup; the isolated capture child
fills the seeded black frame. Manual `extend` keeps the preview by default. This removes the two
possible 5-second preview waits from automatic recovery, but does not repair a stalled USB link.

A local profile file at `~/Library/Application Support/fl2000-re/video-mode` selects any named HDMI
mode without code edits; for example, `640x480` is the conservative 25.2 MHz/36.9 MB/s stability
profile. The virtual desktop stays 1280x960, so fallback trades image detail for lower link demand.
Without a local profile, the LaunchAgent uses `1024x768@50`.

The prior XGA experiment used `1024x768@50`: the same HDMI raster and 1280x960 virtual desktop at a
54 MHz pixel clock (49.849 Hz actual). This reduces raw traffic from about 94 MB/s to 79 MB/s. It is
a custom timing, not a standard VESA DMT mode; monitor compatibility and long-term stability remain
under validation. The original `--mode 1024x768` remains 60 Hz/65 MHz. A 256 KiB chunking experiment
still timed out and was removed from the active transport.

Each session logs a run ID, commit, runtime source/helper hash, HDMI timing, frame/block sizes and
USB speed/bus/address. Session-end records share the same ID. The source hash also changes for
uncommitted runtime edits, so source changes can be distinguished from plain service restarts.

Every USB write is checked for its exact byte count. PyUSB/libusb1 can return a partial byte count
on timeout without raising an exception; such a frame is rejected and never terminated as though it
were complete. EOF failures are also propagated instead of being hidden. Transfers taking 100 ms or
more emit `usb_frame_slow` records with separate pixel/EOF timings; failures include the phase and
expected/actual bytes when available. No failed transfer is retried. A stalled endpoint requires
unplugging and reconnecting the Hagibis USB-A; higher capture fps does not repair a partial
transfer.

The USB feeder requests interactive thread scheduling on macOS after starting the capture child
(which keeps nice(5)). Host pacing includes 5% scheduling headroom while HDMI timing registers
remain unchanged; every frame still ends with its mandatory ZLP. Startup logs whether interactive
thread priority was accepted and the feed margin. This reduces sensitivity to scheduler delays but
does not guarantee a stable physical HDMI/VGA signal.

Wake detection compares two monotonic macOS clocks (one advances during sleep, the other does not),
so a wall-clock correction cannot be mistaken for waking. Interrupted clock sampling is ignored. A
real detected wake logs the measured gap. Each session also logs the first successful USB frame and
the first live captured frame, including their elapsed times from pump startup. These events
describe software delivery; they do not measure when the physical monitor locks.

An isolated low-priority observer samples REG_STATUS, the PLL register and IT66121 status 0x0E once
per second after mode setup. It never calls SET_CONFIGURATION or writes video mode/bank registers.
It logs input-video stability (bit 4), HPD/RxSense, chip scanout fps and status changes immediately,
plus a heartbeat every ten seconds. Its I2C/USB reads stay outside the bulk feeder; observer errors
are logged without stopping video. The observer is stopped before USB teardown. These fields cannot
observe the analog image downstream of an HDMI→VGA adapter. HPD/RxSense were already unreliable on
this dongle, so unchanged values do not prove the physical screen stayed on.

The live log separates capture, scaling and packing costs, reports the requested and budget-limited
capture rates, and counts delivered captures (`novas`) separately from USB repeats (`repetidas`).
`novas` counts consumed capture generations, including captures of unchanged content; it does not
measure distinct video frames. `pausa-max` and `atrasos` expose send gaps over 1.5 refresh periods;
`USB-max` measures the longest write including its ZLP, while `idade-max` measures age since the
capture call started, excluding compositor and monitor latency. Stage costs and targets are the
latest sample, while USB counts and maxima cover the previous reporting interval. `cache sim` means
unchanged raw pixels reused the previous fitted image. The log also identifies the native or Pillow
filter and any compositor backoff. Log rotation preserves launchd's open stdout file.

Code lives in `fl2000_re/` (one module per concern). `hagibis_re.py` is the CLI shim. Agent rules:
`AGENTS.md`.

## Dev: one command

```bash
make check
```

That is ruff + pytest + Prettier. CI runs the same on every push/PR.

To isolate observer interference during physical diagnostics, set `FL2000_LINK_WATCH=0` in the
feeder environment. This disables the separate EP0/I2C polling process and logs
`hdmi_link_watch_disabled`; bulk validation, capture and session logs remain active. The default is
enabled. Reading bridge status still writes the chip I2C command register, so an isolated process
does not guarantee zero effect on the device. This switch is a diagnostic control, not a confirmed
repair for USB timeouts.

Capture counters and approximate timing/idle values have a single writer and use shared raw scalars.
Reading them in the USB loop never waits for a capture-process lock. Complete pixel publication
still uses the separate nonblocking frame lock: if the writer is busy or suspended, the feeder
repeats its last complete frame. Telemetry is approximate and does not synchronize pixel ownership.
This removes one software starvation path; physical USB timeouts still require long-running hardware
validation.

On macOS, the USB feeder owns a Foundation activity with user-initiated and latency-critical options
for the duration of streaming. Thread QoS alone does not opt the process out of App Nap or timer/I/O
deferral. The activity ends on normal exit, interruption or USB failure; idle system sleep remains
allowed. A small Objective-C bridge is compiled once into the user cache, outside the frame loop;
unavailable tooling/API logs a nonfatal event. This follows
[Apple's activity guidance](https://developer.apple.com/documentation/foundation/processinfo) and
does not guarantee that a physical USB endpoint will remain responsive.

The single-packet-burst experiment disables duplicate EP1 on control interface 0 (alternate 0) and
selects streaming interface 1 alternate 1. On the observed adapter its SuperSpeed companion has
`bMaxBurst=0`; interface 0 alternate 1 and interface 1 alternate 0 advertise `bMaxBurst=15`. Pixels
still use bulk OUT endpoint 1 and explicit EOF, with the same timing and wire format. The experiment
also claims/releases streaming interface 1. This tests host/burst interaction; the descriptor
difference alone does not establish a cause of the earlier partial timeouts.

Selection is restricted to macOS SuperSpeed devices whose IF1/alt1 bulk EP1 includes the complete
single-packet SuperSpeed companion descriptor. Other platforms, USB 2 and unsupported/missing
descriptors keep the original control-interface route.

The persistent fault listener (`python -m fl2000_re.fault_listener`) follows the extend log and
writes `~/Library/Logs/fl2000-re-faults.jsonl`. Each captured USB failure records its run/source,
twenty recent HDMI states and eight transport-stat lines, then queries the persisted macOS kernel
window for the same device/endpoint. It never opens or configures USB. Unlike a live `log stream`
alone, this also recovers kernel records that were not delivered to the live stream.

The follower reassembles partial UTF-8 writes and handles rename/copy-truncate rotation. Existing
records seed context without replaying old faults. Application evidence is written before the
bounded kernel query, so query failure cannot erase the fault. `listener_plist()` renders the
per-user KeepAlive LaunchAgent `com.fl2000-re.usb-listener`. A running listener confirms captured
transport failures; it cannot directly inspect the analog image after HDMI→VGA. An opt-in
`usb-wire-codec` profile in `~/Library/Application Support/fl2000-re/` accepts `raw` (default) or
`rle24`. The latter groups identical RGB565 pixels in an independently implemented FL2000 RGB888/RLE
stream. It preserves all original RGB565 bits and the selected resolution; it does not apply
approximate color masks. Static frames reuse the encoded payload. Every payload still ends with an
explicit zero-length transfer. Encoding may increase traffic for high-entropy images, and hardware
stability is experimental. Compressed frames use the actual PLL raster period to avoid accumulating
a queue of tiny repeated frames. Return the profile to `raw` and restart the agent to restore the
original transport.

For clone/extend, RLE encoding runs inside the existing isolated capture child. The USB feeder
receives tagged, complete compressed payloads through a bounded shared buffer sized for the
worst-case RGB888/RLE expansion. Length, content and generation publish under the same lock; the
feeder only tries that lock without waiting and keeps its previous complete payload if the writer is
busy. Encoding, scaling or capture stalls therefore do not block repeated USB frames or their
mandatory EOF. The initial frame is encoded once before the timed pump starts.
