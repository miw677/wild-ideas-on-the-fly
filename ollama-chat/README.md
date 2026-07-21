# Terminal 719

A retro CRT-terminal chatbot web interface for a locally running
[Ollama](https://ollama.com) instance. Everything runs on your machine —
no API keys, no cloud, no npm install. Just Python 3 and a browser.

![architecture](#) <!-- browser ⇄ server.py (proxy) ⇄ Ollama :11434 -->

## Features

- 💬 Streaming responses — tokens appear as the model generates them
- 📝 TUI-style markdown: headings, bold/italic, lists, quotes and rules
  rendered with phosphor brightness, underline and box-drawing — the
  type never leaves the terminal grid (toggle in settings)
- 🧮 Terminal math: LaTeX fragments ($T+1$, \frac, Greek, sub/superscripts)
  are normalized to plain Unicode and shown on the amber phosphor;
  untranslatable expressions stay verbatim
- 🔄 Model picker — switches between any model you've pulled (`ollama list`)
- 🧠 Multi-turn conversations with a bounded memory window
- ⚙️ Settings panel — system prompt, temperature, memory length
- ⏹ Stop button — abort a generation mid-stream
- 📊 Per-reply stats — elapsed time and true generation tokens/sec
- 🖥 Old-school CRT look: green phosphor glow, scanlines, screen
  flicker/jitter, a slow roll bar, and an ASCII-art boot screen
- ⌨️ [VT323](https://fonts.google.com/specimen/VT323) — a recreation of
  the DEC VT320 terminal font — embedded in the page as base64 (OFL
  license), so the app needs no network access for anything
- 🈶 CJK text renders in [Fusion Pixel](https://github.com/TakWolf/fusion-pixel-font)
  (OFL), a bitmap-style Chinese/Japanese/Korean font that matches
  VT323's pixel look; served locally and fetched only when CJK
  characters appear, with size-adjusted system fonts as fallback
- 🔢 Names itself after today's date (7/21 → TERMINAL 721): boots under
  its original designation 719, then two seconds in, Matrix-style
  decodes the logo, header and tab title to the current date
  (`?n=NNNN` in the URL forces a specific number)
- ♿ All CRT motion effects respect `prefers-reduced-motion`

## Built for small local models

The request sent to Ollama is as barebones as it gets — just the user
messages (plus your system prompt, only if you set one). No tools, no
extra context. On top of that:

- **Warm start** — the model is preloaded the moment you open the page
  or switch models, and `keep_alive: 30m` keeps it in memory between
  messages, so replies start without the model-load pause.
- **Thinking disabled** — for models that support reasoning/thinking,
  `think: false` is sent so they answer immediately (checked per model
  via `/api/show`; models without the capability are left alone).
- **Bounded memory** — only the last N turns (default 8, adjustable in
  Settings) are re-sent each message. Prompt processing is the slow part
  on CPU, and it grows with history length — capping it keeps later
  turns as fast as the first.
- **Frame-batched rendering** — the UI repaints at most once per frame
  during streaming, so a fast token stream is never throttled by DOM
  work.

## Requirements

- Python 3.8+ (standard library only)
- Ollama running locally with at least one model pulled:

  ```bash
  ollama pull llama3.2      # or any model you like
  ```

## Run it

```bash
cd ollama-chat
python3 server.py
```

Then open <http://localhost:8080> in your browser.

Options:

```bash
python3 server.py --port 9000                          # different UI port
python3 server.py --ollama http://localhost:11434      # different Ollama URL
```

## How it works

```
browser ── HTTP ──> server.py ── proxy ──> Ollama (localhost:11434)
          (UI + fetch)        (/api/tags, /api/chat)
```

`server.py` serves the single-page UI and forwards `/api/*` calls to
Ollama, relaying the newline-delimited JSON stream chunk by chunk. Going
through the proxy keeps the browser same-origin with the API, so you
don't need to touch `OLLAMA_ORIGINS`/CORS settings.

The UI keeps the conversation in memory only (cleared on refresh or
"New chat"); the selected model is remembered in `localStorage`.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Red status dot / "Ollama not reachable" | Start Ollama (`ollama serve` or the desktop app). The UI retries automatically. |
| Model dropdown says "no models" | Pull one: `ollama pull llama3.2` |
| Slow first reply | The model is being loaded into memory; later replies are faster. |
| Port already in use | `python3 server.py --port 9000` |
