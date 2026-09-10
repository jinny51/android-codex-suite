# Android patch capture v2 contract

`android-patch-capture` directly produces the final
`akbs-android-change-package-v2/2/android_change` package for any supported layer.
The packaged schema is
`../../incoming/v2/akbs-android-change-package.schema.json`; there is no separate
capture schema or conversion phase.

The capture CLI may accept `mtk16`, `rk14`, or `unisoc13` as transient input. The
formal manifest stores only canonical `mtk`, `rk`, or `unisoc` in
`subject.target.platform` and stores Android version separately. Every payload is
hash/size bound, every component is covered by patch and evidence, every source is
used, and publication is atomic.

A successful final package continues through member-side read/check/prepare/submit on
the common patch upload lifecycle. It must never fall back to Framework v1. Existing
genuine v1 packages remain on their historical input route.
