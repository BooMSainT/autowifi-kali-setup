# AutoWIFI Kali setup

A small helper to install the local `~/AutoWIFI` checkout with pipx and optional MCP dependencies.

## On Kali

```bash
git clone https://github.com/BooMSainT/autowifi-kali-setup.git
cd autowifi-kali-setup
bash setup.sh
```

The script checks for `~/AutoWIFI`, asks before changing its ownership, installs using `pipx install --force '.[mcp]'`, and checks the exposed executables. Review the script before running it. Do not run it as root.
