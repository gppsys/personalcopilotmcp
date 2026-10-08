"""Relationship map: how people, organizations, meetings, projects and tasks connect.

Sources, all optional (a failing source is reported, the rest still render):

- Organizations from config, and the executive ("me") at the center.
- People: Obsidian person notes plus real contacts from the e-mail
  interaction log (anyone written to, or frequent senders from the
  executive's own organization domains). Marketing senders are dropped.
- Meeting series: Obsidian meeting notes, linked to the people they mention
  (``[[links]]`` or full names in the text).
- Asana: projects and the tasks that are overdue or due soon, linked to the
  people they mention and to meetings/projects sharing a distinctive topic
  word (e.g. "PCI", "BBVA").
- Upcoming calendar events, linked to known attendees and their meeting series.
- Delegations and my commitments, linked to the person involved.
"""

from __future__ import annotations

import inspect
import math
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from executive_copilot.logging import get_logger
from executive_copilot.services.obsidian_notes import match_people, normalize, resolve_note_name

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from executive_copilot.services.workspace import WorkspaceService

logger = get_logger(__name__)

GENERIC_DOMAINS = {
    "gmail.com", "outlook.com", "hotmail.com", "live.com", "icloud.com", "yahoo.com",
    "me.com", "msn.com", "protonmail.com",
}
NOREPLY = re.compile(
    r"(?i)(no-?reply|notifications?|mailer|newsletter|news|marketing|promo|info@|"
    r"support@|soporte@|billing|invoic|factura|alerts?@|bounce|do-?not-?reply|hello@|team@|"
    r"updates?@|calendar-|admin@|contacto@|ventas@|sales@|pci@|security@|it@|rh@|hr@)"
)
# Display names of shared inboxes / ticket systems ("soporte via PCI USER").
ROLE_NAME = re.compile(r"(?i)(\bvia\b|invoicing|soporte|support|equipo|team|notifica)")
TOPIC_STOP = {
    "de", "la", "el", "los", "las", "y", "en", "con", "para", "por", "del", "al", "the", "and",
    "of", "to", "for", "on", "with", "paycode", "reunion", "sesion", "seguimiento", "plan",
    "semanal", "bi", "serie", "kickoff", "implementacion", "capacitacion", "tecnica", "diaria",
    "bitacora", "1a1", "prep", "ops", "stuff", "flow", "caso", "case", "nuevo", "nueva", "revisar",
    "review", "reunir", "meeting", "sync", "weekly", "tablero", "kanban", "epic", "tareas", "task",
    "proyecto", "project", "presentacion", "documentacion", "soporte", "interno", "equipo",
}


def mentioned(text: str, labels: list[str]) -> list[str]:
    """Labels whose first two name words appear together in the text."""
    haystack = f" {normalize(text)} "
    found = []
    for label in labels:
        words = normalize(re.sub(r"\(.*?\)", "", label)).split()
        if len(words) >= 2 and f" {words[0]} {words[1]} " in haystack:
            found.append(label)
    return found


def topic_tokens(text: str) -> set[str]:
    """Distinctive words used to link meetings, projects and tasks by topic."""
    return {t for t in normalize(text).split() if len(t) >= 3 and t not in TOPIC_STOP and not t.isdigit()}


