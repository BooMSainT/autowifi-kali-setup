# AutoWIFI Kali setup

Installs a local `~/AutoWIFI` checkout with pipx and adds an optional local AI operator through Ollama and MCPHost. The complete AutoWIFI MCP toolset remains available.

Use AutoWIFI only on networks you own or have explicit permission to test.

## Install AutoWIFI

Clone the AutoWIFI source and this setup repository, then run the local setup script:

```bash
git clone https://github.com/momenbasel/AutoWIFI.git ~/AutoWIFI
git clone https://github.com/BooMSainT/autowifi-kali-setup.git
cd autowifi-kali-setup
bash setup.sh
```

Review the scripts before running them. Do not run them as root. The setup fixes AutoWIFI's current MCP stdio and wordlist-discovery compatibility issues before installing it with pipx.

The setup also installs `nmap` and adds two MCP tools for real LAN host discovery:

- `list_lan_interfaces` lists adapters and connected IPv4 networks;
- `discover_lan_devices` discovers responding computers, phones, printers, and other hosts on a directly connected private subnet.

Interfaces such as `wlan0` are reported as adapters. AutoWIFI does not label an interface as a discovered device. Device types remain `unknown` unless a hostname provides evidence.

## Add the Kali wordlist

This optional helper installs Kali's `wordlists` package and prepares an uncompressed, user-owned copy of `rockyou.txt`:

```bash
bash setup-wordlists.sh
bash setup.sh
```

The prepared file is stored at:

```text
~/.local/share/autowifi/wordlists/rockyou.txt
```

Running `setup.sh` again makes the installed AutoWIFI MCP server discover that path. You can also provide any readable, uncompressed wordlist directly to an autonomous run.

## Install the optimized local AI

```bash
bash setup-ai.sh
autowifi-ai
```

The setup creates an `autowifi-fast` Ollama profile based on `qwen2.5:1.5b` with:

- a 4096-token context for MCP tool definitions and conversation;
- a 256-token response limit for direct tool decisions;
- low temperature for repeatable choices;
- a 30-minute model keep-alive on launch;
- a system prompt that preflights, verifies artifacts, retries one sensible correction, restores monitor mode, and reports results.

The first launch still loads the model into memory. Later prompts during the keep-alive window should avoid that delay.

When you launch `autowifi-ai` or `autowifi-auto`, it asks for your Kali password once. MCPHost and Ollama continue under your normal account; the AutoWIFI MCP process receives elevated access so monitor mode and wireless tools can work.

If you previously saw a monitor-mode or agent-creation failure, refresh the generated configuration:

```bash
cd ~/autowifi-kali-setup
git pull
bash setup-ai.sh
autowifi-ai-doctor
autowifi-ai
```

## Discover devices on your LAN

### Reliable code words

Start the direct command console:

```bash
autowifi-console
```

Then tell it what you want in normal language:

```text
scan nearby devices and sort them by power
use wlan2 for monitor mode and keep wlan0 managed
show devices connected to my LAN
capture a handshake for MyWiFi
show my wordlists
do it
quit
```

The console recognizes common operational requests and runs the matching AutoWIFI command immediately. It remembers the last successful action, so `do it`, `run it`, or `go ahead` repeats it. Known actions bypass the language model, which prevents the model from merely printing example JSON or describing what it intends to do. Open-ended requests automatically fall back to the local AI operator. Type `chat` to enter its full interactive mode.

Each action opens in a separate Airgeddon-style terminal window. That window shows the exact command, sudo prompt, live tool output, elapsed-time updates, final status, and saved log path. It remains open until you press Enter, while the main `autowifi-console` stays available for another request.

Common scans, interface changes, LAN discovery, capture verification, wordlist discovery, and exact-target WPA handshake capture use deterministic AutoWIFI workflows instead of waiting for the local model to choose tools. Every task also has a hard deadline and is stopped with an explicit timeout result instead of running indefinitely.

The sudo password prompt appears inside the task window where the privileged command runs. Linux does not display characters or asterisks while the password is entered. Task logs are saved under `~/.local/share/autowifi/tasks/`.

Active capture and password-testing requests require one exact authorized SSID, BSSID, capture file, or lab target. Broad requests such as “capture every network in range” are rejected instead of being misread as a target name.

Short code words still work, including `nearby`, `devices`, `networks`, `interfaces`, `wordlists`, `monitor wlan2`, `managed wlan0`, and `radios wlan2 wlan0`.

