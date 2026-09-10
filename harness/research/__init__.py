"""The research layer (phase 5): the Claude client and its spend gate, the shadow veto worker,
the weekly report annotator, and the text rule they all share.

Nothing in this package changes a decision. The veto is post-hoc and advisory (addendum 0.1);
the annotator writes bullets that cite report cells; the parlay rationale is prose on a slip a
person places by hand. Every call to the Anthropic API in the harness goes through
`harness.research.client`, and every one of those goes through `harness.research.spend`'s
reservation first -- a static test asserts both.
"""
