# Android Change Policy v1

`policy.json` is the canonical, machine-readable policy. This README explains the
boundary; it does not define a second copy of the rules.

This is an internal shared module, not a public Skill. `android-change-workflow` reads
and applies it before source edits, while `android-patch-capture` verifies it through
`lib/android_engineering_ops/policy/patch_markers.py`. Members do not select or run a
separate policy step. The stable `policy_id` remains in generated evidence so existing
captures and historical material stay readable.

The policy has three layers:

1. Patch attribution applies to every Android change archived as patches.
2. Framework logging, debug-property, resource, and feature-material rules apply only
   when `component.layer=platform` and `component.type=framework`. The legacy
   `change_domain=framework` route first normalizes to that component; it is not a new
   domain authority.
3. Historical Jinny helper-name and utility-class suffix conventions remain available
   only as a compatibility advisory. They are not universal Android engineering rules.

For new Codex-authored source, Codex resolves `member_alias` from the current member
profile and creates paired markers while implementing the change:

```text
//<member_alias> <yyyyMMdd>@{
...
//<member_alias> <yyyyMMdd>@}
```

Markers are standalone slash-comment lines. For current Codex changes, every nonblank
added line in an applicable file/hunk is inside a matching pair; pairs never cross a
file or diff hunk. Context, blank lines, explicit generated output, and file types
without the slash-comment adapter are outside this check.

`--profile` may select an existing profile, and a configured default profile may be
used. There is deliberately no free-form alias argument or environment-variable alias
override: the selected profile's `member_alias` is the identity.

Even a one-line custom change uses the pair. This exact `//` form applies only where
slash line comments are valid; XML, shell, properties, and other syntaxes must not be
broken by blindly inserting it. A future comment-style adapter must preserve the same
identity/date/pairing semantics before those files receive direct markers.

No example person's alias is a policy value. Git author, an invented secondary alias,
or a fabricated ticket number must not replace the current profile identity.
Historical and external patches keep their original authorship and are assessed as
imported material instead of being rewritten.

For implementation origin, current Codex work uses the selected member's paired
markers. Mixed work preserves historical markers and adds a current-member pair only
around the newly authored part. Manual, external, and historical imports preserve the
original author evidence and are never made compliant by rewriting attribution.
