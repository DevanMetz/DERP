# AI Copilot

DERP ships with a built-in conversational copilot that translates plain-English requests into ERP actions. It can draft purchase orders, sales orders, and manufacturing orders, look up records, and answer questions about your data — all with a preview-confirm safety net for anything that writes.

## Opening it

The robot icon at the bottom-right of every authenticated page opens the copilot panel. Paste an OpenAI API key in the field at the top (browser-only, never sent to the DERP server) and start typing.

The panel's open/closed state and chat history are persisted in browser `localStorage`, scoped per tenant subdomain. The **Clear history** button wipes both the visible messages and the persisted history.

## What it can do

### Draft documents

The copilot can draft three document types. All three follow a preview → confirm pattern: the copilot proposes a draft, you click **Create Draft PO/SO/MO** to commit it.

| Verb cues | Doc | Example |
|---|---|---|
| `bought`, `purchased`, `order from` | Purchase order | `bought 5 PLA filament from BambuLab at $20 each` |
| `sold`, `selling`, `ship to` | Sales order | `sold 3 widgets to Acme for $50 each` |
| `manufacture`, `produce`, `build` | Manufacturing order | `build 50 widgets` |

Documents are always created in `DRAFT` status. Confirming, posting, completing, or voiding still happens through the regular UI workflows so the irreversible steps stay deliberate.

### Look up records

- `find vendor Acme`
- `search products with sku WIDGET`
- `do any BOMs use PLA filament?`
- `what BOMs use widget?`
- `what's the stock on PLA filament?`
- `show recent purchase prices for PLA filament from BambuLab`

### Answer questions about the page you're on

The copilot reads the URL. On a record detail page, references like "them", "this", or "it" resolve to that record automatically:

- On `/customers/123/`: *"what did they buy last month?"* → fetches Acme's recent sales orders and invoices
- On `/vendors/8/`: *"show our history with them"* → fetches recent POs and bills
- On `/purchase-orders/12/`: *"what's on this PO?"* → fetches the lines
- On `/boms/4/`: *"make 100"* → drafts an MO using that BOM directly
- On `/products/W-001/`: *"draft a PO for 50 from BambuLab"* → product pre-filled, only vendor needed

### Use defaults when you don't have all the info

Say *"try your best"*, *"just do it"*, *"go ahead"*, or *"whatever works"* and the copilot fills in sensible defaults:

- **PO**: qty=1, unit_cost from `product.cost`, first active vendor if none specified
- **SO**: qty=1, unit_price from `product.price`, first active customer if none specified
- **MO**: qty=1, first active BOM if none specified

## Multi-turn slot filling

The copilot carries partial information across turns. You can build a draft conversationally:

```
You: make a po for pla filament
Bot: Pending PO so far — product: PLA Filament. Still need: vendor, quantity, unit cost.

You: bambu lab vendor
Bot: Pending PO so far — vendor: BambuLab, product: PLA Filament. Still need: quantity, unit cost.

You: 10 units at $20
Bot: Ready to create a draft PO for BambuLab totaling $200.00.
```

The fuzzy search tolerates misspellings and missing spaces — `bambulab` matches `Bambu Lab`, `pla filiment` matches `PLA Filament`.

## Safety model

The copilot never writes to the database without a confirm step:

1. The model proposes an action and the server returns a signed, time-limited (30 min) preview token
2. The preview is rendered as a card with **Create Draft** and **Cancel** buttons
3. Clicking the button POSTs the signed token to a separate confirm endpoint, which validates the token and creates the record
4. The newly created record is returned with a link so you can open it

Other safety features:
- **Role check**: only Admin, Manager, and Staff roles can confirm draft creation
- **Audit log**: every chat, every preview, every confirm is logged to `CopilotAuditEvent` (per-tenant table)
- **No financial posting**: the copilot only creates documents in `DRAFT` status — posting to the GL still requires explicit user action through the document's own UI
- **Per-tenant isolation**: chat state lives in the session and `localStorage`, both scoped per tenant subdomain

## What it cannot do (yet)

- Post invoices or bills to the GL
- Void or reverse posted documents
- Create manual journal entries
- Confirm or complete a manufacturing order

These are intentional gaps. They touch the ledger or the audit trail in ways that we want a human-in-the-loop for.

## Bring your own key

The OpenAI API key is stored in `localStorage` on your browser and sent with each chat request. The DERP server never persists it. If no key is configured, the copilot falls back to a limited rule-based planner that handles searches and simple draft requests but can't interpret free-form natural language.

To use the copilot fully, get an API key from [platform.openai.com](https://platform.openai.com/api-keys) and paste it into the panel's API key field. Cost per chat turn is typically fractions of a cent on the default model.

## Tunable internals

For developers extending the copilot, the relevant places are:

- `core/ai_agent.py` — tool definitions, LLM prompt, slot-filling, page-context resolution
- `core/views.py::ai_chat` and `ai_confirm` — the two HTTP endpoints
- `core/models.py::CopilotAuditEvent` — per-tenant audit log
- `templates/base.html` — chat panel UI, localStorage persistence, preview card rendering

To add a new draft-able document type:
1. Add a `_tool_draft_<type>_preview` and `_confirm_create_<type>` in `ai_agent.py`
2. Add a `pending_<type>` state slot
3. Update `_detect_doc_intent` with verb signals
4. Register the tool in the LLM enum and add an example to the system prompt
5. Update the frontend `renderAiPreview` to display the new shape
