# zivaBooks Master Documentation Directory

Welcome to the **zivaBooks** documentation hub! Below is a complete index of all project guides, architectural specifications, setup instructions, development references, and QA test suites.

---

## 📚 Documentation Index

| Guide | Description | Target Audience |
|---|---|---|
| 🚀 **[README.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/README.md)** | Quick start, features overview, environment configuration, and basic health checks. | All Developers / Evaluators |
| 🛠️ **[setup.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/setup.md)** | Detailed local environment setup (SQLite vs PostgreSQL, Redis, email configuration). | Developers / QA |
| 🏗️ **[ARCHITECTURE.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/ARCHITECTURE.md)** | Technical specification covering multi-tenancy, middleware pipelines, domain subsystems, and FEFO inventory logic. | Tech Leads / Architects |
| 💡 **[DEVELOPMENT.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/DEVELOPMENT.md)** | Step-by-step guide for adding new features, APIs, models, migrations, service modules, and tests. | Feature Developers |
| 🔌 **[API.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/API.md)** | Complete JSON API specification, endpoints catalog, authentication requirements, and error contracts. | Frontend / Backend / Integrators |
| 🗄️ **[DATABASE.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/DATABASE.md)** | ER diagrams, data model inheritance hierarchy, table relationships, and indexing rules. | Database Engineers / Backend |
| 🧹 **[MAINTAINABILITY.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/MAINTAINABILITY.md)** | Refactoring boundaries, service layer separation guidelines, and architectural hotspots. | Refactoring / Code Reviewers |
| 🧪 **[TESTCASES.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/TESTCASES.md)** | End-to-end QA manual test matrix covering licensing, OTP, sales, inventory, and permissions. | QA Engineers / Testers |
| 🖥️ **[server-setup.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/server-setup.md)** | PostgreSQL installation, remote pgAdmin configuration, firewall settings, and database creation. | DevOps / Sysadmins |
| 🚢 **[zivabooks_deployment.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/zivabooks_deployment.md)** | Production deployment guidelines, Nginx config, systemd service setup, and SSL hardening. | DevOps / SRE |
| 🤖 **[SKILLS.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/SKILLS.md)** | Antigravity AI Agent context and developer guide. | AI Agents / Copilots |

---

## ⚡ Quick Navigation Matrix

### I want to...
- **Set up the project locally**: Read [setup.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/setup.md)
- **Add a new feature or API**: Read [DEVELOPMENT.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/DEVELOPMENT.md) and [API.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/API.md)
- **Understand how multi-tenancy or stock allocation works**: Read [ARCHITECTURE.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/ARCHITECTURE.md)
- **Understand database models & ER diagrams**: Read [DATABASE.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/DATABASE.md)
- **Refactor code without breaking anything**: Read [MAINTAINABILITY.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/MAINTAINABILITY.md)
- **Deploy to production**: Read [zivabooks_deployment.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/zivabooks_deployment.md) & [server-setup.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/server-setup.md)
- **Run test suites**: Run `uv run python manage.py test` and check [TESTCASES.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/TESTCASES.md)
