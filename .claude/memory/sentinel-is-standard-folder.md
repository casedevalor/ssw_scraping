---
name: sentinel-is-standard-folder
description: Since 2026-09-27 the SSW_SENTINEL folder is the user's standard project folder ("pasta padrão"), not a test bed
metadata:
  type: project
---

As of 2026-09-27 the SSW_SENTINEL project folder became the user's standard project folder ("pasta padrão"). Before that, it was only a test bed whose changes were carried by hand to a separate standard folder.

**Why:** The user told me directly. Nothing was carried over by hand: this folder simply became the reference version.

**How to apply:** Don't tell the user to copy changes "to the standard folder" any more; this is it. Hand-off instructions at the end of a change now target only **each company's copy** (which may carry company-specific code, so still give copy lists plus small by-hand diffs). Related: [[column-sentinel-design]], [[per-range-function-shape]].
