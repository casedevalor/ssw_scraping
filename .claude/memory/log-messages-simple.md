---
name: log-messages-simple
description: "User wants SSW log lines short, one line, objective — no paths, no library noise, no restating rules"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 9c0ead35-5358-4035-b9da-b32146ed035c
  modified: 2026-09-25T17:08:21.890Z
---

Log messages in the SSW bots must be one line, simple and objective: company + tag first (e.g. `RPV SENTINEL | 002 | file | ...`), only the fact and what to fix. No file paths when the source is obvious, no Pydantic/Playwright multi-line dumps, no restating the rule ("positions must run 1..N").

**Why:** the domain log is uploaded to SharePoint and read by people checking many companies; the user trimmed every verbose message during the column-sentinel work (2026-09-25).

**How to apply:** when adding or changing any `log.log(...)` message, keep it to one line with `|` separators and the minimum needed to act. See [[column-sentinel-design]].