You can also run each code directly from the terminal.

Nearby wireless clients sorted by observed signal power:

```bash
autowifi-nearby
```

Restrict it to one authorized SSID or BSSID:

```bash
autowifi-nearby "MyWiFi / AA:BB:CC:DD:EE:FF"
```

Discover active hosts on your connected private LAN:

```bash
autowifi-devices
```

`autowifi-ai` remains available for freeform requests and longer autonomous workflows. Small local models can sometimes answer with text instead of invoking a tool, so use `autowifi-console` or `autowifi-quick` when you need a direct scan result.

For the complete shortcut list:

```bash
autowifi-codes
```

Run any shortcut directly from the terminal:

```bash
autowifi-quick networks
autowifi-quick clients "MyWiFi / AA:BB:CC:DD:EE:FF"
autowifi-quick audit "MyWiFi / AA:BB:CC:DD:EE:FF"
autowifi-quick handshake "MyWiFi / AA:BB:CC:DD:EE:FF"
autowifi-quick wordlists
autowifi-quick reports
```

Available codes are `check`, `interfaces`, `networks`, `nearby`, `devices`, `clients`, `audit`, `handshake`, `pmkid`, `wps`, `deauth5`, `wordlists`, `monitor`, `managed`, `radios`, `verify`, `crack`, `restore`, `reports`, and `health`. Codes that perform a targeted assessment require an exact authorized SSID, BSSID, capture file, or interface.

Start the interactive operator:

```bash
autowifi-ai
```

Then ask:

```text
List my connected LAN interfaces, discover active devices on my directly connected private network, and show IP, MAC, hostname, vendor and evidence-based device type. Do not call an interface a device and do not guess missing fields.
```

This uses a bounded `nmap -sn` host-discovery scan. It accepts only a directly connected private IPv4 subnet and limits one run to 4096 addresses.

## Run one autonomous assessment

The autonomous command requires a scope, exact target, objective, and allowed actions. Those values remain hard boundaries for the run.

```bash
autowifi-auto \
  --scope "my home lab network" \
  --target "LabNet / AA:BB:CC:DD:EE:FF" \
  --objective "verify a captured handshake and test it with my wordlist" \
  --allow "monitor,scan,capture,verify,wordlist,crack,report" \
  --wordlist auto
```

Use an exact wordlist instead of automatic discovery:

```bash
autowifi-auto \
  --scope "my home lab network" \
  --target "LabNet / AA:BB:CC:DD:EE:FF" \
  --objective "test the verified capture with the supplied candidates" \
  --allow "monitor,scan,capture,verify,crack,report" \
  --wordlist "$HOME/my-wordlists/lab-passwords.txt"
```

Available action names are:

```text
dependencies,interfaces,monitor,scan,devices,recommend,capture,pmkid,wps,deauth,wordlist,crack,verify,report,all
```

With `--wordlist auto`, the agent calls AutoWIFI's `find_wordlists` tool before a crack action and selects an uncompressed discovered list. It prefers a smaller practical list first to avoid a long, unnecessary run. Compressed `.gz` files are rejected until prepared by `setup-wordlists.sh`.

Each autonomous run saves its output under:

```text
~/.local/share/autowifi/reports/
```

See all options with:

```bash
autowifi-auto --help
```

## Flipper Zero and Pwn-device hardware status (optional)

The AI setup now installs `autowifi-hardware`, a **read-only** hardware bridge.
It never sends RF/IR commands, injects wireless traffic, or runs arbitrary
commands on attached devices. It does not require either device to be present.

Install or refresh:

```bash
cd ~/autowifi-kali-setup
git pull
bash setup-ai.sh
sudo apt install python3-serial   # optional; enables Flipper USB status reads
```

Connect the Flipper Zero by USB, close qFlipper, and run:

```bash
autowifi-hardware discover
autowifi-hardware status
autowifi-quick hardware
autowifi-console
# Console example: show my Flipper Zero status
```

The bridge detects Flipper USB serial paths in `/dev/serial/by-id/` and uses
the official `info device` CLI command at 230400 baud. If serial permissions
are denied, ensure your Kali user has access to the port (typically group
`dialout`; log out and back in after changing group membership).

**Pwn device:** "Pwn Father" has not been mapped to a specific hardware
model. Only a Linux-based device that **you administer**, supports SSH, and
already has SSH keys and a trusted host key configured can be checked with
the generic adapter. No SSH target is searched for or guessed. Specify it
only when its address and login are known:

