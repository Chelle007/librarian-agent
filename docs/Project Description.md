**Project: Personal AI Assistant**
 
**Context**
Building a personal AI assistant using Hermes Agent + Obsidian as the knowledge backend. Main interface is Telegram (text + voice). Goal is a second brain + life assistant tailored for daily personal use.
 
**About the User**
- Y3 CS student at SIM majoring Computer Science, graduating soon
- Building this as a personal tool and portfolio piece
 
**Stack**
- Agent framework: Hermes Agent (by Nous Research, open-source, self-improving)
- LLM: Gemini Flash (model-agnostic via Hermes, swap anytime)
- Knowledge base: Obsidian (markdown vault; Librarian reads/writes files directly on disk, no REST API — Obsidian is an optional GUI viewer, synced desktop↔VPS via git)
- Interface: Telegram bot (primary)
- Voice: Telegram voice message → transcribed → handled as text input
- Hosting: Hetzner CX22 VPS (~$4.59/mo, no lock-in)
- Obsidian sync to VPS: git (obsidian-git on desktop + VPS auto-commit/pull)
 
**Use Cases**
 
🔔 Reminders (core)
- General one-off reminders → Telegram notification
- Urgent reminders → phone alarm (stretch goal)
- Daily habit reminders → Telegram (recurring)
- Friend birthday reminders → Telegram
 
🧠 Second Brain (core)
- Quick idea/info dump → AI cleans up, picks folder & tags, auto-saves to Obsidian
- Auto-save with Telegram notification, revertable/editable via reply
 
👥 Friends CRM (core)
- Store friend profiles (name, likes, socials, events)
- Birthday tracking → feeds into reminders
- Flashcard quiz ("who's that?") → Telegram only for now
 
🎙️ Voice (core)
- Telegram voice message → transcribed → handled as text input
 
✅ Task Tracker (low priority)
- Add/manage tasks via Telegram
- Reminders for due tasks
- Saved and tracked in Obsidian
 
🌱 Personal Growth (lowest priority)
 
- Wins journal → log wins anytime → saved to Obsidian
- What I learned → quick learning dumps → saved to Obsidian
- Achievements tracker → milestones saved to Obsidian
 
**Decisions Made**
- Platform: Telegram only for now (no Discord)
- Save behavior: auto-save, no confirmation needed, editable/revertable via reply
- Quiz platform: Telegram only for now
- LLM: Gemini Flash (convenience + quality over privacy)
- VPS: Hetzner CX22 over Hostinger (flat pricing, no lock-in, same specs)
- Obsidian integration: direct file I/O on the markdown vault + git sync (superseded earlier Local REST API + MCP plan — no live Obsidian instance on the headless VPS to talk to)
 
**Build progress (July 2026)**

| Stage | Status |
|---|---|
| Stage 1 — Librarian baseline (vault I/O, schema, CLI) | Done |
| Stage 2 — Retrieval, classification, agent, eval/benchmark | Core done |
| Stage 3 — Hermes PA, Telegram, MCP server | Not started |

**Next Steps**
- [x] Design system architecture → `docs/Architecture (7 July).md`
- [x] Librarian Stage 1 + Stage 2 core (`librarian-agent` repo)
- [ ] Set up Hetzner VPS
- [ ] Install Hermes Agent
- [ ] Connect Gemini Flash as LLM
- [ ] Package Librarian as MCP server (`librarian_handle`, `librarian_confirm`, `librarian_query_raw`)
- [ ] Connect Telegram bot
- [ ] Build PA features (reminders, habits, CRM) on top of Librarian tools
