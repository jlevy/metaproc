---
last_updated: '2026-11-21'

providers:
  openai:
    models:
      gpt-6.2-astra:
        actual_price:
          input_per_1m: 8.00
          output_per_1m: 40.00
          cache_read_per_1m: 0.40
          cache_write_per_1m: 10.00
        cost_source: computed
        source_url: 'https://developers.openai.com/api/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against OpenAI's Standard pricing table. Released 2026-11-17. Stored rates are the short-context tier (<=272K input tokens); long context bills $16.00 input, $0.80 cached input, and $60.00 output. cache_write_per_1m is the 30-minute cache-write rate ($20.00 for the 12-hour write). Background-window discounts (30% and 40%) and Ultrafast rates are not modelled. Model availability and lifecycle are maintained in model_catalog.py."
      gpt-6.2-sol:
        actual_price:
          input_per_1m: 2.00
          output_per_1m: 10.00
          cache_read_per_1m: 0.10
          cache_write_per_1m: 2.50
        cost_source: computed
        source_url: 'https://developers.openai.com/api/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against OpenAI's Standard pricing table. Released 2026-11-17. Short-context tier (<=272K input); long context bills $4.00 input, $0.20 cached input, and $15.00 output. cache_write_per_1m is the 30-minute write rate ($5.00 for 12 hours). Background-window discounts and Ultrafast rates are not modelled."
      gpt-6.1-sol:
        actual_price:
          input_per_1m: 2.00
          output_per_1m: 10.00
          cache_read_per_1m: 0.10
          cache_write_per_1m: 2.50
        cost_source: computed
        source_url: 'https://developers.openai.com/api/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against OpenAI's Standard pricing table. Short-context tier (<=272K input); long context bills $4.00 input, $0.20 cached input, and $15.00 output. cache_write_per_1m is the 30-minute write rate ($5.00 for 12 hours). These are API-key (metered) rates; ChatGPT sign-in dispatch is subscription-billed. Model availability and lifecycle are maintained in model_catalog.py."
      gpt-6.1-terra:
        actual_price:
          input_per_1m: 1.00
          output_per_1m: 5.00
          cache_read_per_1m: 0.05
          cache_write_per_1m: 1.25
        cost_source: computed
        source_url: 'https://developers.openai.com/api/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against OpenAI's Standard pricing table. Short-context tier (<=272K input); long context bills $2.00 input, $0.10 cached input, and $7.50 output. cache_write_per_1m is the 30-minute write rate ($2.50 for 12 hours). Background-window discounts are not modelled."
      gpt-6-astra:
        actual_price:
          input_per_1m: 10.00
          output_per_1m: 50.00
          cache_read_per_1m: 1.00
          cache_write_per_1m: 12.50
        cost_source: computed
        source_url: 'https://developers.openai.com/api/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against OpenAI's Standard pricing table. Short-context tier (<=272K input); long context bills $20.00 input, $2.00 cached input, and $75.00 output. cache_write_per_1m is the 30-minute write rate ($25.00 for 12 hours). Ultrafast rates are not modelled."
      gpt-6-sol:
        actual_price:
          input_per_1m: 2.00
          output_per_1m: 10.00
          cache_read_per_1m: 0.20
          cache_write_per_1m: 2.50
        cost_source: computed
        source_url: 'https://developers.openai.com/api/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against OpenAI's Standard pricing table. Short-context tier (<=272K input); long context bills $4.00 input, $0.40 cached input, and $15.00 output. cache_write_per_1m is the 30-minute write rate ($5.00 for 12 hours)."
      gpt-6-luna:
        actual_price:
          input_per_1m: 0.10
          output_per_1m: 0.50
          cache_read_per_1m: 0.01
          cache_write_per_1m: 0.125
        cost_source: computed
        source_url: 'https://developers.openai.com/api/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against OpenAI's Standard pricing table. Short-context tier (<=272K input); long context bills $0.20 input, $0.02 cached input, and $0.75 output. cache_write_per_1m is the 30-minute write rate ($0.25 for 12 hours). Background-window discounts are not modelled."
      gpt-5.6-sol:
        actual_price:
          input_per_1m: 4.00
          output_per_1m: 20.00
          cache_read_per_1m: 0.40
          cache_write_per_1m: 5.00
        cost_source: computed
        source_url: 'https://developers.openai.com/api/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against OpenAI's Standard pricing table and the GPT-5.6 Sol model page. These are promotional rates that OpenAI says are available until 2026-11-30. OpenAI has not published the rate that applies afterwards, so no list_price is stored; recheck after 2026-11-30. Short-context tier (<=272K input); long context bills $8.00 input, $0.80 cached input, and $30.00 output. cache_write_per_1m is the 30-minute write rate ($10.00 for 12 hours)."
      gpt-5.6:
        actual_price:
          input_per_1m: 4.00
          output_per_1m: 20.00
          cache_read_per_1m: 0.40
          cache_write_per_1m: 5.00
        cost_source: computed
        source_url: 'https://developers.openai.com/api/docs/models/gpt-5.6-sol'
        last_reviewed: '2026-11-18'
        notes: "Alias row. OpenAI's GPT-5.6 Sol model page says the gpt-5.6 alias routes requests to GPT-5.6 Sol, and codex-cli records the requested slug, so this row carries the gpt-5.6-sol rate card and must change with it. Omitted from the comparison table because it duplicates gpt-5.6-sol."
      gpt-5.6-terra:
        actual_price:
          input_per_1m: 2.00
          output_per_1m: 12.00
          cache_read_per_1m: 0.20
          cache_write_per_1m: 2.50
        cost_source: computed
        source_url: 'https://developers.openai.com/api/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against OpenAI's Standard pricing table. Short-context tier (<=272K input); long context bills $4.00 input, $0.40 cached input, and $18.00 output. cache_write_per_1m is the 30-minute write rate ($5.00 for 12 hours)."
      gpt-5.6-luna:
        actual_price:
          input_per_1m: 0.20
          output_per_1m: 1.20
          cache_read_per_1m: 0.02
          cache_write_per_1m: 0.25
        cost_source: computed
        source_url: 'https://developers.openai.com/api/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against OpenAI's Standard pricing table. Short-context tier (<=272K input); long context bills $0.40 input, $0.04 cached input, and $1.80 output. cache_write_per_1m is the 30-minute write rate ($0.50 for 12 hours)."
      gpt-5.5:
        actual_price:
          input_per_1m: 5.00
          output_per_1m: 30.00
          cache_read_per_1m: 0.50
        cost_source: computed
        source_url: 'https://developers.openai.com/api/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against OpenAI's Standard pricing table; rates unchanged since the 2026-04-27 review. Short-context tier (<272K); long context bills $10.00 input, $1.00 cached input, and $45.00 output. No cache-write rate is published. Model availability and lifecycle are maintained in model_catalog.py."
      gpt-5.5-pro:
        actual_price:
          input_per_1m: 30.00
          output_per_1m: 180.00
        cost_source: computed
        source_url: 'https://developers.openai.com/api/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against OpenAI's Standard pricing table; rates unchanged. Short-context tier (<272K); long context bills $60.00 input and $270.00 output. OpenAI publishes no cached-input rate for this model."
      gpt-5.4:
        actual_price:
          input_per_1m: 2.50
          output_per_1m: 15.00
          cache_read_per_1m: 0.25
        cost_source: computed
        source_url: 'https://developers.openai.com/api/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Historical: not in the 2026-11-18 codex-cli catalog; retained so earlier run logs still price. Reviewed 2026-11-18 against OpenAI's Standard pricing table. Short-context tier (<272K); long context bills $5.00 input, $0.50 cached input, and $22.50 output."
      gpt-5.4-mini:
        actual_price:
          input_per_1m: 0.75
          output_per_1m: 4.50
          cache_read_per_1m: 0.075
        cost_source: computed
        source_url: 'https://developers.openai.com/api/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Historical: not in the 2026-11-18 codex-cli catalog; retained so earlier run logs still price. Reviewed 2026-11-18 against OpenAI's Standard pricing table; rates unchanged since 2026-04-27."
      gpt-5.4-nano:
        actual_price:
          input_per_1m: 0.20
          output_per_1m: 1.25
          cache_read_per_1m: 0.02
        cost_source: computed
        source_url: 'https://developers.openai.com/api/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Historical: not in the 2026-11-18 codex-cli catalog; retained so earlier run logs still price. Reviewed 2026-11-18 against OpenAI's Standard pricing table; rates unchanged since 2026-04-27."

  anthropic:
    models:
      claude-fable-5-2:
        actual_price:
          input_per_1m: 8.00
          output_per_1m: 40.00
          cache_read_per_1m: 0.20
          cache_write_per_1m: 10.00
        cost_source: self-reported
        source_url: 'https://platform.claude.com/docs/en/about-claude/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against Anthropic's pricing page. Cache reads are 0.025x base input. cache_write_per_1m is the 5-minute write rate ($16.00 for 1-hour writes). Fast mode ($16.00 / $80.00) is not modelled. Model availability and lifecycle are maintained in model_catalog.py."
      claude-fable-5-1:
        actual_price:
          input_per_1m: 10.00
          output_per_1m: 50.00
          cache_read_per_1m: 0.25
          cache_write_per_1m: 12.50
        cost_source: self-reported
        source_url: 'https://platform.claude.com/docs/en/about-claude/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against Anthropic's pricing page. Cache reads are 0.025x base input. cache_write_per_1m is the 5-minute write rate ($20.00 for 1-hour writes)."
      claude-fable-5:
        actual_price:
          input_per_1m: 10.00
          output_per_1m: 50.00
          cache_read_per_1m: 1.00
          cache_write_per_1m: 12.50
        cost_source: self-reported
        source_url: 'https://platform.claude.com/docs/en/about-claude/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against Anthropic's pricing page. Cache reads are 0.1x base input. cache_write_per_1m is the 5-minute write rate ($20.00 for 1-hour writes)."
      claude-opus-5-5:
        actual_price:
          input_per_1m: 4.00
          output_per_1m: 20.00
          cache_read_per_1m: 0.20
          cache_write_per_1m: 5.00
        cost_source: self-reported
        source_url: 'https://platform.claude.com/docs/en/about-claude/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against Anthropic's pricing page. Cache reads are 0.05x base input. cache_write_per_1m is the 5-minute write rate ($8.00 for 1-hour writes). Fast mode ($8.00 / $40.00) is not modelled. The claude-code-cli opus alias resolves here on the pinned client. Model availability and lifecycle are maintained in model_catalog.py."
      claude-opus-5:
        actual_price:
          input_per_1m: 5.00
          output_per_1m: 25.00
          cache_read_per_1m: 0.50
          cache_write_per_1m: 6.25
        cost_source: self-reported
        source_url: 'https://platform.claude.com/docs/en/about-claude/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against Anthropic's pricing page. cache_write_per_1m is the 5-minute write rate ($10.00 for 1-hour writes). Fast mode ($10.00 / $50.00) is not modelled."
      claude-opus-4-8:
        actual_price:
          input_per_1m: 5.00
          output_per_1m: 25.00
          cache_read_per_1m: 0.50
          cache_write_per_1m: 6.25
        cost_source: self-reported
        source_url: 'https://platform.claude.com/docs/en/about-claude/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against Anthropic's pricing page. cache_write_per_1m is the 5-minute write rate ($10.00 for 1-hour writes). Fast mode ($10.00 / $50.00, deprecated for this model) is not modelled."
      claude-opus-4-7:
        actual_price:
          input_per_1m: 5.00
          output_per_1m: 25.00
          cache_read_per_1m: 0.50
          cache_write_per_1m: 6.25
        cost_source: self-reported
        source_url: 'https://platform.claude.com/docs/en/about-claude/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against Anthropic's pricing page, which lists Opus 4.7 at these rates (the 2026-04-24 values had been carried over from 4.6). cache_write_per_1m is the 5-minute write rate."
      claude-opus-4-6:
        actual_price:
          input_per_1m: 5.00
          output_per_1m: 25.00
          cache_read_per_1m: 0.50
          cache_write_per_1m: 6.25
        cost_source: self-reported
        source_url: 'https://platform.claude.com/docs/en/about-claude/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against Anthropic's pricing page. cache_write_per_1m uses Anthropic's 5-minute prompt-cache write rate."
      claude-sonnet-5-5:
        actual_price:
          input_per_1m: 2.00
          output_per_1m: 10.00
          cache_read_per_1m: 0.10
          cache_write_per_1m: 2.50
        cost_source: self-reported
        source_url: 'https://platform.claude.com/docs/en/about-claude/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against Anthropic's pricing page. Cache reads are 0.05x base input. cache_write_per_1m is the 5-minute write rate ($4.00 for 1-hour writes). The sonnet alias resolves here on the pinned Claude Code and Pi clients."
      claude-sonnet-5:
        actual_price:
          input_per_1m: 2.00
          output_per_1m: 10.00
          cache_read_per_1m: 0.20
          cache_write_per_1m: 2.50
        cost_source: self-reported
        source_url: 'https://platform.claude.com/docs/en/about-claude/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against Anthropic's pricing page. The $2 / $10 launch price is now the standard price; the increase to $3 / $15 scheduled for 2026-09-01 did not occur. cache_write_per_1m is the 5-minute write rate ($4.00 for 1-hour writes)."
      claude-sonnet-4-6:
        actual_price:
          input_per_1m: 3.00
          output_per_1m: 15.00
          cache_read_per_1m: 0.30
          cache_write_per_1m: 3.75
        cost_source: self-reported
        source_url: 'https://platform.claude.com/docs/en/about-claude/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against Anthropic's pricing page. cache_write_per_1m uses Anthropic's 5-minute prompt-cache write rate."
      claude-sonnet-4-5:
        actual_price:
          input_per_1m: 3.00
          output_per_1m: 15.00
          cache_read_per_1m: 0.30
          cache_write_per_1m: 3.75
        cost_source: self-reported
        source_url: 'https://platform.claude.com/docs/en/about-claude/pricing'
        last_reviewed: '2026-11-18'
        notes: "Historical: not in the 2026-11-18 model catalog; Anthropic lists Sonnet 4.5 as deprecated. Retained so earlier run logs still price. Reviewed 2026-11-18 against Anthropic's pricing page. cache_write_per_1m uses the 5-minute write rate."
      claude-haiku-5-5:
        actual_price:
          input_per_1m: 0.10
          output_per_1m: 0.50
          cache_read_per_1m: 0.01
          cache_write_per_1m: 0.125
        cost_source: self-reported
        source_url: 'https://platform.claude.com/docs/en/about-claude/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against Anthropic's pricing page. TIERED: stored rates are for requests whose prompt (input plus cache reads and writes) is up to 100,000 tokens. A request over 100,000 prompt tokens bills $0.50 input, $0.625 5-minute cache write, $0.05 cache read, and $2.50 output, 5x these rates. The schema has one rate card per model, so computed costs for long-prompt work are a lower bound. Claude CLI's reported cost takes precedence as the list-cost estimate. 1-hour cache writes are $0.20 (or $1.00 over 100K)."
      claude-haiku-4-5:
        actual_price:
          input_per_1m: 1.00
          output_per_1m: 5.00
          cache_read_per_1m: 0.10
          cache_write_per_1m: 1.25
        cost_source: self-reported
        source_url: 'https://platform.claude.com/docs/en/about-claude/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against Anthropic's pricing page. The catalog accepts the dated ID claude-haiku-4-5-20251001, which has its own row. cache_write_per_1m uses Anthropic's 5-minute prompt-cache write rate."
      claude-haiku-4-5-20251001:
        actual_price:
          input_per_1m: 1.00
          output_per_1m: 5.00
          cache_read_per_1m: 0.10
          cache_write_per_1m: 1.25
        cost_source: self-reported
        source_url: 'https://platform.claude.com/docs/en/about-claude/pricing'
        last_reviewed: '2026-11-18'
        notes: "Dated-ID row for Haiku 4.5. Claude Code reports this ID in modelUsage, and price lookup matches IDs exactly, so this row carries the claude-haiku-4-5 rate card and must change with it. Omitted from the comparison table because it duplicates claude-haiku-4-5."

  google:
    models:
      gemini-4-carbon:
        actual_price:
          input_per_1m: 2.00
          output_per_1m: 10.00
          cache_read_per_1m: 0.10
        list_price:
          input_per_1m: 4.00
          output_per_1m: 20.00
          cache_read_per_1m: 0.20
        cost_source: computed
        source_url: 'https://aistudio.google.com/docs/pricing'
        list_source_url: 'https://aistudio.google.com/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against Google's Gemini API pricing page and cross-checked against the Vertex AI pricing page. GA 2026-11-10; paid tier only. actual_price is Google's promotional rate through 2026-11-30; list_price is the standard rate from 2026-12-01. After 2026-11-30 the promotional rate no longer applies, so move list_price into actual_price at the next review. No context-length tier; output includes thinking tokens. Implicit caching only. Vertex delivers the promotion as 50% credits back, and its non-global regions charge 10% more. The cyber-permissive variant has no promotion. Model availability and lifecycle are maintained in model_catalog.py."
      gemini-4-argon:
        actual_price:
          input_per_1m: 2.00
          output_per_1m: 10.00
          cache_read_per_1m: 0.10
        list_price:
          input_per_1m: 4.00
          output_per_1m: 20.00
          cache_read_per_1m: 0.20
        cost_source: computed
        source_url: 'https://aistudio.google.com/docs/pricing'
        list_source_url: 'https://aistudio.google.com/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against Google's Gemini API pricing page and cross-checked against the Vertex AI pricing page. GA 2026-10-13; paid tier only. actual_price is Google's promotional rate through 2026-11-30; list_price is the standard rate from 2026-12-01. After 2026-11-30 the promotional rate no longer applies, so move list_price into actual_price at the next review. No context-length tier; output includes thinking tokens. Implicit caching only. Vertex delivers the promotion as 50% credits back, and its non-global regions charge 10% more."
      gemini-4-hydrogen:
        actual_price:
          input_per_1m: 1.50
          output_per_1m: 7.50
          cache_read_per_1m: 0.075
        cost_source: computed
        source_url: 'https://aistudio.google.com/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against Google's Gemini API pricing page and cross-checked against the Vertex AI pricing page. GA 2026-11-12; a free tier is also available. Standard paid tier with no context-length tier; output includes thinking tokens. Implicit caching only (cache read is 0.05x input). Vertex publishes a $1.875 cache-write rate effective 2027-01-12, which is not stored yet; its non-global regions charge 10% more."
      gemini-4-neon:
        actual_price:
          input_per_1m: 3.00
          output_per_1m: 15.00
          cache_read_per_1m: 0.15
        cost_source: computed
        source_url: 'https://aistudio.google.com/docs/pricing#gemini-4-neon'
        last_reviewed: '2026-11-21'
        notes: "Gemini API Standard paid pricing for Gemini CLI estimates. Output includes thinking tokens. Caching is implicit."
      gemini-3.8-flash:
        actual_price:
          input_per_1m: 0.75
          output_per_1m: 3.75
          cache_read_per_1m: 0.075
        list_price:
          input_per_1m: 1.50
          output_per_1m: 7.50
          cache_read_per_1m: 0.15
        cost_source: computed
        source_url: 'https://aistudio.google.com/docs/pricing'
        list_source_url: 'https://aistudio.google.com/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against Google's Gemini API pricing page and the Vertex AI pricing page; rates unchanged since 2026-09-14. GA 2026-09-02. Standard paid tier with no context-length tiers; output includes thinking tokens. actual_price is Google's introductory rate through 2026-12-31; list_price is the standard rate from 2027-01-01. Context-cache storage ($0.50 per 1M tokens per hour through 2026-12-31, $1.00 from 2027-01-01) is not modelled. Vertex AI's global endpoint charges the same rates; its non-global regions charge 10% more. Model availability and lifecycle are maintained in model_catalog.py."
      gemini-3.7-flash:
        actual_price:
          input_per_1m: 0.75
          output_per_1m: 3.75
          cache_read_per_1m: 0.075
        list_price:
          input_per_1m: 1.50
          output_per_1m: 7.50
          cache_read_per_1m: 0.15
        cost_source: computed
        source_url: 'https://cloud.google.com/model-garden/pricing'
        list_source_url: 'https://cloud.google.com/model-garden/pricing'
        last_reviewed: '2026-11-18'
        notes: "Historical: not in the 2026-11-18 model catalog; added so earlier run logs price. Reviewed 2026-11-18 against the Vertex AI pricing page (global endpoint). The Gemini API deprecated this model and routes it to gemini-3.8-flash, so it has no Gemini API price row. actual_price is the introductory rate through 2026-12-31; list_price is the standard rate from 2027-01-01."
      gemini-3.6-flash:
        actual_price:
          input_per_1m: 0.75
          output_per_1m: 3.75
          cache_read_per_1m: 0.075
        list_price:
          input_per_1m: 1.50
          output_per_1m: 7.50
          cache_read_per_1m: 0.15
        cost_source: computed
        source_url: 'https://aistudio.google.com/docs/pricing'
        list_source_url: 'https://aistudio.google.com/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against Google's Gemini API pricing page and the Vertex AI pricing page. GA 2026-07-21. Corrected: the 2026-07-24 row stored the standard rate as actual_price, but Google charges an introductory $0.75 / $3.75 / $0.075 through 2026-12-31 and the standard $1.50 / $7.50 / $0.15 from 2027-01-01, so actual_price now holds the introductory rate and list_price the standard rate. Context-cache storage is not modelled. Model availability and lifecycle are maintained in model_catalog.py."
      gemini-3.5-flash:
        actual_price:
          input_per_1m: 1.50
          output_per_1m: 9.00
          cache_read_per_1m: 0.15
        cost_source: computed
        source_url: 'https://cloud.google.com/model-garden/pricing'
        last_reviewed: '2026-11-18'
        notes: "Historical: not in the 2026-11-18 model catalog; retained so earlier run logs still price. Reviewed 2026-11-18 against the Vertex AI pricing page (global endpoint); rates unchanged since 2026-05-23. The Gemini API deprecated this model and routes it to gemini-3.6-flash, so the Gemini API page no longer lists it."
      gemini-3.5-flash-lite:
        actual_price:
          input_per_1m: 0.30
          output_per_1m: 2.50
          cache_read_per_1m: 0.03
        cost_source: computed
        source_url: 'https://aistudio.google.com/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against Google's Gemini API pricing page (text, image, video, and audio input share one rate) and cross-checked against the Vertex AI pricing page. GA 2026-07-21. Context-cache storage ($1.00 per 1M tokens per hour) is not modelled. Model availability and lifecycle are maintained in model_catalog.py."
      gemini-3.1-pro-preview:
        actual_price:
          input_per_1m: 2.00
          output_per_1m: 12.00
          cache_read_per_1m: 0.20
        cost_source: computed
        source_url: 'https://aistudio.google.com/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18. Standard paid-tier pricing for prompts <=200K tokens; prompts over 200K bill $4.00 input, $0.40 cached input, and $18.00 output. Same rate card as -customtools."
      gemini-3.1-pro-preview-customtools:
        actual_price:
          input_per_1m: 2.00
          output_per_1m: 12.00
          cache_read_per_1m: 0.20
        cost_source: computed
        source_url: 'https://aistudio.google.com/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18. Standard paid-tier pricing for prompts <=200K tokens. Omitted from the comparison table because it matches gemini-3.1-pro-preview."
      gemini-3-pro-preview:
        actual_price:
          input_per_1m: 2.00
          output_per_1m: 12.00
          cache_read_per_1m: 0.20
        cost_source: computed
        source_url: 'https://ai.google.dev/gemini-api/docs/pricing'
        last_reviewed: '2026-04-02'
        notes: "Historical: shut down on the Gemini API 2026-03-09 and no longer on Google's pricing page, so the rate was not re-verified on 2026-11-18. Retained so earlier run logs still price. Rate card from the 2026-04-02 review."
      gemini-3-flash-preview:
        actual_price:
          input_per_1m: 0.50
          output_per_1m: 3.00
          cache_read_per_1m: 0.05
        cost_source: computed
        source_url: 'https://aistudio.google.com/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Historical: not in the 2026-11-18 model catalog; retained so earlier run logs still price. Reviewed 2026-11-18: standard paid-tier text, image, and video rate (audio input bills $1.00, cached $0.10)."
      gemini-3.1-flash-lite-preview:
        actual_price:
          input_per_1m: 0.25
          output_per_1m: 1.50
          cache_read_per_1m: 0.025
        cost_source: computed
        source_url: 'https://ai.google.dev/gemini-api/docs/pricing'
        last_reviewed: '2026-04-02'
        notes: "Historical: shut down 2026-05-25 and no longer on Google's pricing page, so the rate was not re-verified on 2026-11-18. Retained so earlier run logs still price. Same rate card as gemini-3.1-flash-lite (GA)."
      gemini-3.1-flash-lite:
        actual_price:
          input_per_1m: 0.25
          output_per_1m: 1.50
          cache_read_per_1m: 0.025
        cost_source: computed
        source_url: 'https://aistudio.google.com/docs/pricing'
        last_reviewed: '2026-11-18'
        notes: "Historical: not in the 2026-11-18 model catalog; retained so earlier run logs still price. Reviewed 2026-11-18: standard paid-tier text, image, and video rate (audio input bills $0.50, cached $0.05); unchanged since 2026-05-23."

  deepseek:
    models:
      deepseek-v4-pro:
        actual_price:
          input_per_1m: 1.32
          output_per_1m: 3.96
          cache_read_per_1m: 0.044
        cost_source: computed
        source_url: 'https://api-docs.deepseek.com/quick_start/pricing/'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against DeepSeek's pricing page. Served by DeepSeek-V4-Pro-0813; 1M context, 384K max output; thinking and non-thinking modes share the ID. TIME-OF-DAY PRICING: stored rates are the peak rate (01:00-04:00 and 06:00-10:00 UTC, Monday to Friday, excluding Chinese public holidays). All other hours bill the off-peak rate, half of peak ($0.66 input, $0.022 cache hit, $1.98 output). Usage logs do not let this file tell the two apart, so computed costs are an upper bound. The 2026-05-23 row's promotional actual_price and $1.74 / $3.48 list_price are superseded and were removed."
      deepseek-flash:
        actual_price:
          input_per_1m: 0.30
          output_per_1m: 1.20
          cache_read_per_1m: 0.006
        cost_source: computed
        source_url: 'https://api-docs.deepseek.com/quick_start/pricing/'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against DeepSeek's pricing page. Current Flash ID, served by DeepSeek-V4.1-Flash-1017; 1M context, 384K max output; thinking and non-thinking modes share the ID. TIME-OF-DAY PRICING: stored rates are the peak rate; off-peak is half ($0.15 input, $0.003 cache hit, $0.60 output). Computed costs are an upper bound."
      deepseek-v4-flash:
        actual_price:
          input_per_1m: 0.30
          output_per_1m: 1.20
          cache_read_per_1m: 0.006
        cost_source: computed
        source_url: 'https://api-docs.deepseek.com/quick_start/pricing/'
        last_reviewed: '2026-11-18'
        notes: "Legacy ID. Reviewed 2026-11-18: DeepSeek still accepts deepseek-v4-flash, but the model behind it is retired; requests are served by DeepSeek-V4.1-Flash-1017 and billed at the deepseek-flash price, so this row carries the deepseek-flash peak rate card. Runs made before that cutover were billed $0.14 / $0.28 / $0.0028 (2026-05-23 review); the schema stores one rate card per ID, so re-pricing those runs now overstates them."

  moonshot:
    models:
      kimi-k3.1-pro:
        actual_price:
          input_per_1m: 2.00
          output_per_1m: 10.00
          cache_read_per_1m: 0.20
          cache_write_per_1m: 2.00
        cost_source: computed
        source_url: 'https://platform.kimi.ai/docs/pricing/chat'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against Kimi's model inference pricing page (platform.moonshot.ai now redirects to platform.kimi.ai). Current K3-series flagship; 1,048,576-token context; flat pricing with no context-length tier. cache_write_per_1m is the 5-minute TTL write rate ($4.00 for 1-hour writes); cache hits bill only the cached-input rate. Packaged for Pi's moonshot provider."
      kimi-k3:
        actual_price:
          input_per_1m: 3.00
          output_per_1m: 15.00
          cache_read_per_1m: 0.30
          cache_write_per_1m: 3.00
        cost_source: computed
        source_url: 'https://platform.kimi.ai/docs/pricing/chat'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against Kimi's model inference pricing page. K3-series model; 1,048,576-token context; flat pricing with no context-length tier. cache_write_per_1m is the 5-minute TTL write rate ($6.00 for 1-hour writes). Packaged for Pi's moonshot provider."
      kimi-k2.6:
        actual_price:
          input_per_1m: 0.95
          output_per_1m: 4.00
          cache_read_per_1m: 0.16
        cost_source: computed
        source_url: 'https://platform.kimi.ai/docs/pricing/chat'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against Kimi's model inference pricing page; rates unchanged since 2026-04-27. K2-series models closed to newly registered users on 2026-10-31, and Kimi plans to retire them by the end of December 2026. Model availability and lifecycle are maintained in model_catalog.py."

  vertex-maas:
    models:
      glm-5.2-maas:
        actual_price:
          input_per_1m: 1.40
          output_per_1m: 4.40
          cache_read_per_1m: 0.14
        cost_source: computed
        source_url: 'https://cloud.google.com/model-garden/partner-models/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against Google's partner and open model pricing page. Pi reports the org-prefixed ID zai-org/glm-5.2-maas, which price lookup resolves to this row. Preview on the global endpoint, replacing the retired GLM-5 and GLM-4.7 MaaS models. No separate Zhipu direct-API list_price is stored."
      deepseek-v4.1-flash-maas:
        actual_price:
          input_per_1m: 0.30
          output_per_1m: 1.20
          cache_read_per_1m: 0.03
        cost_source: computed
        source_url: 'https://cloud.google.com/model-garden/partner-models/pricing'
        last_reviewed: '2026-11-18'
        notes: "Reviewed 2026-11-18 against Google's partner and open model pricing page. Pi reports the org-prefixed ID deepseek-ai/deepseek-v4.1-flash-maas. Preview on the global endpoint, replacing the retired DeepSeek V3.2 MaaS model; Google charged nothing until 2026-10-16. No DeepSeek direct list_price is stored: the direct API serves a different snapshot (V4.1-Flash-1017) with time-of-day pricing. The deepseek-v4.1-flash-1017-maas endpoint has no published price yet and has no row."
      glm-5-maas:
        actual_price:
          input_per_1m: 1.00
          output_per_1m: 3.20
          cache_read_per_1m: 0.10
        cost_source: computed
        source_url: 'https://cloud.google.com/model-garden/partner-models/pricing'
        last_reviewed: '2026-11-18'
        notes: "Historical: Vertex retired this MaaS model on 2026-10-21 (replacement glm-5.2-maas); retained so earlier run logs still price. Reviewed 2026-11-18: Google's pricing page still lists this rate. Google's no-charge note expired 2026-02-19. No separate Zhipu direct-API list_price is stored."
      glm-4.7-maas:
        actual_price:
          input_per_1m: 0.60
          output_per_1m: 2.20
          cache_read_per_1m: 0.06
        cost_source: computed
        source_url: 'https://cloud.google.com/model-garden/partner-models/pricing'
        last_reviewed: '2026-11-18'
        notes: "Historical: Vertex retired this MaaS model on 2026-10-21 (replacement glm-5.2-maas); retained so earlier run logs still price. Reviewed 2026-11-18: Google's pricing page still lists this rate."
      kimi-k2-thinking-maas:
        actual_price:
          input_per_1m: 0.60
          output_per_1m: 2.50
          cache_read_per_1m: 0.06
        list_price:
          input_per_1m: 0.60
          output_per_1m: 2.50
          cache_read_per_1m: 0.15
        cost_source: computed
        source_url: 'https://cloud.google.com/model-garden/partner-models/pricing'
        list_source_url: 'https://platform.moonshot.ai/'
        last_reviewed: '2026-11-18'
        notes: "Historical: Vertex retired this MaaS model on 2026-10-21 with no Kimi successor on Vertex; retained so earlier run logs still price. actual_price re-verified 2026-11-18 on Google's pricing page. list_price is frozen at the 2026-04-02 Moonshot rate; Kimi's current pricing page no longer lists this model."
      deepseek-v3.2-maas:
        actual_price:
          input_per_1m: 0.56
          output_per_1m: 1.68
          cache_read_per_1m: 0.056
        list_price:
          input_per_1m: 0.14
          output_per_1m: 0.28
          cache_read_per_1m: 0.0028
        cost_source: computed
        source_url: 'https://cloud.google.com/model-garden/partner-models/pricing'
        list_source_url: 'https://api-docs.deepseek.com/quick_start/pricing/'
        last_reviewed: '2026-11-18'
        notes: "Historical: Vertex retired this MaaS model on 2026-10-21 (replacement deepseek-v4.1-flash-maas); retained so earlier run logs still price. actual_price re-verified 2026-11-18 on Google's pricing page. list_price is frozen at the V4-Flash direct rate from the 2026-05-23 review ($0.14 / $0.28 / $0.0028), the comparison point while this model served; DeepSeek's current Flash price is not applied to historical runs."
      qwen3-235b-a22b-instruct-2507-maas:
        actual_price:
          input_per_1m: 0.22
          output_per_1m: 0.88
        list_price:
          input_per_1m: 0.287
          output_per_1m: 0.92
        cost_source: computed
        source_url: 'https://cloud.google.com/model-garden/partner-models/pricing'
        list_source_url: 'https://www.alibabacloud.com/help/en/model-studio/model-pricing'
        last_reviewed: '2026-11-18'
        notes: "Historical: Vertex retired this MaaS model on 2026-10-21 with no Qwen chat successor on Vertex; retained so earlier run logs still price. actual_price re-verified 2026-11-18 on Google's pricing page. list_price is frozen at the 2026-04-02 Alibaba Cloud Model Studio Global deployment rate and was not re-verified."
      qwen3-coder-480b-a35b-instruct-maas:
        actual_price:
          input_per_1m: 0.22
          output_per_1m: 1.80
          cache_read_per_1m: 0.022
        list_price:
          input_per_1m: 0.861
          output_per_1m: 3.441
        cost_source: computed
        source_url: 'https://cloud.google.com/model-garden/partner-models/pricing'
        list_source_url: 'https://www.alibabacloud.com/help/en/model-studio/model-pricing'
        last_reviewed: '2026-11-18'
        notes: "Historical: Vertex retired this MaaS model on 2026-10-21 with no Qwen chat successor on Vertex; retained so earlier run logs still price. actual_price re-verified 2026-11-18 on Google's pricing page. list_price is frozen at the 2026-04-02 Alibaba Cloud Model Studio Global deployment rate for the 0-32K input tier and was not re-verified."
