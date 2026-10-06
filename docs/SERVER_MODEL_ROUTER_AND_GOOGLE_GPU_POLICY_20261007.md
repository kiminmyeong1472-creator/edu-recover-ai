# EduRecover server model router and Google GPU policy — 2026-10-07

## Server-only routing policy

All model selection and gates run on the EduRecover server. No routing rule or
cloud credential is placed in browser JavaScript.

1. Non-personal, low-cost work: Amazon Bedrock GPT-5.6 Luna.
2. Privacy-sensitive, low-cost work: Amazon Bedrock Claude Haiku 4.5.
3. Privacy-sensitive standard work: Amazon Bedrock Claude Sonnet 5.
4. Privacy-sensitive high-value work: Amazon Bedrock Claude Opus 5.
5. If GPT hallucination, exaggeration/overclaim, false reporting, lie, invalid
   output, or instruction refusal is reported, GPT is excluded from the retry
   and the server routes to Gemini or Claude.
6. Google Cloud and AWS promotional credits are tracked as planning budgets:
   Google 400,000 KRW; AWS 280,000 KRW by default. Runtime environment variables
   can override both amounts and the planning FX rate.
7. Token-cost ledger records provider/model/token counts/estimated cost only.
   It never stores the prompt or response body.

## Privacy notice

A Seoul AWS source region does not prove that every model executes only in
Seoul. Model-specific Bedrock inference profiles can be in-region, geographic,
or global. EduRecover must disclose this limitation for any model that can route
outside the selected AWS Region. Provider ZDR/retention approval remains a
separate fail-closed gate.

## Google GPU

Google Cloud is the approved GPU rental provider for training. Short-lived
preemptible/on-demand leases are preferred and should be released aggressively
when idle. A GPU VM local disk is never authoritative storage.

Training checkpoint and receipt metadata must be synchronized to the persistent
EduRecover server volume at short intervals. A candidate is not promotion-ready
unless the server has a monotonic checkpoint receipt and hash evidence. Large
artifacts may additionally use an approved private cloud object store, but the
server retains the authoritative run state and hashes.

This policy supersedes any older provider preference that named Runpod as the
default GPU rental target.
