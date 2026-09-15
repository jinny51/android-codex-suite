---
name: jinny-android-coding-practices
description: "Use when an Android change is being implemented under the optional Jinny practices extension and the task needs Jinny naming, helper-organization, or review conventions. Apply these as recommendations without weakening the core Android change policy or project rules."
---

# Jinny Android Coding Practices

Apply these conventions only where they fit the existing project. They are additive:
the core Android change policy, repository instructions, public API compatibility,
generated code ownership, and established local style always take precedence.

- helper methods may use a suffix derived from the resolved `member_alias`;
- two or more feature helpers may be grouped in a same-package alias-derived `Utils` type;
- review or project conventions must be concrete and non-conflicting.

Never hardcode an example person's alias. Do not rename existing APIs or reorganize a
working subsystem merely to match these preferences. In review, distinguish mandatory
policy findings from optional Jinny style suggestions.