---
# Model Pricing Reference

All prices are **per 1M tokens in USD**. Metaproc uses these rates as a fallback to
estimate list cost from logged token counts.
CLI-reported costs take precedence as list-cost estimates.

Rows for models that the model catalog no longer accepts are kept, with a “Historical”
note, because earlier run logs still need prices.

## All Models — List Price Comparison

Sorted by output cost descending.
List price = the public vendor-direct rate when we have a separate, independently
verifiable vendor price, or the provider’s standard rate when a time-limited
introductory rate is in effect.
When `list_price` is absent, `list_cost` falls back to `actual_price`.

The **Norm** column shows each model’s input price as a multiple of $0.28 / 1M tokens —
the legacy `deepseek-chat` baseline used when the Norm column was introduced.
The baseline is held constant so Norm values stay stable across pricing refreshes, even
after `deepseek-chat` itself was retired as a separate row.

Three rows are omitted from the table because they duplicate another row’s rate card:
`gemini-3.1-pro-preview-customtools` (same as `gemini-3.1-pro-preview`), the `gpt-5.6`
alias (same as `gpt-5.6-sol`), and the dated `claude-haiku-4-5-20251001` ID (same as
`claude-haiku-4-5`).

| Model | Provider | Input | Norm | Output | Cache Read | Cache Write | Source |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| gpt-5.5-pro | OpenAI | $30.00 | 107.14x | $180.00 | -- | -- | [openai](https://developers.openai.com/api/docs/pricing) |
| gpt-6-astra | OpenAI | $10.00 | 35.71x | $50.00 | $1.00 | $12.50 | [openai](https://developers.openai.com/api/docs/pricing) |
| claude-fable-5-1 | Anthropic | $10.00 | 35.71x | $50.00 | $0.25 | $12.50 | [anthropic](https://platform.claude.com/docs/en/about-claude/pricing) |
| claude-fable-5 | Anthropic | $10.00 | 35.71x | $50.00 | $1.00 | $12.50 | [anthropic](https://platform.claude.com/docs/en/about-claude/pricing) |
| gpt-6.2-astra | OpenAI | $8.00 | 28.57x | $40.00 | $0.40 | $10.00 | [openai](https://developers.openai.com/api/docs/pricing) |
| claude-fable-5-2 | Anthropic | $8.00 | 28.57x | $40.00 | $0.20 | $10.00 | [anthropic](https://platform.claude.com/docs/en/about-claude/pricing) |
| gpt-5.5 | OpenAI | $5.00 | 17.86x | $30.00 | $0.50 | -- | [openai](https://developers.openai.com/api/docs/pricing) |
| claude-opus-5 | Anthropic | $5.00 | 17.86x | $25.00 | $0.50 | $6.25 | [anthropic](https://platform.claude.com/docs/en/about-claude/pricing) |
| claude-opus-4-8 | Anthropic | $5.00 | 17.86x | $25.00 | $0.50 | $6.25 | [anthropic](https://platform.claude.com/docs/en/about-claude/pricing) |
| claude-opus-4-7 | Anthropic | $5.00 | 17.86x | $25.00 | $0.50 | $6.25 | [anthropic](https://platform.claude.com/docs/en/about-claude/pricing) |
| claude-opus-4-6 | Anthropic | $5.00 | 17.86x | $25.00 | $0.50 | $6.25 | [anthropic](https://platform.claude.com/docs/en/about-claude/pricing) |
| gpt-5.6-sol | OpenAI | $4.00 | 14.29x | $20.00 | $0.40 | $5.00 | [openai](https://developers.openai.com/api/docs/pricing) |
| claude-opus-5-5 | Anthropic | $4.00 | 14.29x | $20.00 | $0.20 | $5.00 | [anthropic](https://platform.claude.com/docs/en/about-claude/pricing) |
| gemini-4-carbon | Google | $4.00 | 14.29x | $20.00 | $0.20 | -- | [google](https://aistudio.google.com/docs/pricing) |
| gemini-4-argon | Google | $4.00 | 14.29x | $20.00 | $0.20 | -- | [google](https://aistudio.google.com/docs/pricing) |
| gpt-5.4 | OpenAI | $2.50 | 8.93x | $15.00 | $0.25 | -- | [openai](https://developers.openai.com/api/docs/pricing) |
| claude-sonnet-4-6 | Anthropic | $3.00 | 10.71x | $15.00 | $0.30 | $3.75 | [anthropic](https://platform.claude.com/docs/en/about-claude/pricing) |
| claude-sonnet-4-5 | Anthropic | $3.00 | 10.71x | $15.00 | $0.30 | $3.75 | [anthropic](https://platform.claude.com/docs/en/about-claude/pricing) |
| gemini-4-neon | Google | $3.00 | 10.71x | $15.00 | $0.15 | -- | [google](https://aistudio.google.com/docs/pricing#gemini-4-neon) |
| kimi-k3 | Moonshot | $3.00 | 10.71x | $15.00 | $0.30 | $3.00 | [moonshot](https://platform.kimi.ai/docs/pricing/chat) |
| gpt-5.6-terra | OpenAI | $2.00 | 7.14x | $12.00 | $0.20 | $2.50 | [openai](https://developers.openai.com/api/docs/pricing) |
| gemini-3.1-pro-preview | Google | $2.00 | 7.14x | $12.00 | $0.20 | -- | [google](https://aistudio.google.com/docs/pricing) |
| gemini-3-pro-preview | Google | $2.00 | 7.14x | $12.00 | $0.20 | -- | [google](https://ai.google.dev/gemini-api/docs/pricing) |
| gpt-6.2-sol | OpenAI | $2.00 | 7.14x | $10.00 | $0.10 | $2.50 | [openai](https://developers.openai.com/api/docs/pricing) |
| gpt-6.1-sol | OpenAI | $2.00 | 7.14x | $10.00 | $0.10 | $2.50 | [openai](https://developers.openai.com/api/docs/pricing) |
| gpt-6-sol | OpenAI | $2.00 | 7.14x | $10.00 | $0.20 | $2.50 | [openai](https://developers.openai.com/api/docs/pricing) |
| claude-sonnet-5-5 | Anthropic | $2.00 | 7.14x | $10.00 | $0.10 | $2.50 | [anthropic](https://platform.claude.com/docs/en/about-claude/pricing) |
| claude-sonnet-5 | Anthropic | $2.00 | 7.14x | $10.00 | $0.20 | $2.50 | [anthropic](https://platform.claude.com/docs/en/about-claude/pricing) |
| kimi-k3.1-pro | Moonshot | $2.00 | 7.14x | $10.00 | $0.20 | $2.00 | [moonshot](https://platform.kimi.ai/docs/pricing/chat) |
| gemini-3.5-flash | Google | $1.50 | 5.36x | $9.00 | $0.15 | -- | [vertex](https://cloud.google.com/model-garden/pricing) |
| gemini-4-hydrogen | Google | $1.50 | 5.36x | $7.50 | $0.075 | -- | [google](https://aistudio.google.com/docs/pricing) |
| gemini-3.8-flash | Google | $1.50 | 5.36x | $7.50 | $0.15 | -- | [google](https://aistudio.google.com/docs/pricing) |
| gemini-3.7-flash | Google | $1.50 | 5.36x | $7.50 | $0.15 | -- | [vertex](https://cloud.google.com/model-garden/pricing) |
| gemini-3.6-flash | Google | $1.50 | 5.36x | $7.50 | $0.15 | -- | [google](https://aistudio.google.com/docs/pricing) |
| gpt-6.1-terra | OpenAI | $1.00 | 3.57x | $5.00 | $0.05 | $1.25 | [openai](https://developers.openai.com/api/docs/pricing) |
| claude-haiku-4-5 | Anthropic | $1.00 | 3.57x | $5.00 | $0.10 | $1.25 | [anthropic](https://platform.claude.com/docs/en/about-claude/pricing) |
| gpt-5.4-mini | OpenAI | $0.75 | 2.68x | $4.50 | $0.075 | -- | [openai](https://developers.openai.com/api/docs/pricing) |
| glm-5.2-maas | Vertex MaaS | $1.40 | 5.00x | $4.40 | $0.14 | -- | [vertex](https://cloud.google.com/model-garden/partner-models/pricing) |
| kimi-k2.6 | Moonshot | $0.95 | 3.39x | $4.00 | $0.16 | -- | [moonshot](https://platform.kimi.ai/docs/pricing/chat) |
| deepseek-v4-pro | DeepSeek | $1.32 | 4.71x | $3.96 | $0.044 | -- | [deepseek](https://api-docs.deepseek.com/quick_start/pricing/) |
| qwen3-coder-480b | Vertex MaaS | $0.861 | 3.08x | $3.441 | -- | -- | [alibaba](https://www.alibabacloud.com/help/en/model-studio/model-pricing) |
| glm-5 | Vertex MaaS | $1.00 | 3.57x | $3.20 | $0.10 | -- | [vertex](https://cloud.google.com/model-garden/partner-models/pricing) |
| gemini-3-flash-preview | Google | $0.50 | 1.79x | $3.00 | $0.05 | -- | [google](https://aistudio.google.com/docs/pricing) |
| gemini-3.5-flash-lite | Google | $0.30 | 1.07x | $2.50 | $0.03 | -- | [google](https://aistudio.google.com/docs/pricing) |
| kimi-k2-thinking | Vertex MaaS | $0.60 | 2.14x | $2.50 | $0.15 | -- | [moonshot](https://platform.moonshot.ai/) |
| glm-4.7-maas | Vertex MaaS | $0.60 | 2.14x | $2.20 | $0.06 | -- | [vertex](https://cloud.google.com/model-garden/partner-models/pricing) |
| gemini-3.1-flash-lite-preview | Google | $0.25 | 0.89x | $1.50 | $0.025 | -- | [google](https://ai.google.dev/gemini-api/docs/pricing) |
| gemini-3.1-flash-lite | Google | $0.25 | 0.89x | $1.50 | $0.025 | -- | [google](https://aistudio.google.com/docs/pricing) |
| gpt-5.4-nano | OpenAI | $0.20 | 0.71x | $1.25 | $0.02 | -- | [openai](https://developers.openai.com/api/docs/pricing) |
| gpt-5.6-luna | OpenAI | $0.20 | 0.71x | $1.20 | $0.02 | $0.25 | [openai](https://developers.openai.com/api/docs/pricing) |
| deepseek-flash | DeepSeek | $0.30 | 1.07x | $1.20 | $0.006 | -- | [deepseek](https://api-docs.deepseek.com/quick_start/pricing/) |
| deepseek-v4-flash | DeepSeek | $0.30 | 1.07x | $1.20 | $0.006 | -- | [deepseek](https://api-docs.deepseek.com/quick_start/pricing/) |
| deepseek-v4.1-flash-maas | Vertex MaaS | $0.30 | 1.07x | $1.20 | $0.03 | -- | [vertex](https://cloud.google.com/model-garden/partner-models/pricing) |
| qwen3-235b | Vertex MaaS | $0.287 | 1.03x | $0.92 | -- | -- | [alibaba](https://www.alibabacloud.com/help/en/model-studio/model-pricing) |
| gpt-6-luna | OpenAI | $0.10 | 0.36x | $0.50 | $0.01 | $0.125 | [openai](https://developers.openai.com/api/docs/pricing) |
| claude-haiku-5-5 | Anthropic | $0.10 | 0.36x | $0.50 | $0.01 | $0.125 | [anthropic](https://platform.claude.com/docs/en/about-claude/pricing) |
| deepseek-v3.2 | Vertex MaaS | $0.14 | 0.50x | $0.28 | $0.0028 | -- | [deepseek](https://api-docs.deepseek.com/quick_start/pricing/) |

## Provider Notes

### OpenAI

**Verified 2026-11-18** against OpenAI’s
[pricing page](https://developers.openai.com/api/docs/pricing) (Standard tier) and the
GPT-5.6 Sol model page.
`openai.com/api/pricing` refused automated access during this review, so the developer
docs page is the cited source.

- Stored rates are the **short-context** tier, ≤272K input tokens.
  Above 272K, OpenAI bills about 2x input and 1.5x output for the whole request; each
  row’s note gives the long-context rates.
- `cache_write_per_1m` stores the **30-minute** cache-write rate, matching the 5-minute
  convention used for Anthropic.
  OpenAI also publishes a 12-hour write rate, noted per row.
  GPT-5.5 and earlier have no published cache-write rate.
- `gpt-5.6-sol` rates are promotional until **2026-11-30**. OpenAI has not published the
  rate that applies afterwards, so no `list_price` is stored; recheck after that date.
- `gpt-5.6` is a documented alias that routes to GPT-5.6 Sol.
  codex-cli records the requested slug, and price lookup matches IDs exactly, so the
  alias has its own row with the Sol rate card.
- Batch, Flex, Ultrafast, regional-processing uplifts (10%), and the background-window
  discounts on GPT-6.2 Astra, GPT-6.2 Sol, GPT-6.1 Terra, and GPT-6 Luna are not
  modelled.
- `gpt-5.4`, `gpt-5.4-mini`, and `gpt-5.4-nano` are historical rows: OpenAI still prices
  them, but the codex-cli catalog no longer accepts them.

Cached-input discounts now vary within the family (0.05x input for GPT-6.1 and GPT-6.2,
0.1x for GPT-6, GPT-5.6, and earlier), so verify every stored rate against the pricing
page rather than a formula.

**ChatGPT-OAuth dispatch (Vehicle B, via codex-cli).** When a run dispatches through
codex-cli using a ChatGPT subscription OAuth session (`~/.codex/auth.json` with
`auth_mode: chatgpt`), billing is subscription-flat (free-per-request, bundled into the
monthly subscription fee) — **not** metered at the rates above.
The same is true of the Claude-CLI personal-plan dispatch path.
The metered rates in this file apply only to `OPENAI_API_KEY` (Vehicle A) dispatch.

Cost source: `computed` (no adapter in metaproc self-reports OpenAI cost today).
`list_cost` falls back to the same rate card because OpenAI models in this file do not
have a separate `list_price` block.

### Anthropic

**Verified 2026-11-18** against Anthropic’s public
[pricing page](https://platform.claude.com/docs/en/about-claude/pricing).

- Rows cover the Fable 5.x, Opus 5.x, Sonnet 5.x, and Haiku 5.5 generations, plus Opus
  4.8 and the retained 4.x rows.
  The Opus 4.7 rate, previously carried over from 4.6, is now confirmed.
- Cache reads are `0.1x` base input except on Fable 5.1 and Fable 5.2 (`0.025x`) and on
  Opus 5.5 and Sonnet 5.5 (`0.05x`). 5-minute cache writes are `1.25x` and 1-hour cache
  writes are `2x` base input.
  This file stores the 5-minute cache-write rate in `cache_write_per_1m`, because the
  usage logs currently do not distinguish 5-minute from 1-hour writes.
- **Haiku 5.5 is priced by prompt length.** A request whose prompt (input plus cache
  reads and writes) exceeds 100,000 tokens pays 5x the base rates.
  The schema holds one rate card per model, so the `claude-haiku-5-5` row stores the
  ≤100K tier and computed costs for long-prompt work are a lower bound.
- Sonnet 5’s $2 / $10 launch price is now its standard price; the increase Anthropic had
  scheduled for 2026-09-01 did not occur.
- Fast mode, batch (50% off), and the 1.1x US-only `inference_geo` multiplier are not
  modelled.
- Claude Code reports the dated ID `claude-haiku-4-5-20251001` for Haiku 4.5, so that ID
  has its own row with the `claude-haiku-4-5` rate card.
- `claude-sonnet-4-5` is a historical row; Anthropic lists it as deprecated.

Claude CLI reports a list-cost estimate.
The fallback uses the public API rates in `actual_price`.

### Google AI (Gemini)

**Verified 2026-11-18** against Google’s public
[Gemini API pricing page](https://aistudio.google.com/docs/pricing) (the
`ai.google.dev/gemini-api/docs/*` pages now redirect there) and the
[Vertex AI pricing page](https://cloud.google.com/model-garden/pricing).
Rows record the Standard paid tier.

**November 2026 changes:**

- `gemini-4-argon` (GA 2026-10-13) and `gemini-4-carbon` (GA 2026-11-10) bill a
  promotional $2.00 / $10.00 / $0.10 through **2026-11-30** and a standard $4.00 /
  $20.00 / $0.20 from 2026-12-01. `actual_price` holds the promotional rate and
  `list_price` the standard rate.
  After 2026-11-30 the promotional rate no longer applies: the next review should move
  the standard rate into `actual_price`.
- `gemini-4-hydrogen` (GA 2026-11-12) bills $1.50 / $7.50 / $0.075 with no promotion.
  Gemini 4 models support implicit caching only, at 0.05x input, and have no
  context-length tier.
  Vertex publishes a Hydrogen cache-write rate effective 2027-01-12, which is not stored
  yet.
- `gemini-4-neon` was verified on 2026-11-21. Gemini CLI estimates use the Gemini API
  Standard paid rate of $3.00 per million input tokens, $15.00 per million output
  tokens, and $0.15 per million cached input tokens.
  Vertex Standard rates match on the global endpoint, with a 10% surcharge in non-global
  regions. Vertex cache-write pricing starts on 2027-01-12.
  [Gemini API pricing](https://aistudio.google.com/docs/pricing#gemini-4-neon),
  [Vertex AI pricing](https://cloud.google.com/model-garden/pricing).
- `gemini-3.6-flash` corrected: Google charges the same introductory rate as
  `gemini-3.8-flash` ($0.75 / $3.75 / $0.075 through 2026-12-31, then $1.50 / $7.50 /
  $0.15). The row previously stored the standard rate as `actual_price`; it now holds
  the introductory rate in `actual_price` and the standard rate in `list_price`.
- `gemini-3.5-flash-lite` added at $0.30 / $2.50 / $0.03.
- `gemini-3.7-flash` added as a historical row from the Vertex AI page, because the
  Gemini API deprecated it and routes it to `gemini-3.8-flash`.
- `gemini-3.5-flash` is now priced from the Vertex AI page: the Gemini API deprecated it
  and routes it to `gemini-3.6-flash`, so the Gemini API page no longer lists it.
- `gemini-3-pro-preview` and `gemini-3.1-flash-lite-preview` are shut down and no longer
  on the pricing page; their rows keep the 2026-04-02 rate card unverified.

Current model availability, lifecycle, and route evidence are maintained in
`model_catalog.py`; this file records pricing evidence only.

For `gemini-3.1-pro-preview` and `gemini-3.1-pro-preview-customtools`, the stored rate
is the published rate for prompts `<=200K` tokens.
For `gemini-3-flash-preview` and `gemini-3.1-flash-lite`, it is the text, image, and
video input rate; audio input costs more.

Cached-input prices are `0.1x` the standard input rate for the Gemini 3.x models in this
file and `0.05x` for Gemini 4. Vertex AI’s global endpoint charges the Gemini API rates;
its non-global regions charge 10% more.
Context-cache storage charges are not modelled.

The `gemini-3.1-pro-preview-customtools` model is intentionally omitted from the
comparison table because it has the same standard rate card as `gemini-3.1-pro-preview`.

The Gemini Developer API free tier is model-specific, not universal.
As of 2026-11-18, Google’s pricing page shows no free tier for `gemini-4-argon` or
`gemini-4-carbon`, and a free tier for `gemini-4-hydrogen`, `gemini-3.8-flash`,
`gemini-3.6-flash`, and `gemini-3.5-flash-lite`.

### DeepSeek (Direct API)

**Verified 2026-11-18** against DeepSeek’s public
[pricing page](https://api-docs.deepseek.com/quick_start/pricing/).

- **Time-of-day pricing.** DeepSeek charges a peak rate from 01:00 to 04:00 and from
  06:00 to 10:00 UTC, Monday to Friday, excluding Chinese public holidays.
  All other hours bill the off-peak rate, half of peak.
  Usage logs carry no billing window, so this file stores the **peak** rate and computed
  costs are an upper bound.
- `deepseek-flash` is the current Flash ID, served by DeepSeek-V4.1-Flash-1017.
- `deepseek-v4-flash` is a legacy ID that DeepSeek still accepts; its requests are
  served by DeepSeek-V4.1-Flash-1017 and billed at the `deepseek-flash` price, so the
  row now carries that rate card.
  Runs made before that change were billed $0.14 / $0.28 / $0.0028; re-pricing them now
  overstates them.
- `deepseek-v4-pro` is served by DeepSeek-V4-Pro-0813. The May 2026 promotional
  `actual_price` and the $1.74 / $3.48 `list_price` are superseded and were removed.

The direct API rows provide peak-rate estimates when the CLI supplies no cost.
The `deepseek-v3.2-maas` Vertex MaaS row keeps the V4-Flash direct rate from the
2026-05-23 review ($0.14 / $0.28 / $0.0028) as its frozen `list_price`, the comparison
point while that model served.

### Moonshot (Kimi)

**Verified 2026-11-18** against Kimi’s
[model inference pricing page](https://platform.kimi.ai/docs/pricing/chat).
`platform.moonshot.ai` now redirects to `platform.kimi.ai`.

- `kimi-k3.1-pro` ($2.00 / $10.00 / $0.20) and `kimi-k3` ($3.00 / $15.00 / $0.30) are
  the current K3-series models, with flat pricing across the 1,048,576-token context.
  `cache_write_per_1m` stores the 5-minute TTL write rate; Kimi also publishes a 1-hour
  rate, noted per row.
  Both models ship under Pi’s `moonshot` provider in
  [`pi-models.default.json`](pi-models.default.json).
- `kimi-k2.6` is unchanged at $0.95 / $4.00 / $0.16. Kimi closed K2-series models to
  newly registered users on 2026-10-31 and plans to retire them by the end of December
  2026\.

### Vertex MaaS (Third-Party Models)

**Verified 2026-11-18** against Google’s public
[partner and open model pricing page](https://cloud.google.com/model-garden/partner-models/pricing).

- Vertex retired all six previously listed MaaS models on **2026-10-21**: `glm-5-maas`,
  `glm-4.7-maas`, `deepseek-v3.2-maas`, `kimi-k2-thinking-maas`,
  `qwen3-235b-a22b-instruct-2507-maas`, and `qwen3-coder-480b-a35b-instruct-maas`. Their
  rows are kept as historical rows; the pricing page still lists their rates, which
  match.
- Replacements on the global endpoint, both in Preview: `glm-5.2-maas` ($1.40 / $4.40 /
  $0.14) and `deepseek-v4.1-flash-maas` ($0.30 / $1.20 / $0.03; free until 2026-10-16).
  Vertex has no Kimi or Qwen chat successor.
  `deepseek-v4.1-flash-1017-maas` has no published price yet and has no row.
- Pi reports these models with an org prefix (for example `zai-org/glm-5.2-maas`);
  `_lookup_model()` strips it, so rows are keyed by the bare ID.

Where we have a separate, independently verifiable direct-vendor public rate, that rate
is stored in `list_price`. Where we do not, `list_cost` falls back to `actual_price`.
The two replacement rows have no `list_price`: no current Zhipu rate was verified for
GLM-5.2, and DeepSeek’s direct API serves a different snapshot under time-of-day
pricing.
The historical rows keep the `list_price` blocks from their last reviews, frozen
and not re-verified.

For Alibaba Qwen models, `list_price` is deployment-mode-specific.
This file uses Alibaba Cloud Model Studio’s **Global deployment mode** pricing, and for
`qwen3-coder-480b-a35b-instruct-maas` it uses the published **0-32K input tier**.

## Google Credits and Promotions

**Verified 2026-04-02** against Google’s public startup and free-program docs.

- Standard Google startup credits are not “all models” credits.
  Google says program members can use standard startup credits on **Gemini** and
  **Vertex AI open models**. Source:
  [AI startup program](https://cloud.google.com/startup/ai).
- The additional partner-model startup credit is separate, and it is not limited to
  open-source models. Google says qualifying startups may receive an additional
  **$10,000** to use **partner LLM models** through Model Garden.
  Source: [AI startup program](https://cloud.google.com/startup/ai) and
  [Startup Perks](https://cloud.google.com/startup/perks).
- Google’s startup materials explicitly describe that partner-model credit as applying
  to partner models such as **AI21**, **Anthropic**, and **Mistral** in Model Garden.
  Source: [Startup Perks](https://cloud.google.com/startup/perks).
- The standard **$300 Free Trial** is narrower still.
  Google’s Free Trial docs say that the credit cannot be used for **Gemini Developer API
  / AI Studio** costs and cannot be used for a **generative AI partner model offered as
  a managed API / model as a service**. Source:
  [Free Google Cloud features and trial offer](https://docs.cloud.google.com/free/docs/free-cloud-features).
- For Anthropic on Google specifically, this review did **not** find any broader public
  Google-wide Claude discount beyond the startup partner-model credit above.
  Public docs show pay-as-you-go Claude pricing on Vertex plus sales-led custom quotes,
  not a blanket promotion.
  Sources:
  [Vertex AI pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing),
  [AI startup program](https://cloud.google.com/startup/ai), and
  [Startup Perks](https://cloud.google.com/startup/perks).

## How Pricing Flows into Usage Reports

The `metaproc write-usage` command produces `usage.md` files with two cost columns.

- **Actual cost** requires provider billing evidence.
  It is unavailable in reports generated from agent logs alone.
- **List cost** uses the cost reported by the agent CLI when available.
  The fallback multiplies logged token counts by `list_price` or, if absent,
  `actual_price`.

### List-Cost Sources by Provider

| Provider | List-cost basis |
| --- | --- |
| OpenAI (Codex CLI) | API rates in `actual_price` |
| Anthropic (Claude CLI, Pi CLI) | CLI estimate if available, with public API rates in `actual_price` as a fallback |
| Google (Gemini CLI) | Public API rates in `list_price` when present, otherwise `actual_price` |
| DeepSeek (direct API) | CLI estimate if available, with peak API rates in `actual_price` as a fallback |
| Moonshot (Kimi API) | CLI estimate if available, with public API rates in `actual_price` as a fallback |
| Vertex MaaS (Pi CLI) | CLI estimate if available, with rates in `list_price` or `actual_price` as a fallback |

### Key logic in usage.py

- Prices are looked up by the model ID the usage record carries, which is the served ID
  where the adapter reports one (Claude `modelUsage`, Gemini `stats.models`, Pi
  messages) and the requested ID otherwise (codex-cli).
  The match is exact apart from the org-prefix rule below, so aliases and dated IDs that
  adapters report need their own rows.
- Org-prefixed model names (for example `zai-org/glm-5.2-maas` from Pi CLI) are resolved
  by `_lookup_model()` which strips the prefix automatically.

## Known Issues and Uncertainties

### Claude CLI Estimates and Rate Cards

Claude CLI’s list-cost estimate can exceed the estimate computed from this rate card for
the same logged token counts.

**Possible explanations**:

1. Extended-thinking tokens may be billed in the self-reported total while the usage log
   token counts do not expose them separately.
2. The CLI may report costs using a rate basis or token accounting detail not visible in
   the log summary we ingest.
3. The token counts extracted from logs may not exactly match what the API billed for.
4. For `claude-haiku-5-5`, requests over 100,000 prompt tokens bill 5x the stored ≤100K
   rate card.

**How to verify**:

- Compare a single Claude CLI API response’s full `usage` payload against the log entry.
- Compute expected cost from every token category present in that API response.
- Check whether Claude CLI’s reported total includes extended-thinking or other hidden
  token buckets.

### NOTE: One rate card per model

The schema stores a single `actual_price` (and optional `list_price`) per model ID. It
cannot express prompt-length tiers (Haiku 5.5, OpenAI long context, Gemini 3.1 Pro over
200K), time-of-day pricing (DeepSeek), or a rate that changes on a date.
Each affected row states which tier it stores and in which direction computed costs can
be wrong.

### NOTE: Vendor list prices may still be deployment-mode-specific

The `list_price` entries for Vertex MaaS models are exact only for the vendor deployment
mode, tier, and public page cited in `list_source_url`.

Current choices in this file:

- Moonshot: Kimi public API pricing.
- DeepSeek: public DeepSeek API pricing.
- Alibaba Qwen: Model Studio **Global deployment mode** pricing, with the
  `qwen3-coder-480b-a35b-instruct` row using the published **0-32K input tier**.

If you want a different vendor deployment mode, update both `list_price` and the note.

### NOTE: GLM direct-vendor list pricing is intentionally omitted

The older GLM `list_price` block was removed because it could not be independently
verified from a current machine-readable public Zhipu pricing source.
Until that source is available, `list_cost` for `glm-5-maas`, `glm-4.7-maas`, and
`glm-5.2-maas` falls back to Vertex AI’s published public rate, which is exact and
source-backed.

## Data Structure

Each model entry in the YAML frontmatter has an `actual_price` block:

- `input_per_1m` -- cost per 1M input tokens
- `output_per_1m` -- cost per 1M output tokens
- `cache_read_per_1m` -- cost per 1M cached input tokens (cache hits)
- `cache_write_per_1m` -- cost per 1M tokens written to cache

Models with a separately stored vendor-direct public rate, or a standard rate that
follows an introductory rate, also have a `list_price` block with the same fields.
When `list_price` is absent, `list_cost` falls back to `actual_price`.

Additional per-model fields:

- `cost_source` -- `"self-reported"` (adapter reports cost) or `"computed"` (from
  pricing table)
- `source_url` -- official pricing page URL for `actual_price`
- `list_source_url` -- official vendor pricing page URL for `list_price`
- `last_reviewed` -- date of last verification (YYYY-MM-DD)
- `notes` -- free-text context about tiers, regions, or caveats

## How to Update This File

**Schedule:** Monthly, when adding a model to a process spec, or when a provider changes
its rates. Model availability and lifecycle are reviewed separately under
[Model Catalog Maintenance](../../../docs/project/model-catalog-maintenance.md).

### Update procedure

1. **Verify current rates** against the official provider pages listed below.
   Record the applicable API, region, context tier, cache policy, and effective date.
2. **Use secondary catalogs for drift detection.** Compare models.dev, OpenRouter, and
   pi-mono with this file, but do not treat them as rate authority.
3. **Resolve every delta** against a primary provider source.
   Leave the existing rate unchanged and record the uncertainty when no current primary
   source is available.
4. **Recheck Google credit and promotion docs** if this file makes any claims about free
   tiers, startup credits, or partner-model credits.
5. **Update the YAML frontmatter:** prices, notes, `last_reviewed` per model, and
   top-level `last_updated`. Keep rows for models the catalog no longer accepts and
   label them historical.
6. **Update the markdown table and prose below** so it matches the frontmatter exactly.
   The table is sorted by output cost descending.
7. **Run the focused test:**
   `uv --config-file uv.toml run --frozen pytest tests/test_usage.py -q`.
8. **Run `make verify`** and confirm the pricing data is present in the built wheel.
9. **Verify on a recent run when available:** `metaproc write-usage <phase-dir>`.

### When to update

- When adding a new model to a process spec.
- When provider pricing changes.
- When a promotional or introductory rate stored in this file ends.
- When Google changes startup-credit or free-tier scope in a way that affects the prose
  above.
- After a run shows “unknown model” warnings in `usage.md`.

### Official pricing sources (primary — use these)

- **OpenAI:** <https://developers.openai.com/api/docs/pricing> (also
  <https://openai.com/api/pricing>, which may refuse automated access)
- **Anthropic:** <https://platform.claude.com/docs/en/about-claude/pricing> and
  <https://claude.com/pricing>
- **Google Gemini:** <https://aistudio.google.com/docs/pricing>
- **Google Gemini models and deprecations:** <https://aistudio.google.com/docs/models>
  and <https://aistudio.google.com/docs/deprecations>
- **Vertex AI (Gemini):** <https://cloud.google.com/model-garden/pricing>
- **Vertex AI (partner and open models):**
  <https://cloud.google.com/model-garden/partner-models/pricing>
- **DeepSeek:** <https://api-docs.deepseek.com/quick_start/pricing/>
- **DeepInfra:** <https://deepinfra.com/pricing>
- **Moonshot / Kimi:** <https://platform.kimi.ai/docs/pricing/chat>
- **Alibaba Model Studio:**
  <https://www.alibabacloud.com/help/en/model-studio/model-pricing>
- **xAI:** <https://docs.x.ai/docs/models>

### Cross-reference sources (secondary — for validation)

- **models.dev:** <https://models.dev/api.json> — use the `firmware` provider key.
- **OpenRouter:** <https://openrouter.ai/api/v1/models>
- **pi-mono:** <https://github.com/badlogic/pi-mono> — maintains manual corrections for
  upstream pricing errors across 700+ models.

### Google-specific credit and free-tier sources

- Google AI startup program: <https://cloud.google.com/startup/ai>
- Google Startup Perks: <https://cloud.google.com/startup/perks>
- Google Cloud Free Trial / Free Program:
  <https://docs.cloud.google.com/free/docs/free-cloud-features>

### Cache-pricing ratios by provider

These ratios are sanity checks when filling in `cache_read_per_1m` for a new model.
They now vary within provider families, so verify each rate against the provider page.

| Provider | Cache-read ratio to input (rates in this file) |
| --- | --- |
| OpenAI | 0.05x for GPT-6.1 and GPT-6.2; 0.1x for GPT-6, GPT-5.6, and earlier GPT-5 models |
| Anthropic | 0.025x for Fable 5.1 and 5.2; 0.05x for Opus 5.5 and Sonnet 5.5; 0.1x otherwise. 5-minute cache write = input × 1.25; 1-hour cache write = input × 2 |
| Google | 0.05x for Gemini 4 (implicit caching only); 0.1x for Gemini 3.x |
| DeepSeek | 0.02x for `deepseek-flash`; about 0.033x for `deepseek-v4-pro` |
| Moonshot | 0.1x for K3 series; about 0.17x for `kimi-k2.6` |
| xAI | input ÷ 4 (75% discount) for most models |

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
