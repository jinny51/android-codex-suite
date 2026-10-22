# AKBS Doc Submit

`akbs-doc-submit` handles real, independent technical documents, not report text or
the README/evidence accompanying a Patch. Documents are submitted directly to AKBS;
Git, LincHub and Android source access are not prerequisites.

## Typical requests

- “把这份技术分析文档提交到 AKBS。”
- “先生成这份操作指南的提交材料，不上传。”
- “把这份文档的新版本提交到原记录。”

The member plugin reuses its existing profile, update check, endpoint and HTTP error
handling. It preserves the original file and actual author/outcome date in managed
local artifacts before an explicitly requested upload.

```bash
python3 skills/akbs-doc-submit/scripts/akbs_doc_submit.py \
  --profile <existing-profile> --prepare --file /path/to/guide.docx \
  --title "操作指南" --summary "适用范围和解决的问题" --date YYYY-MM-DD

python3 skills/akbs-doc-submit/scripts/akbs_doc_submit.py \
  --validate /path/to/prepared-document

python3 skills/akbs-doc-submit/scripts/akbs_doc_submit.py \
  --profile <existing-profile> --submit-latest --run-id <prepared-run-id>
```

Prepare-only and validation do not upload. A successful upload saves a document;
it does not by itself publish knowledge or award contribution points. The existing
management Skill owns Doc review and publication. An authenticated administrator
can prepare and complete their own document in the same conversation, without a
self-review UI handoff.

Approved document knowledge is found through `akbs-knowledge-search`, with its
purpose, limitations and accurate original. Revisions keep the same document
identity and retain the earlier reviewed version. Repeated commits, filenames,
pages, or report mentions do not create extra document contributions.
