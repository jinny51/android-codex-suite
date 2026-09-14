# Android orchestration extension v1

This contract lets `android-engineering-ops` select one optional orchestration Skill.
No configuration means core-direct operation. `mode = "jinny"` selects the official
Jinny implementation; `mode = "custom"` selects an installed plugin by name.

Every extension plugin stores this file at:

```text
contracts/android-orchestration-extension/v1/extension.json
```

Minimal custom extension manifest:

```json
{
  "schema": "android-orchestration-extension-v1",
  "provider_id": "my-android-orchestrator",
  "provider_version": "1.0.0",
  "compatible_core_contracts": ["android-engineering-orchestration-v1"],
  "orchestrator": {"skill_id": "my-android-orchestrator"}
}
```

`provider_id` and `provider_version` are the extension plugin identity and must match
its plugin manifest. The Skill must be
present at `skills/<skill_id>/SKILL.md` with matching frontmatter and an
`agents/openai.yaml` default prompt that names `$<skill_id>`.

The contract does not prescribe roles, models, task counts, side-effect ceilings, or
worker documents. A selected extension is trusted orchestration instruction. Android
domain policy, source authority, build/deploy safety, and patch capture still come from
the applicable `android-engineering-ops` Skills.
