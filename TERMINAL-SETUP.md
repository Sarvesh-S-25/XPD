# Getting live usage limits — step by step (Windows)

When this is done, PromptMeter shows the same numbers as Claude's own usage
dialog, updating by itself, covering **all** your usage including Cowork.

Total time: about five minutes. No Node.js, no API key, no payment.

---

## 1. Open PowerShell

Press the **Windows key**, type `powershell`, press **Enter**.

A blue window opens with a prompt like `PS C:\Users\sarve>`.

> If your prompt says `C:\Users\sarve>` **without** the `PS`, you're in CMD, not
> PowerShell. Close it and search for "PowerShell" instead — the command below
> only works in PowerShell.

You do **not** need to run as Administrator.

---

## 2. Install Claude Code

Type this exactly and press Enter:

```powershell
irm https://claude.ai/install.ps1 | iex
```

Wait for it to finish. It downloads a single program — it does not need Node.js
or anything else.

---

## 3. Close PowerShell and open it again

This is not optional. Windows only picks up the new `claude` command in a fresh
window.

Then check it worked:

```powershell
claude --version
```

You should see something like `2.1.233 (Claude Code)`.

> **`claude is not recognized`?** Close the window and open a new one again. If
> it still fails, restart your PC — Windows sometimes needs that to refresh PATH.

---

## 4. Log in

Type:

```powershell
claude
```

First run asks you two things:

1. **A colour theme** — pick any, press Enter.
2. **Login method** — choose **"Claude account with subscription"**.

Your browser opens. Sign in as **sarveshs2025@gmail.com** and approve. Go back to
PowerShell — it will say you're logged in.

---

## 5. Prove the numbers are there

Still inside Claude Code, type:

```
/usage
```

You should see your **Current session** and **Weekly** bars — the same ones as the
app's usage dialog. This costs nothing; it does not use the model.

Seeing them here means PromptMeter can get them too.

Now leave: type `/exit` and press Enter.

---

## 6. Connect PromptMeter

1. Make sure PromptMeter is running — double-click **`start.bat`** if it isn't.
2. Go to the **Setup** page.
3. Under **Live status line**, click **Write the setting**.
4. Click **Test it**. You want the green "It works" box.

If Test it fails saying Python wasn't found, install Python from
[python.org/downloads](https://www.python.org/downloads/) and tick
**"Add python.exe to PATH"** on the first screen of the installer.

---

## 7. Start Claude Code again and send one message

Back in PowerShell:

```powershell
claude
```

Type anything — `hi` will do — and press Enter.

Two things happen:

- A **usage bar appears at the bottom** of the Claude Code window.
- PromptMeter's Windows page turns **green** with your real percentages.

The one message is needed because Claude Code only receives the limit numbers
after its first reply in a session.

---

## Done. What you have now

- **Live limits**, matching Claude's own dialog, updating continuously.
- **Covers everything** — Cowork, browser chat, the desktop app, the terminal.
  The limits are one shared pool, so one reading covers them all.
- **Costs nothing.** The status line is a local script; it makes no API calls.

---

## Day to day

Whenever a Claude Code terminal session is open, PromptMeter updates on its own.
When no session is running the numbers keep ticking from the last reading — the
countdowns run down, and the bars reset themselves when a window rolls over.

If you've done a lot of Cowork or browser work and want to re-sync immediately,
either open a terminal session for a moment, or use **Sync from Claude** on the
Windows page.

---

## If something goes wrong

| What you see | What to do |
|---|---|
| `irm ... is not recognized` | You're in CMD, not PowerShell. Open PowerShell. |
| `claude is not recognized` | Close and reopen PowerShell. If it persists, restart the PC. |
| Test it says Python not found | Reinstall Python, ticking **Add python.exe to PATH**. |
| No bar at the bottom of Claude Code | You started `claude` before writing the setting. Exit with `/exit` and start it again. |
| App still yellow after a message | Press **Check again** on Setup. Confirm `/usage` shows bars inside Claude Code. |
| Want to undo it | **Undo** on the Setup page, or `python -m promptmeter --disconnect`. Your other settings are untouched. |

Run `claude doctor` in PowerShell for a health check of the install itself.
