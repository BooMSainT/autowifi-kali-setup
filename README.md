# AutoWIFI Kali setup

Installs the local `~/AutoWIFI` checkout with pipx and provides an optional local AI interface through Ollama and MCPHost.

## Install AutoWIFI

```bash
git clone https://github.com/BooMSainT/autowifi-kali-setup.git
cd autowifi-kali-setup
bash setup.sh
```

Review the script before running it. Do not run it as root.

## Install the local AI

```bash
bash setup-ai.sh
autowifi-ai
```

The default model is `qwen2.5:1.5b`, selected for faster responses on CPU-only Kali laptops. The setup also keeps the model loaded longer and limits the Ollama context to reduce repeated startup and prompt-processing delays.

For better answers on a faster machine, install and select the larger model:

```bash
ollama pull qwen2.5:3b
AUTOWIFI_AI_MODEL=qwen2.5:3b autowifi-ai
```

You can select any already-installed Ollama model with `AUTOWIFI_AI_MODEL`. The launcher checks that the chosen model exists before starting.

Use AutoWIFI only on networks you own or have explicit permission to test.
