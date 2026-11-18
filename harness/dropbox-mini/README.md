# DropBox's assistant harness (mini)

A copy of the assistant code the assistant and family-job benches drive (harness v3.1, 2026-11-18).
DropBox is a private team chat app; mini is its local assistant. `dropbox_bench.py` runs this file
against a stand-in DropBox server, so the harness tested is the one that ships.

What it does for each chat request:

1. **Routes** it (optional): simple rules first, then a one-word CHAT/TOOLS question to a small model.
2. **Runs one turn of the [pi](https://github.com/badlogic/pi-mono) agent** on an Ollama model, inside a
   macOS sandbox (`mini-sandbox.sb`) that can read and write only the asker's files and reach only this Mac.
3. **Gives the model house rules** (`rules()`): search and read before answering, do the job yourself,
   ask before deleting, vague "clean up" means list and ask, totals with a tool, reply in the asker's language,
   terse style.
4. **Checks the turn** (`run_pi()`): a file it was asked to save is missing; it says it changed something but
   no file changed; or it answered about the person's files without reading them. A failed check gets one
   more turn with the problem spelled out.

Configuration comes from environment variables (`DROPBOX_MINI_*`): model, tool model, extensions, timeout.
It needs a DropBox server to talk to, so it does not run on its own; read it for the prompts and checks.
