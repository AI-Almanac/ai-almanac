# Letting the assistant drive the interface

Status: **phase 1 built**, 2026-11-11 (the channel, the panel registry, `open_panel` and
`show`). The decisions taken while reviewing the draft are listed at the end, followed
by where the build differs from this design.

## What we want

The assistant should be able to operate the interface on the user's behalf, not only
talk about it:

- open a setup panel (benchmark, blend, forecast) already filled in;
- draw attention to a specific run, view or metric;
- pan and zoom a map to an area of interest;
- answer "How do I run another benchmark?" or "How do I add my own observation data?"
  by showing the way, not only describing it.

Two rules already settled for the workspace still hold:

- **The assistant is optional.** Everything it can do, the user can do by hand. An
  assistant action lands on a control the user could have reached themselves.
- **The assistant never spends or submits by moving the interface.** Starting a run is
  the user pressing a button, or an approval card.

## Before this design

Before the action vocabulary, the assistant reached the interface in three unrelated ways:

| Path | How it travels | Where it works |
| --- | --- | --- |
| Fill a setup form | `benchmark_config` / `blend_config` stream events, callbacks through `ChatPanel` | Benchmarks and Blends pages |
| Open a file or run | `artifact:` / `run:` links in the reply, opened by a click handler | Workspace |
| Propose a run | Approval cards | Everywhere |

Each was built on its own. Adding an ability has meant a new event type, a new callback
and new handling in the page. That will not stretch to map control and guided help.

## The core idea: interface actions are a client-side vocabulary

The interface defines a small set of **actions**: named, typed things it knows how to
do. The assistant is one of several things that can trigger them.

```
            suggestion while typing ─┐
            help menu ───────────────┤
            buttons, starter prompts ┼──▶  action dispatcher  ──▶  page handlers
            chips in the transcript ─┤        (in the browser)
            assistant tool call ─────┘
```

An action is an intent with a payload, never a click or a selector:

| Action | Payload | Effect |
| --- | --- | --- |
| `open_panel` | a panel by name, and for a setup panel a draft | Opens that panel, filled in when a draft is given |
| `show` | a run, a file, or a view of a run | Opens it on the stage |
| `set_map_view` | area, and optionally metric, lead time, model | Moves the map there |
| `highlight` | a named part of the interface | Briefly points at it |
| `start_guide` | a guide id | Runs that walk-through |

Each page registers handlers for the actions it supports. A page with no handler for an
action ignores it; the chip in the transcript (below) still says what was offered.

Putting the vocabulary in the browser, with the assistant as one trigger among several,
is what makes the "no round trip" case possible: the same action can be fired with no
assistant involved at all.

## How the assistant triggers an action

The existing chat stream already carries what is needed. Every tool call is sent to the
browser as `tool_call` and `tool_result` events and is stored with the turn.

- Each action has a matching assistant tool (`open_panel`, `show`, …).
- The server-side tool does one thing: parse the arguments with the action's schema and
  return the validated action as its result. It does not wait for the browser and does
  not know whether the browser acted.
- The browser sees the `tool_result`, recognises an action, and dispatches it.

So there is no new event type and no extra request: the interface reacts to tool
results that are already streaming. The cost of an assistant-triggered action is the
assistant turn itself.

Because tool calls are stored with the turn, every action is also **a chip in the
transcript** ("Opened blend setup"). The chip is a button that fires the action again.

Rules for when an action fires by itself:

- It fires once, live, when it arrives in a turn the user just started by sending a
  message.
- It does not fire when a conversation is reloaded: chips only.
- It does not fire in a turn that ran unattended (a queued follow-up after a run
  finished): chips only. The interface must not move while the user is doing something
  else.

## Help without the assistant

Anything typed into the chat box and sent is an assistant turn: seconds and tokens,
whatever happens afterwards. "Fully client-side" therefore means answering *before* the
message is sent, or outside the chat altogether. Silently intercepting a sent message
and returning a canned answer is rejected: the user asked the assistant and should get
the assistant.

What we can do:

