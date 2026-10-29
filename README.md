# AutoWIFI Kali setup

Installs the local `~/AutoWIFI` checkout with pipx and provides an optional local AI interface through Ollama and MCPHost.

## Install AutoWIFI

```bash
git clone https://github.com/BooMSainT/autowifi-kali-setup.git
cd autowifi-kali-setup
bash setup.sh
```

Review the script before running it. Do not run it as root.

## Install the optimized local AI

```bash
bash setup-ai.sh
autowifi-ai
```

The setup creates an `autowifi-fast` Ollama profile based on `qwen2.5:1.5b` with:

- a 4096-token context for MCP tool definitions and normal conversation;
- a 256-token response limit to prevent slow, wandering answers;
- low temperature for more direct tool decisions;
- an explicit 30-minute warm-up on every launch, even when Ollama was already running.

The first launch still has to load the model into memory. Later prompts during the keep-alive window should avoid that load delay.

## Check performance

```bash
autowifi-ai-doctor
```

The diagnostic reports CPU threads, memory, loaded Ollama models, and the time for a small warm response. It does not invoke AutoWIFI tools or touch a wireless interface.

## Change the balance

Use the larger model for better answers on a faster machine:

```bash
ollama pull qwen2.5:3b
AUTOWIFI_AI_MODEL=qwen2.5:3b autowifi-ai
```

Rebuild the optimized profile with different limits:

```bash
AUTOWIFI_AI_CONTEXT=3072 AUTOWIFI_AI_MAX_TOKENS=192 bash setup-ai.sh
```

Lower values are faster and use less memory, but overly small limits can reduce tool reliability. The default profile is the recommended balance for a CPU-only Kali laptop.

Use AutoWIFI only on networks you own or have explicit permission to test.
