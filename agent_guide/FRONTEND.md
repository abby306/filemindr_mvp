# FRONTEND.md — filemindr

Machine-readable design tokens + component/screen specs. The contract between `FileMindr_Design_System.pdf` and the code. Edit token values here; agents read this, not the PDF.

Aesthetic in one line: **Apple-minimal frame, provenance-led identity.** One accent (iris), two type families, three weights, calm spring motion. Boldness spent only on: provenance (citation ⇄ source), the retrieval trace, and data-as-mono.

## Stack
Next.js + TypeScript · Tailwind (tokens as CSS vars) · Radix UI (a11y primitives) · Framer Motion (the 3 signature motions) · Recharts/Visx (analytics) · TanStack Query (server state). Fonts via `next/font`: **Inter** (UI/prose) + **Geist Mono** (data); SF Pro / SF Mono on Apple platforms.

## Design tokens — CSS variables

```css
:root {
  /* accent — iris (the one bold color) */
  --accent-50:#EEF1FD; --accent-100:#DDE3FB; --accent-300:#9DAEF5;
  --accent:#3D63DD; --accent-hover:#3151C0; --accent-active:#2A44A0;

  /* neutral — graphite */
  --n-0:#FFFFFF; --n-50:#F7F8FA; --n-100:#F1F2F5; --n-200:#E0E3E8;
  --n-300:#CBCFD6; --n-400:#A3A9B4; --n-500:#767D8A; --n-600:#545B68;
  --n-700:#3A404B; --n-900:#14171C; --n-950:#0C0E12;

  /* semantic (light) */
  --canvas:var(--n-50); --surface:var(--n-0); --surface-2:var(--n-100);
  --border:var(--n-200); --border-strong:var(--n-300);
  --text-1:var(--n-900); --text-2:var(--n-600); --text-3:#868D99;

  /* status */
  --ok:#1F9D57; --warn:#E0922F; --danger:#D8443C; --idle:#767D8A; --info:var(--accent);

  /* radius */
  --r-sm:8px; --r-md:12px; --r-lg:16px; --r-xl:22px; --r-pill:999px;

  /* elevation */
  --e1:0 1px 2px rgba(20,23,28,.06), 0 1px 1px rgba(20,23,28,.04);
  --e2:0 4px 12px rgba(20,23,28,.08), 0 2px 4px rgba(20,23,28,.05);
  --e3:0 16px 48px rgba(20,23,28,.16);

  /* motion */
  --ease-quiet:cubic-bezier(.32,.72,0,1);
  --ease-standard:cubic-bezier(.4,0,.2,1);
  --dur-micro:160ms; --dur-base:220ms; --dur-emphasis:320ms; --dur-sheet:420ms;
}

:root[data-theme="dark"] {
  --canvas:#0C0E12; --surface:#14171C; --surface-2:#1B1F25;
  --border:#252A32; --border-strong:#3A404B;
  --text-1:#F1F2F5; --text-2:#A3A9B4; --text-3:#767D8A;
  --accent:#7B93FF; --accent-hover:#93A6FF; --accent-active:#AEBCFF;
  --ok:#3DD68C; --warn:#F0B440; --danger:#FF6B61; --idle:#767D8A;
  /* dark lifts via surface + border; --e3 only for modals */
}
```

## Type scale
| Token | size/line | weight | tracking | use |
|---|---|---|---|---|
| display | 34/41 | 600 | -0.02em | page titles, hero |
| title1 | 28/34 | 600 | -0.02em | section heads |
| title2 | 22/28 | 600 | -0.01em | sub-sections |
| title3 | 18/24 | 600 | -0.01em | card titles |
| headline | 16/22 | 600 | 0 | emphasis |
| body | 15/23 | 400 | 0 | reading text |
| callout | 14/20 | 400 | 0 | secondary text |
| subhead | 13/18 | 500 | 0 | labels |
| caption | 11/14 | 500 | 0.01em | meta, badges |
| **mono-data** | 13/18 | 450 | 0 | **facts, IDs, amounts, dates, trace** |

Spacing: 4pt grid — `2 4 8 12 16 20 24 32 40 48 64 80`. Weights allowed: 400 / 500 / 600 only.

## Signature motions (Framer Motion)
1. **Pipeline fill** — upload card stage pips advance `received→ocr_done→extracted→indexed`; active stage pulses 1.2s. Bind to real backend status.
2. **Trace reveal** — retrieval steps stream in, 120ms stagger, fade+rise 8px.
3. **Citation glow** — hover tints source region (160ms); click smooth-scrolls + 600ms highlight pulse.

All motion: honor `prefers-reduced-motion` → opacity-only, no transform.

## Components (token-driven; no inline colors/radii/durations)
Actions: Button (primary/secondary/ghost/destructive), IconButton, SegmentedControl, CommandPalette (⌘K).
Inputs: TextField, Search, Dropzone, Select/Combobox, Toggle, Slider, ChipInput (classes).
Containers: Card, DocumentCard, SidePanel, Modal, BottomSheet, Tabs, Accordion (trace), Table.
Signals: StatusBadge, ConfidenceBar, ClassChip, CitationPill, Toast, Tooltip, Skeleton, EmptyState.
Nav: Sidebar, TopBar, Breadcrumb, AccountSwitcher (personal ⇄ company).
Data: StatTile, Line/Area/Bar chart, Sparkline, UsageMeter, PricingCard.

## Screens → key components & behavior

### Upload  (`/`)
Dropzone (any file) → optimistic DocumentCard in `received`, animates via Pipeline fill. Multi-file, paste, browse all funnel to one flow; duplicates recognized. Copy is user-side ("Drop files here", "12 indexed").

### Document view  (`/documents/{id}`)
Split: source render (left) ⇄ card (right). Card = title, summary, ClassChips + ConfidenceBar, **typed facts in mono with `↩` provenance jump (signature)**, entities (people/orgs/places), dates-with-roles, "N facts indexed" (the glimpse, no vectors exposed). `+ add class` creates/labels user classes. Empty class set is a calm valid state.

### Ask  (`/chat`)
Streaming answer + collapsible **trace** (Trace reveal motion) naming retrieval steps in plain language. Inline numbered CitationPills (click-to-source). Numeric answers from typed facts — trace says so. Scope toggle: whole archive / this document. "Unsupported" honesty path. Rating row under each answer.

### Ratings
Thumb up/down (1 tap) + optional 1–5 stars. Low rating opens diagnostic reasons: `not grounded · missing document · wrong number · wrong document` + note. Writes to the answer's retrieval trace → feeds analytics + eval.

### Analytics  (`/analytics`)
Two lenses. **Usage:** documents over time, queries/day, storage, top classes, token spend, most-asked docs. **Quality:** answer rating %, grounded %, retrieval latency, extraction success. All derived from `processing_events` + `retrieval_traces` + usage events. Sparse charts, neutral ink, single accent series.

### Billing  (`/billing`)
Plan card + UsageMeters (documents / queries / storage) using status palette (amber→red near limit). PricingCards (Free/Pro/Team) mapped to real cost drivers. Invoices + payment management. Team tier unlocks shared company accounts + audit.

## Quality floor
Responsive to mobile; touch ≥44px; visible keyboard focus; AA contrast; color never the sole signal; real empty/loading/error states (direction, not mood). Every value traces to a token.
