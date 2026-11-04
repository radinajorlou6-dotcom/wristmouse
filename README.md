# WristMouse

Control a Windows laptop with a **Galaxy Watch 8** without touching anything: tilt to move the cursor, pinch to click, flick your wrist to scroll, and talk to type. It works over **Wi-Fi or Bluetooth**.

## Download

From the [latest release](../../releases/latest), download **`WristMouse-Windows.zip`**, unzip it anywhere, and run `WristMouse.exe`.
The watch app is inside it. Install it from the laptop app's **Install on watch** tab (one-time setup, about 5 minutes).

## Gestures

| Do this | Get this |
|---|---|
| Turn palm up, hold ~1 s | Pause / resume the mouse (watch buzzes once = on, twice = paused) |
| Point / swing your forearm | Move the cursor (works palm-down or sideways, no setup) |
| **Table mode:** slide your hand on the desk | Move the cursor like a real mouse: forward = up, back = down. Switch with the button at the top of the window or say "table mode" / "air mode" |
| Pinch thumb + index finger | Left click |
| Double pinch | Right click |
| Raise the watch to your mouth (hold ½ s) | Voice typing. Talk, then lower your arm and it types (triple pinch is an option in settings) |
| Quick flick down / up (or wrist twist, if you pick that) | Scroll down / up |

Pause the mouse (palm up) before you put your arm down or pick something up.

A small label next to the cursor shows each action as it happens (Left click, Scroll down ×3, Listening…), so you don't need vibration to know a pinch registered. You can move it to the corner or turn it off on the Gestures tab.

### Voice commands
Anything you say is typed. If the whole phrase is a command, it runs instead:
`enter`, `backspace`, `delete`, `tab`, `escape`, `select all`, `copy`, `paste`, `cut`, `undo`, `redo`, `save`, `find`,
`delete that` (removes what was just typed), `new tab`, `close tab`, `next tab`, `go back`, `refresh`, `switch window`,
`minimize`, `show desktop`, `click`, `right click`, `double click`, `scroll up`, `scroll down`, `pause mouse`, `cancel`.
Say `type copy` to type the word "copy".

Speech recognition runs **offline on the laptop** (Whisper). The model downloads once the first time you start the app.

## One-time watch install

1. Watch: **Settings → About watch → Software information** → tap **Software version** about 7 times until Developer mode turns on.
2. Watch: **Settings → Developer options** → turn on **ADB debugging** and **Wireless debugging**. The watch and laptop must be on the same Wi-Fi.
3. Laptop app → **Install on watch** tab. On the watch, open **Wireless debugging → Pair new device** and type the pairing IP:port + code. Then type the IP:port from the main Wireless debugging screen into **Connect address**, and click **Install**.

The app opens on the watch with all permissions granted. After that it runs in the background, even with the screen off. It also restarts by itself after the watch reboots (open the app once after a reboot to re-enable voice).

## Setting it up for your hand

All tuning is on the laptop, under the **Gestures** tab:
- Cursor movement follows your forearm relative to the room (gravity-aligned, like a laser pointer), so it doesn't depend on how your wrist is turned. If up/down is backwards (watch worn with the buttons toward your elbow), tick **Reverse up/down**.
- **Calibrate pinch**: pinch 5 times and it picks the right sensitivity.
- Sliders for cursor speed, up/down speed, acceleration, flick strength, scroll amount and double-pinch speed. You can also scroll by twisting the wrist instead of flicking.

## Connection notes

- **Automatic** mode uses Wi-Fi when the watch can reach the laptop, otherwise Bluetooth LE. No pairing needed in either case.
- Campus networks (eduroam) usually block devices from talking to each other, so Wi-Fi won't connect there. Bluetooth still works, or put both on your phone's hotspot.
- The first time, Windows asks to allow WristMouse through the firewall. Click **Allow**. You can redo it from the **Connection** tab.

## How it works

```
watch/   Wear OS app (Kotlin, no dependencies). Streams accelerometer + gyroscope at 100 Hz,
         streams the mic on request, vibrates on command. Links: TCP over Wi-Fi (found via UDP
         broadcast on port 47321) or a BLE GATT server.
pc/      Windows app (Python). All gesture recognition, Whisper speech-to-text, mouse/keyboard
         output via SendInput, settings window, and the watch installer (adb).
```

The line protocol is documented at the top of `pc/wristmouse/hub.py`. Every push to `main` builds both apps in GitHub Actions, runs the tests plus a self-test of the packaged exe, and publishes a release.

Run from source: `cd pc && pip install -r requirements.txt && python run.py`. Tests: `python -m pytest tests`.
