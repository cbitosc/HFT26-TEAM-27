# 🚀 CBIT Hacktoberfest '26 — Project Starter Repository

> **Official Public Starter Repository for CBIT Hacktoberfest '26**  
> *Chaitanya Bharathi Institute of Technology (CBIT), Hyderabad*

---

## 📋 Team Information

| Attribute | Details |
| :--- | :--- |
| **Team Name** | **NeuralNarcissists** |
| **Team Number** | `TEAM-27` (`HFT26-TEAM-27`) |
| **Registration ID** | `HFEST26-BUZGMYUC` |
| **Problem Track** | **Track Selection in Progress** |
| **Domain** | `General Track` |
| **Team Leader** | DAKSH CHOKHANI (daksh.anindian26@gmail.com) |
| **Institution** | CBIT HYDERABAD |

### 👥 Team Members

| # | Name | Email | Institution |
| :--- | :--- | :--- | :--- |
| 1 | ROHIT REDDY GOPU | rohit.gopu00@gmail.com | CBIT HYDERABAD |
| 2 | JESHWANTH NEELAM | neelam.jeshwanth08@gmail.com | CBIT HYDERABAD |
| 3 | SIMRAN LAHOTI | simranlahoti11@gmail.com | VARDHAMAN COLLEGE OF ENGINEERING HYDERABAD |
| 4 | VINAY MABBU | vinaymabbu79@gmail.com | CBIT HYDERABAD |

---

## 🎯 Problem Statement

### **Track Selection in Progress**
*Domain:* **General Track**

Details regarding the chosen problem statement track and technical requirements.

---

## 🚦 Our Solution: Evidence-Grounded Incident Reconstruction

Upload what an investigator actually has (traffic-camera or dashcam video, witness statements, sensor
and telemetry CSVs) and get one cited account of what happened:

- **One timeline across all sources.** Every event links back to the exact frame, line or row it came from.
- **Contradictions found automatically:** a witness's speed vs telemetry, a claimed green light vs the
  signal log, clock times, "sped up" vs braking, and gaps in the evidence. Rule-verified first, LLM-assisted
  second, and never presented as more certain than they are.
- **Verified video findings.** For fixed traffic cameras, a "stopped vehicle" claim from the vision model is
  only kept if the image patch is unchanged a few seconds later.
- **Cross-source fusion.** A collision is inferred when two vehicles' separate telemetry logs both turn
  sharply at the same moment and place.
- **Chain of custody.** SHA-256 at upload, duplicate and bad-file rejection, an evidence manifest per run,
  run history, and an append-only audit log.
- **An investigator report** (Markdown/JSON) with citations, evidence gaps and an AI-drafted summary
  labelled as such. Sample: [`docs/sample_report/report.md`](docs/sample_report/report.md).

### Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r src/backend/requirements.txt
echo "FEATHERLESS_API_KEY=<your key>" > .env          # optional: enables video and witness analysis
.venv/bin/python scripts/analyze.py data/witness/*.txt data/sensor/*.csv   # CLI -> reports/<run>/report.md
cd src/backend && ../../.venv/bin/uvicorn app.main:app --port 8000 --env-file ../../.env   # API: /docs
```

Architecture: [`docs/contracts/INTEGRATION_CONTRACT.md`](docs/contracts/INTEGRATION_CONTRACT.md) ·
change log: [`docs/BUILD_LOG.md`](docs/BUILD_LOG.md).

---

## 🛠️ Contribution & Submission Workflow

All teams must follow the official fork-and-pull-request workflow:

1. **Fork this Repository**:
   - One team member should click the **Fork** button at the top right to fork this upstream repository to your personal/team GitHub account.
2. **Add Collaborators**:
   - In your forked repository, navigate to **Settings > Collaborators** and invite all your registered team members.
3. **Clone & Develop Locally**:
   ```bash
   git clone https://github.com/<your-username>/HFT26-TEAM-27.git
   cd HFT26-TEAM-27
   git checkout -b feature/prototype
   ```
4. **Commit & Push Regularly**:
   - Make atomic, descriptive commits throughout the hackathon.
   ```bash
   git add .
   git commit -m "feat: implement core solution architecture"
   git push origin feature/prototype
   ```
5. **Open Pull Request (PR) for Final Submission**:
   - When ready for evaluation, open a **Pull Request** from your fork's `main` branch back to this upstream repository's `main` branch.
   - **PR Title Format**: `[SUBMISSION] TEAM-27 - NeuralNarcissists - Track Selection in Progress`
   - **PR Description Checklist**:
     - 📌 Solution Overview & Approach
     - 🏗️ Architecture & Tech Stack Details
     - 🚀 Local Setup & Installation Instructions
     - 🎥 Demo Video Link / Live Deployment URL
     - 📸 Key Screenshots & Visuals

---

## 📁 Recommended Project Structure

```
.
├── src/                  # Source code (Frontend / Backend / ML)
├── docs/                 # Documentation, architecture diagrams, slides
├── tests/                # Test suites & verification scripts
├── README.md             # Your project documentation & presentation
└── LICENSE               # Open-source license (MIT recommended)
```

---

## ⚠️ Important Rules & Code of Conduct
* **Upstream Protection**: Do not push directly to this repository's main branch. All evaluation will be conducted on your submitted Pull Request.
* **Original Work**: All code submitted must be developed during the hackathon timeline. Open-source libraries and frameworks are welcomed with proper attribution.
* **Code Freeze**: Final Pull Requests must be submitted before the designated hackathon submission deadline.
