# Agent Hub

Agent Hub is the starting point for reusable, user-owned AI routines in DERP. A routine stores a name, a purpose, a prompt, and an optional cadence note so repeated tasks do not need to be reinvented each time.

## What is available now

- Each signed-in user sees only their own saved routines.
- Admin, Manager, and Staff users can create, edit, pause, enable, and delete routines.
- **Open in Copilot** places the saved prompt in the Copilot composer for review before you send it.
- Routine prompts never contain or persist the browser-only OpenAI API key.
- Any ERP write initiated from a routine still uses Copilot's signed preview and explicit confirmation flow.

## A good first routine

Create a routine such as:

```text
Name: Purchasing follow-up
Purpose: Review buying work I need to complete.
Cadence note: Weekday mornings
Prompt: Review my open purchase orders and recent purchase context. Summarize what needs attention. Do not create or post anything unless I explicitly ask.
```

Click **Open in Copilot**, inspect or edit the prompt, and send it when ready.

## Boundaries

Agent Hub is intentionally manual in its first version. It does not create a Windows startup process, schedule unattended tasks, control your computer, read email or calendars, or connect to home automation services.

Routines may contain personal working instructions, so do not put passwords, tokens, API keys, or other secrets in them. The data export screen hides routines by default; users must explicitly include sensitive models to export them.

## Planned extension points

The safe next additions are explicit connectors and controlled triggers:

- Calendar or email read-only briefings.
- Local computer maintenance reports with narrowly scoped tools.
- Scheduled runs with an activity inbox and a master pause control.
- Per-connector permissions and approval requirements for actions.

Those additions should keep the same principles as the current Copilot: user visibility, least privilege, auditability, and confirmation before consequential writes.
