## What this changes

<!-- One or two sentences. What is different after this merges? -->

## Why

<!-- The problem being solved. Link an issue if there is one. -->

## How it was verified

<!-- Be specific. "Ran the tests" is fine only if you also say which
     behaviour they now cover. If you tested against real OBS or a real
     microphone, say so and say what you saw. -->

- [ ] `ruff check .` passes
- [ ] `cd backend && pytest` passes
- [ ] Added or updated tests covering this change
- [ ] Suite still passes with no Bible database present

## Live-service safety

- [ ] Nothing here can raise into the projector display path
- [ ] Optional components (OBS WebSocket, LLM fallback, cloud STT) still
      degrade gracefully when unavailable
- [ ] No new required dependency, or the reason is explained above

## Notes for the reviewer

<!-- Anything you are unsure about, or deliberately left out of scope. -->
