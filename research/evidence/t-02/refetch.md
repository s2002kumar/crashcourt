# Refetch of the clause sources

`crashcourt clauses check --refetch` downloads every cited document, compares its hash with the clause's
`snapshot_sha256`, checks that each quote is still in the document, and checks that each `source_url` fragment is
an element id on the page the URL names (headings and explicit anchors alike). Drift is reported, never fatal: it is
a prompt to re-read the source. Whether a verdict's clause still matches its snapshot is checked per verdict once
the checker exists; this file is the registry-level check.

## Before: the clause files as recorded on 2026-09-27

```text
date: 2026-10-08T21:34:16Z
clause files: as recorded on 2026-09-27 (git archive 9a27cc0 clauses), checked with this branch's refetch
$ uv run crashcourt clauses check --refetch --dir <the 9a27cc0 clauses>
clauses: 9 clauses valid in <the 9a27cc0 clauses>
DRIFT  LG-DE-RERUN   sha256 0d56d51135d7c9359d12b382d75a01384c123cf16d74e5048965e3818ad8dbd0 != recorded dc75ac9e28efbd99f22fa377ec670bf8dd1abda6f794e77235f09ae8bbbaa5e4; re-read https://docs.langchain.com/oss/python/langgraph/graph-api.md
DRIFT  LG-DUR-ASYNC  sha256 3881beeb186af756c6f5495c89effa821fd654957c63584341b91861e6677b6a != recorded 4a73f776970a6e5294d9e4c6ab4956164a78e807a352abacf2a70b2e7fd212c4; re-read https://docs.langchain.com/oss/python/langgraph/checkpointers.md
DRIFT  LG-DUR-EXIT   sha256 3881beeb186af756c6f5495c89effa821fd654957c63584341b91861e6677b6a != recorded 4a73f776970a6e5294d9e4c6ab4956164a78e807a352abacf2a70b2e7fd212c4; re-read https://docs.langchain.com/oss/python/langgraph/checkpointers.md
DRIFT  LG-DUR-SYNC   sha256 3881beeb186af756c6f5495c89effa821fd654957c63584341b91861e6677b6a != recorded 4a73f776970a6e5294d9e4c6ab4956164a78e807a352abacf2a70b2e7fd212c4; re-read https://docs.langchain.com/oss/python/langgraph/checkpointers.md
DRIFT  LG-INT-RERUN  sha256 daecd0a9f23aff377cb56093915b403d97bd3bfe8d571fbecfccd3e0b28184c8 != recorded df80f2e6ec5b1788a4dfc92d592f69560c1ec4b1f11ac5a73ade85bc51bb39cd; re-read https://docs.langchain.com/oss/python/langgraph/interrupts.md
DRIFT  LG-LATEST     sha256 3881beeb186af756c6f5495c89effa821fd654957c63584341b91861e6677b6a != recorded 4a73f776970a6e5294d9e4c6ab4956164a78e807a352abacf2a70b2e7fd212c4; re-read https://docs.langchain.com/oss/python/langgraph/checkpointers.md
ok     LG-OWN-8796   hash matches
DRIFT  LG-PW         sha256 3881beeb186af756c6f5495c89effa821fd654957c63584341b91861e6677b6a != recorded 4a73f776970a6e5294d9e4c6ab4956164a78e807a352abacf2a70b2e7fd212c4; re-read https://docs.langchain.com/oss/python/langgraph/checkpointers.md
ok     LGP-DT        hash matches
refetch: 2 unchanged, 7 drifted, 0 unreachable (reported for review; never fatal)
```

Seven clauses drifted because their pages changed. Every quote and every anchor was still on its page.

## What changed upstream between 2026-09-27 and 2026-10-08

Commits to the page sources in langchain-ai/docs (`src/oss/langgraph/*.mdx`) and to the code-sample snippets those
pages import (`src/snippets/code-samples/`):

| Page | Commit | Change | Cited section touched? |
| --- | --- | --- | --- |
| checkpointers | b7119d5c5f (#6400) | adds `keywords` to the front matter | No |
| checkpointers | 0ae6ef3d50 (#6225) | reflows one line of a `StateSnapshot` example | No |
| graph-api | b7119d5c5f (#6400) | adds `description` and `keywords` to the front matter | No |
| graph-api | 12f4a43bf8 (#6231) | adds a JavaScript-only note on `Overwrite` for reducers | No |
| graph-api | 0ca01d3dfb (#6515), in 15 imported snippets | replaces the "View example trace" cards' LangSmith URLs and adds a caption | No |
| interrupts | 04fd789023 (#6320) | adds "Define an interrupt response schema" and qualifies the resume-value bullet ("subject to validation when you specify a typed `response_schema`") | No: the two quoted "Rules of interrupts" sentences are unchanged. The new section documents a second resume after a rejected value, which LG-INT-RERUN's notes now mention |
| interrupts | 0ca01d3dfb (#6515), in 9 imported snippets | the same trace-card change | No |

c06efd0147 (#6551, uuid4 to uuid7 in samples) touched only JavaScript and TypeScript samples and another page, none of
which appears in these Python pages. functional-api, whose "Idempotency" section LG-DE-RERUN's notes quote, has no
commits in the period.

No change touches a quoted sentence or the section a clause cites, so the seven clauses were re-snapshotted with
`retrieved: 2026-10-08`.

## After

```text
date: 2026-10-08T21:34:52Z
clause files: 80d7bfc, after the re-snapshot
$ uv run crashcourt clauses check --refetch
clauses: 9 clauses valid in clauses
ok     LG-DE-RERUN   hash matches
ok     LG-DUR-ASYNC  hash matches
ok     LG-DUR-EXIT   hash matches
ok     LG-DUR-SYNC   hash matches
ok     LG-INT-RERUN  hash matches
ok     LG-LATEST     hash matches
ok     LG-OWN-8796   hash matches
ok     LG-PW         hash matches
ok     LGP-DT        hash matches
refetch: 9 unchanged, 0 drifted, 0 unreachable (reported for review; never fatal)
```

An earlier version of the anchor check in this pull request derived ids from Markdown headings and reported LG-PW's
`#pending-writes` as missing; the page defines it as an explicit anchor (`<a id="pending-writes" />`). The check now
reads the rendered page's element ids, and LG-PW cites `#pending-writes` as before.
