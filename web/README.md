# Multi-Agent AI System — Web Interface

## Overview

Modern, professional web interface for the Multi-Agent AI Business Planning System.

## Files

### Landing Page (`static/landing.html`)
- **Purpose**: Public-facing landing page showcasing the system
- **Features**:
  - Hero section with gradient effects and animated elements
  - Agent grid visualization (Mother Agent + 9 specialists)
  - Feature cards with hover effects
  - Technology stack showcase
  - CTA section and footer
- **Design**: Dark mode, glassmorphism, gradient accents
- **Colors**: Primary (#2563eb), Secondary (#7c3aed), Dark background (#0f172a)

### Dashboard (`static/index.html`)
- **Purpose**: Internal CEO dashboard for business planning
- **Features**:
  - Real-time chat interface with WebSocket
  - Pipeline trace visualization with pan/zoom/drag
  - Knowledge base with epistemic status tracking
  - Decision approval flows (Yes/Adjust/Kill)
  - Session history and state management
- **Tech**: Vanilla JS, WebSocket, SVG animations, CSS Grid

## Design System

### Typography
- **Headings**: Inter (400-700)
- **Monospace**: JetBrains Mono (400-600)
- **Body**: Inter

### Color Palette
```css
--primary: #2563eb      /* Blue */
--primary-dark: #1e40af
--primary-light: #3b82f6
--secondary: #7c3aed    /* Purple */
--success: #16a34a      /* Green */
--warning: #f59e0b      /* Amber */
--bg: #0f172a           /* Dark slate */
--bg-light: #1e293b
--text: #f8fafc         /* White */
--text-muted: #94a3b8   /* Slate */
--border: #334155
```

### Components

#### Agent Cards
- 112×90px cards with icon, name, ID
- States: idle, revealed, processing, done
- Progress bar at bottom
- Spinner/check indicators

#### Feature Cards
- White/dark background with border
- 56×56px gradient icon
- Hover: translateY(-4px) + shadow
- Border color changes on hover

#### Buttons
- Primary: Blue gradient, shadow on hover
- Secondary: Transparent with border
- Hover: Transform translateY(-2px)

#### Status Badges
- Pill-shaped (border-radius: 9999px)
- Color-coded: confirmed, assumption, inferred, contradiction
- JetBrains Mono, 9-11px, uppercase

### Responsive
- Desktop: 1280px max-width
- Tablet: 768px breakpoint
- Mobile: Single column, hidden nav

## Routes

### Landing
- `/web/static/landing.html` — Public landing page

### Dashboard
- `/web/static/index.html` — Main dashboard
- Tabs: Chat | Pipeline Trace | Knowledge Base

## Backend API (Expected)

```
GET  /api/health
GET  /api/session-key?token=
GET  /api/messages/:session_key?token=
POST /api/messages
      { text, token }
GET  /api/knowledge-base?token=
POST /api/knowledge-base/add
      { topic, fact, status, token }

WS   /ws/:session_key?token=
      → { role, text, timestamp }
      → { role: 'trace', agent, step, detail }
      → { role: 'status', text }
```

## WebSocket Trace Protocol

```json
{
  "role": "trace",
  "agent": "Opportunity",
  "step": "analyzing market",
  "detail": "Researching TAM..."
}

{
  "role": "trace",
  "agent": "Financial",
  "step": "complete"
}
```

## Running

```bash
# Serve static files
python -m http.server 8000 --directory web/static

# Or with Flask/FastAPI backend
python app.py
```

Open:
- Landing: http://localhost:8000/landing.html
- Dashboard: http://localhost:8000/index.html

## Features Checklist

### Landing Page
- [x] Hero with animated gradients
- [x] Agent grid visualization
- [x] Feature cards (6 core features)
- [x] Tech stack grid (8 technologies)
- [x] Stats section (3 metrics)
- [x] CTA section
- [x] Footer with links
- [x] Responsive design

### Dashboard
- [x] Chat interface with WebSocket
- [x] Pipeline trace canvas (pan/zoom/drag)
- [x] Agent cards with state management
- [x] Connection lines with animations
- [x] Knowledge base CRUD
- [x] Decision approval buttons
- [x] Empty states
- [x] Loading indicators
- [x] Minimap
- [x] Metrics panel
- [x] Stream logs
- [x] Session history
- [x] Message streaming effect

## Design Principles

1. **Professional First**: Enterprise B2B SaaS aesthetic
2. **Technical but Approachable**: Show complexity without overwhelming
3. **Real-time Feedback**: Visual states for every action
4. **Dark Mode Native**: Optimized for long sessions
5. **Performance**: No frameworks, pure vanilla JS
6. **Accessibility**: WCAG AA contrast ratios, keyboard nav
7. **Responsive**: Mobile-first approach

## Browser Support

- Chrome/Edge 90+
- Firefox 88+
- Safari 14+
- Mobile: iOS 14+, Android Chrome 90+

## License

MIT
