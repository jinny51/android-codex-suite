---
name: akbs-doc-submit
description: "Prepare, check, submit, or revise an independent technical document in AKBS. Uses the actual document file and the existing member identity; excludes reports, Patch/App accompanying explanations, knowledge publication, and source development."
---

# AKBS Doc Submit

Use this entry for a real, independently delivered technical document: an analysis,
operation guide, method, interface reference, or reviewed reference material.
Doc does not require Git, LincHub, Android source access, a chip platform, or an
engineering plugin. A report saying a document was written is not the original.

Patch README/build/verification/rollback material remains part of its Patch.
App SDK/README maintenance remains part of that App unless the actual task is an
independent document delivery; never count the same work again by renaming a file.
This Skill does not award points or publish knowledge.

## Prepare and check

Read the requested file, preserve its actual author and outcome date, and state its
purpose and known limitations. Facts, analysis and unknowns must remain distinct;
a useful analysis need not claim that the underlying product problem is fixed.
Do not invent content, verification, authorship, or a completed use of the document.

The entry uses the member plugin's existing identity, version checks, endpoint,
HTTP error handling and managed artifact location. A version update still requires
the returned Codex restart instruction. Do not create another config or credentials.
The shared `preflight-install-family` check runs before business reads or writes;
continue only with `status=PASS`. It does not require the engineering plugin.

```bash
python3 "scripts/akbs_doc_submit.py" --profile <existing-profile> --prepare \
  --file /path/to/document.docx --title "文档标题" \
  --summary "文档解决的问题和用途" --date YYYY-MM-DD

python3 "scripts/akbs_doc_submit.py" --validate /path/to/prepared-document
```

Preparation copies the original into the governed local artifact output; it does
not modify the user's source file or upload anything. Validation is read-only and
does not fetch updates. Read the reported concrete file/field error before retrying;
do not replace a missing original with a daily/weekly report paragraph.

## Submit or revise

Only submit when the user requested submission. Ordinary members use:

```bash
python3 "scripts/akbs_doc_submit.py" --profile <existing-profile> \
  --submit-latest --run-id <prepared-run-id>
```

Keep the prepared bytes and original run identity when retrying an uncertain
submission. A requested document revision updates the existing document identity,
not a renamed new document. Member submission or revision does not directly
overwrite an already reviewed knowledge conclusion.

```bash
# For an actual revision, take this identity and revision from the server record.
python3 "scripts/akbs_doc_submit.py" --profile <existing-profile> --prepare \
  --file /path/to/revised-document.docx --title "文档标题" --summary "本次修订用途" \
  --date YYYY-MM-DD --document-id <document_id> --expected-revision <revision>
```

If the server reports that another revision already exists, inspect that record
before preparing against its revision; do not change the expected number merely
to force an overwrite. Keep uncertain-upload retries on the same prepared run.

For an administrator's own document, reuse local preparation and continue through
the available management Skill's authenticated Doc path in this same conversation.
Do not probe administrator credentials for an ordinary member, fall back to member
upload after an administrator identity failure, or ask the administrator to interact
with themselves in the UI. A prepare-only request never authorizes that continuation.

Uploading preserves the material; the management Skill separately reviews and
publishes supported knowledge. Search and read the reviewed result through
`akbs-knowledge-search`, including its exact document original. Do not turn a
download, a search hit, or a proposed use into a successful reuse claim.