class GraphBuilder:
    """Accumulates nodes and links with de-duplication."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.links: dict[tuple[str, str, str], dict[str, Any]] = {}

    def node(self, node_id: str, kind: str, label: str, **meta: Any) -> str:
        if node_id in self.nodes:
            existing = self.nodes[node_id]["meta"]
            for key, value in meta.items():
                if value not in (None, "", [], {}) and key not in existing:
                    existing[key] = value
        else:
            self.nodes[node_id] = {"id": node_id, "type": kind, "label": label, "weight": 1.0, "meta": {
                k: v for k, v in meta.items() if v not in (None, "", [], {})
            }}
        return node_id

    def link(self, a: str, b: str, kind: str, weight: float = 1.0) -> None:
        if a == b or a not in self.nodes or b not in self.nodes:
            return
        key = (a, b, kind) if a < b else (b, a, kind)
        if key in self.links:
            self.links[key]["weight"] += weight
        else:
            self.links[key] = {"source": a, "target": b, "kind": kind, "weight": weight}

    def degree(self) -> Counter[str]:
        deg: Counter[str] = Counter()
        for link in self.links.values():
            deg[link["source"]] += 1
            deg[link["target"]] += 1
        return deg


class KnowledgeGraphService:
    """Builds the relationship map served to the workspace."""

    def __init__(self, workspace: WorkspaceService) -> None:
        self.ws = workspace
        self.config = workspace.config
        self.g = GraphBuilder()
        self.errors: list[dict[str, str]] = []
        self.person_by_email: dict[str, str] = {}
        self.person_by_name: dict[str, str] = {}
        self.person_notes: list[str] = []
        self.me_names: set[str] = set()
        self.me_emails: set[str] = set()
        self.me_locals: set[str] = set()

    # --- people -----------------------------------------------------------

    def _org_for_domain(self, domain: str) -> str | None:
        for org in self.config.organizations:
            for d in org.domains:
                d = d.lower()
                if d in GENERIC_DOMAINS:
                    continue
                if domain == d or domain.endswith("." + d):
                    return f"org:{org.name}"
        return None

    def _self_identity(self) -> tuple[set[str], set[str]]:
        emails: set[str] = set()
        names: set[str] = set()
        for org in self.config.organizations:
            for pa in org.provider_accounts:
                emails.add(pa.account_id.lower())
                for ptype in ("email", "calendar"):
                    cred = self.ws.credential_manager.get_credential(ptype, pa.provider_name, pa.account_id)
                    label = (cred or {}).get("account_label")
                    if label and "@" not in label:
                        names.add(normalize(label))
        return emails, names

    def person(self, name: str | None = None, email: str | None = None, **meta: Any) -> str | None:
        """Find or create a person node, merging by e-mail, note and name."""
        email = (email or "").lower() or None
        if email and email in self.person_by_email:
            node_id = self.person_by_email[email]
            self.g.node(node_id, "person", self.g.nodes[node_id]["label"], **meta)
            return node_id

        if (name and normalize(name) in self.me_names) or (email and email in self.me_emails):
            return "me"
        if email and "." in email.split("@")[0] and email.split("@")[0] in self.me_locals:
            return "me"  # same "first.last" as one of my mailboxes, e.g. a former employer
        if name and ROLE_NAME.search(name):
            return None
        candidates = [c for c in (name, email) if c]
        notes = match_people(candidates, self.person_notes) if candidates else []
        label = notes[0] if notes else (name or (email.split("@")[0].replace(".", " ").title() if email else None))
        if not label or not re.search(r"[A-Za-zÀ-ÿ]{2}", label):
            return None
        label = re.sub(r"\s*\(.*?\)", "", label.split(" - ")[-1]).strip() if notes else label.strip()
        key = normalize(label)
        node_id = self.person_by_name.get(key) or f"person:{key}"
        self.g.node(node_id, "person", label, note=notes[0] if notes else None, **meta)
        self.person_by_name[key] = node_id
        if email:
            self.person_by_email[email] = node_id
            emails = self.g.nodes[node_id]["meta"].setdefault("emails", [])
            if email not in emails:
                emails.append(email)
            domain = email.split("@")[-1]
            org = self._org_for_domain(domain)
            if org:
                self.g.link(node_id, org, "works_at")
            elif domain not in GENERIC_DOMAINS:
                company = self.g.node(f"company:{domain}", "company", domain.split(".")[0].title(), domain=domain)
                self.g.link(node_id, company, "works_at")
        return node_id

    # --- sources ----------------------------------------------------------

    async def _contacts(self) -> None:
        from sqlalchemy import text

        own_emails, own_names = self._self_identity()
        work_domains = {
            d.lower() for org in self.config.organizations for d in org.domains if d.lower() not in GENERIC_DOMAINS
        }
        db = self.ws._db()
        try:
            async with db.session() as session:
                rows = (await session.execute(text(
                    "SELECT person_email, MAX(person_name), COUNT(*), "
                    "SUM(CASE WHEN direction = 'outbound' THEN 1 ELSE 0 END), MAX(occurred_at) "
                    "FROM interactions GROUP BY person_email "
                    "ORDER BY 4 DESC, 3 DESC LIMIT 400"
                ))).all()
        finally:
            await db.close()

        kept = 0
        for raw_email, name, total, sent, last in rows:
            email = str(raw_email or "").lower()
            domain = email.split("@")[-1]
            if not email or email in own_emails or NOREPLY.search(email):
                continue
            if name and (normalize(name) in own_names or ROLE_NAME.search(name)):
                continue
            is_work = any(domain == d or domain.endswith("." + d) for d in work_domains)
            if not (sent or (is_work and total >= 8)):
                continue
            node = self.person(name, email, interactions=int(total), sent=int(sent or 0), last_contact=str(last)[:10])
            if node:
                meta = self.g.nodes[node]["meta"]
                meta["interactions"] = max(int(total), meta.get("interactions", 0))
                kept += 1
            if kept >= 45:
                break

    def _meetings(self) -> None:
        vault = self.ws.vault("meetings")
        org = self._folder_org(self.config.obsidian.meetings_folder)
        person_labels = sorted({n["label"] for n in self.g.nodes.values() if n["type"] == "person"})
        for note in vault.list_notes():
            name = note["name"]
            text = vault.read(name) or ""
            node = self.g.node(
                f"meeting:{name}", "meeting", name,
                note=name, meetings=note["meetings"], latest=note["latest"],
            )
            if org:
                self.g.link(node, org, "belongs_to")
            linked = set(re.findall(r"\[\[([^\]|#]+)", text))
            for target in linked:
                if target in self.person_notes:
                    pid = self.person(target)
                    if pid:
                        self.g.link(node, pid, "attends", 2)
            for label in mentioned(text, person_labels):
                pid = self.person_by_name.get(normalize(label))
                if pid:
                    self.g.link(node, pid, "mentions")

    def _people_notes(self) -> None:
        try:
            people = self.ws.vault("people")
        except ValueError:
            return
        self.person_notes = people.note_names()
        org = self._folder_org(self.config.obsidian.people_folder or "")
        for name in self.person_notes:
            pid = self.person(name)
            if pid and org:
                self.g.link(pid, org, "works_at")

    def _folder_org(self, folder: str) -> str | None:
        head = normalize(folder.split("/", maxsplit=1)[0]) if folder else ""
        for org in self.config.organizations:
            if normalize(org.name) == head:
                return f"org:{org.name}"
        return None

    async def _asana(self) -> None:
        result = await self.ws.asana_tasks()
        self.errors.extend(result["errors"])
        tasks = result["tasks"]
        today = datetime.now(self.ws.tz).date()
        soon = (today + timedelta(days=14)).isoformat()
        org = next((f"org:{o.name}" for o in self.config.organizations if o.organization_type == "employer"), None)

        per_project: Counter[str] = Counter()
        overdue_project: Counter[str] = Counter()
        for t in tasks:
            if t["project"]:
                per_project[t["project"]] += 1
                if t["due_date"] and t["due_date"] < today.isoformat():
                    overdue_project[t["project"]] += 1
        for project, count in per_project.items():
            pid = self.g.node(f"project:{project}", "project", project, open_tasks=count, overdue=overdue_project[project])
            if org:
                self.g.link(pid, org, "belongs_to")

        person_labels = sorted({n["label"] for n in self.g.nodes.values() if n["type"] == "person"})
        focus = [t for t in tasks if t["due_date"] and t["due_date"] <= soon][:45]
        for t in focus:
            overdue = t["due_date"] < today.isoformat()
            tid = self.g.node(
                f"task:{t['id']}", "task", t["title"],
                due_date=t["due_date"], overdue=overdue, url=t["url"], project=t["project"],
            )
            if t["project"]:
                self.g.link(tid, f"project:{t['project']}", "in_project")
            for label in mentioned(f"{t['title']} {t['notes']}", person_labels):
                person_id = self.person_by_name.get(normalize(label))
                if person_id:
                    self.g.link(tid, person_id, "mentions")

    def _topics(self) -> None:
        """Link meetings with projects/tasks that share a distinctive topic word."""
        meetings = {nid: topic_tokens(n["label"]) for nid, n in self.g.nodes.items() if n["type"] == "meeting"}
        others = {
            nid: topic_tokens(n["label"])
            for nid, n in self.g.nodes.items()
            if n["type"] in ("project", "task")
        }
        for mid, mt in meetings.items():
            if not mt:
                continue
            for oid, ot in others.items():
                if mt & ot:
                    self.g.link(mid, oid, "topic")

    async def _events(self) -> None:
        result = await self.ws.events(days=7)
        self.errors.extend(result["errors"])
        meeting_names = [n["label"] for n in self.g.nodes.values() if n["type"] == "meeting"]
        for ev in result["events"][:30]:
            eid = self.g.node(
                f"event:{ev['id']}", "event", ev["title"],
                start=ev["start"], location=ev["location"], account=ev["account"],
            )
            known = 0
            for att in ev.get("attendees", []):
                pid = self.person_by_email.get((att.get("email") or "").lower())
                if pid:
                    self.g.link(eid, pid, "invited")
                    known += 1
            name, exists = resolve_note_name(ev["title"], meeting_names, self.config.obsidian.series_map)
            if exists:
                self.g.link(eid, f"meeting:{name}", "series")
                known += 1
            if not known:
                self.g.link(eid, "me", "agenda")

    async def _tracked(self) -> None:
        tracked = await self.ws.tracked_items()
        for d in tracked["delegations"][:25]:
            if not d["person"]:
                continue
            pid = self.person(d["person"] if "@" not in d["person"] else None, d["person"] if "@" in d["person"] else None)
            did = self.g.node(f"delegation:{d['id']}", "delegation", d["title"], due_date=d["due_date"], overdue=d["is_overdue"])
            if pid:
                self.g.link(did, pid, "delegated_to")
        for c in tracked["my_commitments"][:25]:
            cid = self.g.node(f"commitment:{c['id']}", "commitment", c["title"], due_date=c["due_date"], overdue=c["is_overdue"])
            if c["person"]:
                pid = self.person(c["person"] if "@" not in c["person"] else None, c["person"] if "@" in c["person"] else None)
                if pid:
                    self.g.link(cid, pid, "promised_to")
            self.g.link(cid, "me", "promised")

    # --- build --------------------------------------------------------------

    async def build(self) -> dict[str, Any]:
        account = self.config.calendar_defaults.account_id or "me"
        me_label = account.split("@")[0].split(".")[0].title()
        self.g.node("me", "me", me_label)
        self.me_emails, own_names = self._self_identity()
        self.me_locals = {e.split("@")[0] for e in self.me_emails if "." in e.split("@")[0]}
        self.me_names = own_names | {normalize(me_label)} | {normalize(x.replace(".", " ")) for x in self.me_locals}
        for org in self.config.organizations:
            oid = self.g.node(f"org:{org.name}", "org", org.name, org_type=org.organization_type, domains=org.domains)
            self.g.link("me", oid, "member", 3)

        steps: list[tuple[str, Callable[[], Awaitable[None] | None]]] = [
            ("obsidian_people", self._people_notes),
            ("email_contacts", self._contacts),
            ("obsidian_meetings", self._meetings),
            ("asana", self._asana),
            ("topics", self._topics),
            ("calendar", self._events),
            ("tracked", self._tracked),
        ]
        for label, step in steps:
            try:
                result = step()
                if inspect.isawaitable(result):
                    await result
            except Exception as e:
                logger.warning("Graph source failed", source=label, error=str(e))
                self.errors.append({"source": label, "error": str(e)})

        # Companies with a single person add noise: fold them away first.
        deg = self.g.degree()
        for nid in [n for n, v in self.g.nodes.items() if v["type"] == "company" and deg[n] <= 1]:
            self.g.nodes.pop(nid)
            self.g.links = {k: v for k, v in self.g.links.items() if nid not in (v["source"], v["target"])}
        # Anything left unconnected (single nodes or small islands such as a
        # delegation and its person) hangs off "me" so nothing floats alone.
        self._attach_islands()

        deg = self.g.degree()
        for nid, n in self.g.nodes.items():
            n["degree"] = deg[nid]
            interactions = n["meta"].get("interactions", 0)
            base = {"me": 6, "org": 4.5, "company": 2.5, "meeting": 2.2, "project": 2.4, "person": 1.6}.get(n["type"], 1.0)
            n["weight"] = round(base + math.log1p(deg[nid]) * 0.6 + math.log1p(interactions) * 0.25, 2)

        nodes = list(self.g.nodes.values())
        links = list(self.g.links.values())
        return {
            "nodes": nodes,
            "links": links,
            "stats": dict(Counter(n["type"] for n in nodes)),
            "insights": self._insights(nodes),
            "errors": self.errors,
            "generated_at": datetime.now(self.ws.tz).isoformat(timespec="seconds"),
        }

    def _attach_islands(self) -> None:
        adj: dict[str, set[str]] = {nid: set() for nid in self.g.nodes}
        for link in self.g.links.values():
            adj[link["source"]].add(link["target"])
            adj[link["target"]].add(link["source"])
        seen: set[str] = set()
        for start in self.g.nodes:
            if start in seen:
                continue
            component, stack = [], [start]
            seen.add(start)
            while stack:
                cur = stack.pop()
                component.append(cur)
                for nxt in adj[cur] - seen:
                    seen.add(nxt)
                    stack.append(nxt)
            if "me" not in component:
                anchor = max(component, key=lambda nid: (self.g.nodes[nid]["type"] == "person", len(adj[nid])))
                self.g.link(anchor, "me", "contact")

    def _insights(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        people = [n for n in nodes if n["type"] == "person"]
        if people:
            hub = max(people, key=lambda n: n["degree"])
            out.append({"title": "Persona más conectada", "value": hub["label"], "detail": f"{hub['degree']} conexiones", "node": hub["id"]})
            talk = max(people, key=lambda n: n["meta"].get("interactions", 0))
            if talk["meta"].get("interactions"):
                out.append({"title": "Más intercambio por correo", "value": talk["label"], "detail": f"{talk['meta']['interactions']} correos", "node": talk["id"]})
        projects = [n for n in nodes if n["type"] == "project" and n["meta"].get("overdue")]
        if projects:
            hot = max(projects, key=lambda n: n["meta"]["overdue"])
            out.append({"title": "Proyecto con más atraso", "value": hot["label"], "detail": f"{hot['meta']['overdue']} tareas vencidas", "node": hot["id"]})
        meetings = [n for n in nodes if n["type"] == "meeting"]
        if meetings:
            busy = max(meetings, key=lambda n: n["degree"])
            out.append({"title": "Reunión que más conecta", "value": busy["label"], "detail": f"{busy['degree']} conexiones", "node": busy["id"]})
        return out
