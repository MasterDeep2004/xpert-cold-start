# Cold Start — approach and trade-offs

## What I focused on

I put most of the effort into getting the people/chat map and the first pass at open loops right. Those are the parts where the messages provide repeated evidence: who is in the same work rooms, which conversations are about a customer or an internal team, and where somebody is asking for a concrete next step. I kept goal extraction conservative because a short WhatsApp history contains plenty of passing ideas that are not necessarily personal goals.

## My approach

The program is a small Python pipeline with no external dependencies and no model calls.

- It reads the JSONL files once, builds per-chat and per-person context, and uses the signup seed as a hard override for circles.
- It classifies relationships from repeated context rather than a single keyword. Work-room membership, customer onboarding rooms, family-group context, and domestic-work language are treated as different kinds of evidence.
- It creates a small set of organisation candidates and maps chats using names, group context, and shared participants. A direct chat stays attached to a person unless there is a stronger organisation signal.
- It extracts goals only when a first-person aspiration is stated explicitly. It does not turn every `want`, `need`, or work request into a goal.
- It extracts action-shaped messages as loop candidates, assigns a conservative owner/type, and only marks a loop done when a nearby later message contains a clear completion signal. Open family or staff matters are left alone; safe administrative follow-ups can be prepared for review.

The implementation is intentionally rules-first. It is cheap, deterministic, easy to inspect, and does not transmit private WhatsApp messages to another service. The downside is that it cannot resolve ambiguous relationships or implied commitments as well as a capable language model could.

## What it costs

There are no model calls, so the expected model cost is **$0.00 per 1,000 messages**. Runtime is local Python and standard-library JSON processing. This is a deliberate trade-off: I chose predictable cost and reproducibility over asking a model to interpret every message. `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` are therefore not needed by this version.

## Where it is wrong

On the supplied development set, the checker reports **22.4 / 56 data points**: circles 0.61, organisations 0.42, chats 0.91, open-loop discovery 0.22, and loop status 0.09. Goals and help are not labelled in this set, so there is no development score for those parts. The stated baseline reports 17.5 / 56 on the same data-scored portion, so this version is 4.9 points higher on the supplied development set. This is a development result, not a prediction of hidden-set performance.

The largest weaknesses are predictable:

1. **Sparse contacts:** a person who sends only photos or one-word replies is hard to distinguish as staff, relative, vendor, or friend. I expect circle errors to remain common where there is no explicit relationship language.
2. **Organisation boundaries:** a person can work across multiple companies, and a customer group may mention the platform or company without belonging to it. Organisation matching is the least reliable part of the current development run.
3. **Loop precision and closure:** imperative language catches real tasks, but it also catches routine operational instructions and conversational requests. The development run found 17 of 74 labelled loops (about 23% recall) and predicted 81 candidates; that is not yet good enough. Closure is deliberately cautious because falsely saying a task is done can hide work that is still pending.
4. **Help decisions:** the rules can recognise obvious personal/staff contexts, but they do not understand every social boundary. This needs more evaluation against the hidden labels before making aggressive `assist` or `remind` decisions.
5. **Goals:** the extractor may miss goals expressed indirectly. I prefer missing an ambiguous goal to inventing one without evidence.

If I had more time, I would first improve loop candidate ranking and status resolution against the development answer key. In particular, I would add a conversation-level state tracker so that repeated mentions of the same task are merged and completion messages are linked only when their subject is clear. I would then improve organisation membership using a small number of carefully chosen model calls rather than sending the full history to a model.