```bash
export AUTOWIFI_PWN_HOST=192.168.1.50   # replace with YOUR device IP
export AUTOWIFI_PWN_USER=pi              # replace with YOUR SSH login
ssh "$AUTOWIFI_PWN_USER@$AUTOWIFI_PWN_HOST"  # trust host key yourself first
autowifi-hardware status --json
```

The read-only SSH check runs `uname -s && uptime -p` with batch-mode and
strict host-key verification. No SSH password or private key is stored in
this repository. These environment variables are **not** persisted by setup.

Monitor locally and optionally save private JSONL snapshots:

```bash
autowifi-hardware watch --interval 15 --cycles 3 --record
# Log: ~/.local/share/autowifi/hardware/events.jsonl (mode 0600)
```

The output is independent of wireless monitor mode and does not change
interfaces. The Flipper's device information may contain device identifiers;
keep local hardware logs private. A protocol-specific Pwn Father integration
can be added once its precise model is identified.

## DSTIKE ESP8266 Deauther (Spacehuhn 2.6.1) — one command

**Plug the DSTIKE into Kali with a micro-USB data cable first.** In the
existing Kali setup repository, paste just this line:

```bash
cd ~/autowifi-kali-setup && git pull && bash setup-deauther.sh
```

The installer installs only `python3-serial` and `usbutils`, deploys
`autowifi-deauther`, and immediately tests the USB serial connection at
115200 baud. It does **not** update the operating system, flash firmware, or
transmit deauthentication frames. Run it as your usual user (sudo is used only
for installing packages). If the device is absent, setup still finishes and
prints the next diagnostic steps.

Afterward, these commands work without rerunning setup:

```bash
autowifi-deauther detect             # list serial USB ports and USB details
autowifi-deauther status             # check Spacehuhn firmware using info/sysinfo
autowifi-deauther cached-aps         # read existing results; does not initiate a scan
autowifi-deauther listen --seconds 30 --record   # collect available serial output
```

If more than one USB serial device is present, explicitly identify the
deauther after reviewing `detect` output:

```bash
autowifi-deauther status --port /dev/ttyUSB0
```

If you see **permission_denied**, add your user to the serial port group:

```bash
sudo usermod -aG dialout "$USER"
```

Log out of Kali and back in to activate the group membership. If Kali
reports **no USB serial ports**, try a known working **data-capable** micro-USB
cable, another USB port, and `lsusb`. The device may power its screen over a
charge-only cable while providing no serial connection.

The `cached-aps` command reads already-stored access-point observations,
not a new scan. `listen` only captures unsolicited firmware serial output,
so a quiet device may legitimately return an empty result. Both are
read-only, and the bridge refuses arbitrary serial commands. With
`--record`, JSONL logs are stored with permissions 0600 under
`~/.local/share/autowifi/deauther/events.jsonl`. Treat recorded SSIDs and
MAC addresses as private.

The existing `autowifi-quick deauther` and `autowifi-console` commands
are refreshed automatically by `bash setup-deauther.sh` when already installed.
Examples inside `autowifi-console`:

```text
deauther status
deauther detect USB
deauther cached access points
deauther listen for 30 seconds record
```

The console will explicitly show CLI help for requests such as
`deauther scan for 30 seconds`, because this **read-only** bridge does not
start radio scans. Use the DSTIKE's own Scan menu to collect new observations,
then request its cached results in Kali. The console does not require a
sudo password for read-only deauther/hardware checks; USB permissions may
still need the dialout group as described above.

Firmware command references: [official serial command documentation](https://docs.spacehuhn.com/deauther/usage/serial-commands/).

## Check performance

```bash
autowifi-ai-doctor
```

The diagnostic reports CPU threads, memory, loaded Ollama models, and the time for a small warm response. It does not invoke AutoWIFI tools or touch a wireless interface.

## Change the speed and quality balance

Use the larger model for better decisions on a faster machine:

```bash
ollama pull qwen2.5:3b
AUTOWIFI_AI_MODEL=qwen2.5:3b autowifi-ai
```

Rebuild the optimized profile with different limits:

```bash
AUTOWIFI_AI_CONTEXT=3072 AUTOWIFI_AI_MAX_TOKENS=192 bash setup-ai.sh
```

Lower values are faster and use less memory. Values that are too small can reduce tool reliability. The default profile is the recommended balance for a CPU-only Kali laptop.