1. **A catalogue of guides**: a short list of how-tos ("Run a benchmark", "Train a
   blend", "Add observation data"…), each with an id, a title, a few words to match on,
   and a walk-through built on the existing tour machinery (`data-tour` anchors,
   `queueTour` for a guide that starts on another page).
2. **Suggestion while typing**: as the user types something that matches a guide, a
   suggestion appears above the composer: "Show me: Run a benchmark". Clicking it runs
   the guide in the browser. No request, no assistant.
3. **A help menu** listing every guide, for people who do not use the assistant.
4. **If the message is sent anyway**, the assistant has the guide list (titles and ids)
   in its instructions, answers in a sentence or two, and calls `start_guide`. One
   assistant turn, no lookup step.

The same guides serve all four. The server owns the list (ids, titles, matching words)
as a packaged config file: it goes into the assistant's instructions and the browser
fetches it once. The walk-through steps bind to the page, so they stay in the frontend.
A test fails if a guide in the list has no walk-through.

A guide whose task still lives on another page (adding observation data is on the Data
page today) takes the user there, and its last step offers the way back to the
workspace they came from. That is an interim: see the next section.

## The workspace as the one place: panels

The direction is for a workspace to be the one place the whole platform is used from.
Not everything moves in now, but the design should make moving something in a small,
regular piece of work.

The workspace already opens things over the stage: the benchmark, blend and forecast
setup forms and the run picker. That becomes a named **panel registry**:

- A panel has a name, a title, a component, and a form: over the stage (run setup) or a
  modal dialog over the whole workspace (registering data, settings).
- The workspace opens a panel by name. The "New" menu, a guide step, a chip and the
  assistant's `open_panel` action all go through the same call.
- Bringing a page into the workspace means factoring its form into a reusable
  component, as was done for the three setup forms, and registering it as a panel. The
  old page keeps rendering the same component until it is retired.
- A guide that used to send the user to another page is then edited to open the panel
  instead; nothing else about it changes.

So `open_panel` is the one action for "bring up this piece of the platform", and the
list of panels is what grows as the platform moves into the workspace. Likely first
additions after run setup: registering observation or forecast data, and settings.

## What the assistant knows about the screen: nothing

The workspace is treated as stateless from the assistant's side. The browser sends no
snapshot of what is on screen, and an action's tool result says only that the action
was offered.

Why:

- **Simplicity.** Tools are fire-and-forget. No state to keep in step, no handshake.
- **Cost.** A screen snapshot on every message is tokens on every message.
- **Honesty.** Any snapshot is stale the moment the user clicks something.

What this costs, and how it is handled:

| Lost | Handling |
| --- | --- |
| "Zoom in a bit more", "the other model" | Actions are absolute and repeatable: "show Awi zone", never "zoom in 2×". The assistant's own earlier actions are in its conversation history, so follow-ups to what *it* did still work. |
| "What am I looking at?", "explain this zone" | Not answerable from ambient state. Instead the user attaches the thing: an "Ask about this" control on a run, a map cell or a table row puts a reference into their message. Context is explicit and user-initiated. |
| The assistant cannot tell whether the action happened | It is told to say what it offered ("I've set up a blend form with AIFS and FuXi"), never that something was submitted. The chip is always there as the fallback. |

This is the position for now, not a permanent rule. If a later feature truly needs the
assistant to read the screen, the route is a tool the browser answers, added beside this
channel.

"Ask about this" is a later step. It uses the same mechanism the workspace already has
for dropping a prompt into the composer.

Which actions the assistant is offered follows from where the conversation lives: a
workspace conversation gets the workspace's actions. No per-message capability list is
needed. A browser that receives an action it does not know shows the chip as plain
text.

## Trust

The assistant reads fetched data, file names and run names, any of which could carry
instructions. So:

- Actions are a closed list. There is no "click", "navigate to URL" or "run script".
- The browser treats an action's payload as untrusted input: it parses it again and
  acts only on things the page has already loaded for this user. A run that is not in
  the workspace is not opened.
- A draft for a setup form is parsed with the same schema the form submits, and the
  form's own validation still runs before anything can be started.
- Guides are picked by id from the catalogue; an unknown id does nothing.

## How the pieces fit the code

| Piece | Where |
| --- | --- |
| Action schemas | `src/ai_almanac/server/services/interface_actions.py` (pydantic) on the server; `web/src/lib/interface-actions/actions.ts` parses them in the browser |
| Assistant tools | The `interface` toolset in the same module, attached to workspace conversations |
| Dispatcher | `web/src/lib/interface-actions/dispatch.ts`; the workspace page holds the handlers |
| Chips | `InterfaceActionCard.svelte`, a result card beside the existing ones |
| Panel registry | `web/src/lib/workspace/panels.ts` and `WorkspacePanel.svelte`, replacing the workspace page's hand-written list of stage panels |
| Setup panels | `BenchmarkForm`, `BlendForm` and `ForecastForm` already take a setup to start from |
| Guides | Existing tours (`web/src/lib/tour.svelte.ts`, per-page `tours.ts`) |

The existing `artifact:` / `run:` links become `show` actions. The Benchmarks and Blends
pages keep their `benchmark_config` / `blend_config` events for now and move to
`open_panel` in a later pass.

## Phases

1. **Channel and setup panels.** Action schemas, the dispatcher, chips and the firing
   rules. The panel registry, and `open_panel` for benchmark, blend and forecast setup. `show` for
   runs and files, replacing the link handling.
2. **Guides.** The catalogue, the suggestion while typing, the help menu, `start_guide`.
   First guides: run a benchmark, train a blend, add observation data. Guides for tasks
   outside the workspace go to the page and link back.
3. **Map and focus.** `set_map_view`, `show` for a specific view or metric, `highlight`.
4. **"Ask about this".** User-attached references in a message.

Alongside these, pages move into the workspace as panels one at a time; each is its own
piece of work and none blocks the phases above.

Phase 1 changes `src/ai_almanac/server/`, so it needs the security review before a push.

## Decisions

Taken on 2026-11-11 while reviewing the draft.

1. **Form and approval card both stay.** In the workspace, "run it" gets an approval
   card as now. "Set up a blend with AIFS and FuXi", or anything the assistant is unsure
   of, opens the filled-in form.
2. **The extra model step is measured before it is optimised.** After a tool call the
   model is called again to finish its reply. Phase 1 ships with that, measures what it
   adds, and ending the turn straight after an action is looked at only if it is
   noticeable.
3. **The server owns the guide catalogue.**
4. **Everything is designed to come into the workspace, as panels.** Until a page has
   been brought in, a guide that needs it goes there and links back.
5. **The assistant sees no screen state, for now.**

## Where phase 1 differs from the design

- **The browser parses actions by hand.** The types are not generated from the server's
  schemas. The browser has to parse an untrusted payload field by field anyway, so a
  generated type would only have described what that parser returns.
- **The server checks a draft's ids against the catalog.** The design had the tool only
  parse and return. A draft naming a model or dataset that does not exist is sent back to
  the assistant as an error, so it can correct itself before the user sees an empty form.
- **Panels open over the stage only.** The registry names panels and opens them one way,
  but the modal-dialog placement is not built: nothing needs it until the first page
  (Data, Settings) is brought in.
- **`artifact:` and `run:` links still open through their own click handler.** They do the
  same thing as a `show` action but were not rewired onto the dispatcher.
- **The forecast form sets up a blend's forecast only.** The assistant can also run a raw
  forecast with no blend through its approval card; the form has no such option yet.
- **One thing on the stage at a time.** A form opened and a run shown in the same reply
  leaves the run showing; both stay as buttons in the conversation.
- **Only the action tools can trigger an action.** The browser reads an action from the
  result of `open_panel` or `show` and from no other tool, so data another tool returns
  can never move the interface.
- **An open form is left alone.** If the user already has a panel open, an action from
  the assistant does not replace it; it waits as its button in the conversation.

The extra model step after an action has not been measured yet. In the one live trial
the filled-in blend form appeared within 45 seconds of the user's message, after the
assistant had looked up models and datasets; that was not timed more closely.
