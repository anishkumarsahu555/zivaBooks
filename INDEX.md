# zivaBooks — Master Documentation Directory & Index

Welcome to the **zivaBooks** documentation hub! Below is the complete directory of all project architecture specifications, engineering rules, design tokens, operational commands, QA validation suites, and deployment guides.

---

## 📚 Complete Documentation Index

| Guide | Description | Target Audience |
|---|---|---|
| 🚀 **[README.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/README.md)** | Quick start, core capabilities, environment configuration, and health checks. | All Developers & Evaluators |
| 🧠 **[MEMORY.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/MEMORY.md)** | Persistent system memory, domain overview, architectural decisions, and gotchas. | Human Developers & AI Agents |
| 📋 **[TASKS.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/TASKS.md)** | Project roadmap, completed milestones, active backlog, and technical debt. | Product Managers & Developers |
| 🏗️ **[ARCHITECTURE.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/ARCHITECTURE.md)** | High-level system architecture, middleware pipeline, multi-tenancy, and FEFO ledger. | Tech Leads & Architects |
| 🎨 **[DESIGN.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/DESIGN.md)** | UI/UX design system, color palette, typography, POS thermal/A4 print, and components. | Frontend & Product Designers |
| 💻 **[COMMANDS.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/COMMANDS.md)** | Exhaustive CLI reference: uv, manage.py, seeds, tests, Celery, Redis, and backups. | Developers & DevOps Engineers |
| 📜 **[RULES.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/RULES.md)** | Mandatory engineering principles, tenant isolation invariants, and security rules. | All Contributors & AI Agents |
| 🧪 **[VALIDATION.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/VALIDATION.md)** | Automated test execution matrix, QA protocols, and pre-deployment release checks. | QA Engineers & Tech Leads |
| 🔌 **[API.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/API.md)** | Complete JSON API specification, endpoints catalog, parameters, and contracts. | Frontend / Backend / Integrators |
| 🗄️ **[DATABASE.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/DATABASE.md)** | Database schema, ER diagrams, data model inheritance hierarchy, and indexes. | Database Engineers & Backend |
| 💡 **[DEVELOPMENT.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/DEVELOPMENT.md)** | Step-by-step developer guide for adding features, APIs, models, and services. | Feature Developers |
| 🧹 **[MAINTAINABILITY.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/MAINTAINABILITY.md)** | Refactoring boundaries, service layer separation guidelines, and architectural hotspots. | Code Reviewers & Refactorers |
| 🔬 **[TESTCASES.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/TESTCASES.md)** | End-to-end manual QA test scenarios covering licensing, OTP, sales, and ledgers. | QA Engineers & Testers |
| 🏷️ **[CUSTOM_TENANT_PLAN_GUIDE.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/CUSTOM_TENANT_PLAN_GUIDE.md)** | Admin setup guide for 25+ feature toggles, seat limits, and custom enterprise plans. | System Admins & Account Reps |
| 🛠️ **[setup.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/setup.md)** | Local environment setup (SQLite vs PostgreSQL, Redis, email configuration). | Developers & QA |
| 🖥️ **[server-setup.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/server-setup.md)** | PostgreSQL installation, remote pgAdmin configuration, firewall, and database creation. | DevOps & Sysadmins |
| 🚢 **[deploy/DEPLOYMENT.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/deploy/DEPLOYMENT.md)** | Production Docker Compose + Reverse Proxy deployment & backup layout (`/opt/`). | DevOps, SRE & Sysadmins |
| 🚢 **[zivabooks_deployment.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/zivabooks_deployment.md)** | Bare-metal production deployment guidelines (systemd + Nginx). | DevOps & SRE |
| 🤖 **[SKILLS.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/SKILLS.md)** | Antigravity AI Agent context and developer guide. | AI Agents & Copilots |

---

## ⚡ Quick Navigation Matrix

### I want to...
- **Run the app or background worker**: Use [`./start.sh`](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/start.sh) and check [COMMANDS.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/COMMANDS.md)
- **Understand persistent system memory & architecture gotchas**: Read [MEMORY.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/MEMORY.md)
- **Check roadmap, active tasks & technical debt**: Read [TASKS.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/TASKS.md)
- **Review UI design tokens, components & POS thermal layouts**: Read [DESIGN.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/DESIGN.md)
- **Follow coding rules and multi-tenant invariants**: Read [RULES.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/RULES.md)
- **Run test suites or perform QA verification**: Read [VALIDATION.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/VALIDATION.md) and [TESTCASES.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/TESTCASES.md)
- **Add a new feature or API**: Read [DEVELOPMENT.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/DEVELOPMENT.md) and [API.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/API.md)
- **Configure custom enterprise plans with 25+ toggles**: Read [CUSTOM_TENANT_PLAN_GUIDE.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/CUSTOM_TENANT_PLAN_GUIDE.md)
- **Understand database models & ER diagrams**: Read [DATABASE.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/DATABASE.md)
- **Refactor code without breaking anything**: Read [MAINTAINABILITY.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/MAINTAINABILITY.md)
- **Deploy with Docker Compose & Edge Reverse Proxy (`/opt/` layout)**: Read [deploy/DEPLOYMENT.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/deploy/DEPLOYMENT.md)
- **Deploy to production with bare-metal systemd & Nginx**: Read [zivabooks_deployment.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/zivabooks_deployment.md) & [server-setup.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/server-setup.md)
