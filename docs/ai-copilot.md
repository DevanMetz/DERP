# AI Copilot

DERP includes a conversational copilot for looking up ERP records, drafting
business documents, and proposing inventory changes. It is intended to help a
signed-in user work within one self-hosted installation.

## Opening It

Open the copilot panel from authenticated DERP pages. The OpenAI API key and
chat history are kept in browser `localStorage` for this installation URL; the
server does not persist the user's API key.

Without an API key, DERP provides a limited rule-based planner for basic
searches and draft requests.

## Supported Work

The copilot can prepare:

| Request | Result |
| --- | --- |
| `bought 5 PLA filament from BambuLab at $20 each` | Draft purchase order |
| `sold 3 widgets to Acme for $50 each` | Draft sales order |
| `build 50 widgets` | Draft manufacturing order |
| `received 100 WIDGET at $5 each` | Stock receipt proposal |
| `wrote off 5 damaged widgets` | Stock issue proposal |

It can also look up products, vendors, customers, warehouses, stock levels,
BOM usage, and document context for the page the user is viewing.

Drafting honors product workflow settings: purchasable products for purchase
orders, sellable products for sales orders, and manufacturable products with
an active BOM for manufacturing orders.

## Safety Model

The copilot never performs a write directly from a chat message:

1. The server creates a preview of the proposed action.
2. The UI presents a signed, time-limited confirmation action.
3. A permitted user explicitly confirms the operation.
4. DERP performs the write and records the outcome.

Additional controls:

- Only Admin, Manager, and Staff roles can confirm write actions.
- New sales, purchase, and manufacturing documents are created in draft status.
- Inventory movements use the existing atomic inventory services.
- Chat, preview, confirm, and error activity is logged in `CopilotAuditEvent`.
- Financial posting, voiding, and manual journal creation remain outside the
  copilot workflow.

## Agent Hub

Agent Hub at `/derp/agents/` saves reusable prompts for a user. Selecting
**Open in Copilot** loads the prompt into the composer for review; it does not
send or schedule work automatically.

See [Agent Hub](./agent-hub.md).

## Developer Reference

- `core/ai_agent.py`: tool definitions, prompt planning, and confirmations.
- `core/views.py`: `ai_chat` and `ai_confirm` HTTP endpoints.
- `core/models.py`: `CopilotAuditEvent` and `AgentRoutine`.
- `templates/base.html`: panel UI and browser persistence.
