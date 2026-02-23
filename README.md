# Nubi

<div align="center">

**Simple. Powerful. LLM-First.**

A modern Business Intelligence tool that speaks your language.

[Get Started](#getting-started) · [Documentation](#documentation) · [Features](#features)

</div>

---

## What is Nubi?

Nubi is a next-generation BI platform that combines the simplicity of natural language with the power of SQL and Python. Built for data analysts, engineers, and business users who want insights without the complexity.

### 🎯 Core Philosophy

- **LLM-First**: Ask questions in plain English, get answers in data
- **Python & SQL Native**: Full control when you need it, magic when you don't
- **Visual & Interactive**: Beautiful dashboards that update in real-time
- **Developer-Friendly**: API-first design, extensible architecture

---

## ✨ Features

### 🤖 **Natural Language Queries**
Ask questions like "Show me revenue by region last quarter" and watch as Nubi translates your intent into optimized SQL.

### 📊 **Interactive Dashboards**
Drag-and-drop widgets, real-time charts, and KPI cards that actually look good. Built with Alpine.js and Chart.js for blazing-fast performance.

### 🔗 **Data Stitching Engine**
Chain SQL queries and Python transformations into powerful data flows. Template variables, pandas DataFrames, and BigQuery support out of the box.

### 💬 **Conversational Analytics**
Chat with your data. Ask follow-up questions. Iterate on queries. All powered by Google's Gemini AI.

### 🔌 **Multiple Data Sources**
- BigQuery (native support)
- PostgreSQL
- More connectors coming soon

---

## 🚀 Getting Started

### Prerequisites

- Node.js 18+
- Python 3.10+
- PostgreSQL 15+
- BigQuery or PostgreSQL database for your data

### Database Setup

```bash
# Create the database
createdb nubi

# Run migrations
python database/migrate.py

# Check migration status
python database/migrate.py --status
```

### Frontend Setup

```bash
# Install dependencies
npm install

# Create environment file
cp .env.example .env
# Edit .env — set VITE_BACKEND_URL and VITE_GOOGLE_CLIENT_ID

# Start development server
npm run dev
```

The frontend will be available at `http://localhost:5173`

### Backend Setup

```bash
# Navigate to backend
cd backend

# Install Python dependencies
pip install -r requirements.txt

# Configure environment (see .env.example for all variables)
# Key variables:
# DATABASE_URL=postgresql://localhost:5432/nubi
# JWT_SECRET=change-me-in-production
# GEMINI_API_KEY=your_gemini_api_key

# Start the backend
python main.py
```

The API will be available at `http://localhost:8000`

---

## 📖 Documentation

### Architecture

```
┌─────────────────┐
│  React Frontend │  ← User Interface (Vite + React + TailwindCSS)
└────────┬────────┘
         │
┌────────▼────────┐
│  FastAPI Backend│  ← Auth (JWT), API, Query Engine
└───┬─────────┬───┘
    │         │
┌───▼───┐ ┌──▼─────┐
│Postgres│ │BigQuery│  ← Your Data
└───────┘ └────────┘
```

### Key Concepts

**Boards**: Visual dashboards containing widgets, charts, and KPIs

**Queries**: SQL or Python code blocks that fetch and transform data

**Datastores**: Database connections (BigQuery, PostgreSQL, etc.)

**Stitch Chains**: Multi-step data flows with templating and Python logic

**LLM Assistant**: AI-powered query builder and dashboard editor

### API Examples

#### Execute a Query Chain

```javascript
const response = await fetch('http://localhost:8000/stitch', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    chain_id: 'your-chain-uuid',
    args: {
      date_from: '2024-01-01',
      region: 'US'
    }
  })
});

const { status, table, count } = await response.json();
console.log(`Fetched ${count} rows:`, table);
```

#### Chat with Your Data

```javascript
const response = await fetch('http://localhost:8000/chat/stream', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    board_id: 'your-board-uuid',
    query_id: 'your-query-uuid',
    messages: [
      { role: 'user', content: 'Show me sales trends by month' }
    ]
  })
});
```

---

## 🛠️ Tech Stack

### Frontend
- **React 19** - UI framework
- **Vite** - Build tool
- **TailwindCSS** - Styling
- **Monaco Editor** - Code editing
- **Chart.js** - Visualizations
- **React Flow** - Node-based flows

### Backend
- **FastAPI** - API framework
- **Python 3.10+** - Runtime
- **asyncpg** - PostgreSQL driver
- **Pandas** - Data manipulation
- **BigQuery** - Data warehouse
- **Jinja2** - SQL templating
- **Google Gemini** - LLM integration
- **JWT / bcrypt** - Authentication
- **Pluggable storage** - Google Cloud Storage or local filesystem

---

## 🎨 Screenshots

*Coming soon - beautiful dashboards in action*

---

## 🤝 Contributing

We welcome contributions! Whether it's:

- 🐛 Bug reports
- 💡 Feature requests  
- 📝 Documentation improvements
- 🔧 Code contributions

Please open an issue or submit a pull request.

---

## 📄 License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

---

## 🌟 Why Nubi?

Traditional BI tools are either:
- **Too simple**: Drag-and-drop interfaces that hit a wall
- **Too complex**: Require weeks of training and consultants

**Nubi is different.**

It gives you the simplicity of natural language queries with the depth of SQL and Python. Start with "Show me revenue", graduate to custom transformations, all in the same tool.

---

## 💬 Community & Support

- **Issues**: [GitHub Issues](https://github.com/yourusername/nubi/issues)
- **Discussions**: [GitHub Discussions](https://github.com/yourusername/nubi/discussions)

---

<div align="center">

**Built with ❤️ for data people**

[⭐ Star us on GitHub](https://github.com/yourusername/nubi)

</div>
