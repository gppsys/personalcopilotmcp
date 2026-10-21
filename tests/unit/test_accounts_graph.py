"""Tests for UI settings, accounts (no secret leaks) and the relationship map."""

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from executive_copilot.admin.workspace_api import create_workspace_router
from executive_copilot.config import AppConfig
from executive_copilot.config.loader import ConfigLoader, load_ui_settings, save_ui_settings
from executive_copilot.credentials import CredentialManager
from executive_copilot.services.accounts import AccountsService
from executive_copilot.services.knowledge_graph import (
    GraphBuilder,
    KnowledgeGraphService,
    mentioned,
    topic_tokens,
)


def test_ui_settings_override_and_removal(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("calendar_defaults:\n  account_id: a@x.com\n")
    (tmp_path / "config.local.yaml").write_text("calendar_defaults:\n  account_id: b@x.com\n")
    assert ConfigLoader(tmp_path).load().calendar_defaults.account_id == "b@x.com"

    save_ui_settings(tmp_path, {"calendar_defaults": {"account_id": "ui@x.com"}, "email_calendar": {"target_account_id": "t@x.com"}})
    cfg = ConfigLoader(tmp_path).load()
    assert cfg.calendar_defaults.account_id == "ui@x.com"
    assert cfg.email_calendar.target_account_id == "t@x.com"

    save_ui_settings(tmp_path, {"email_calendar": {"target_account_id": None}})
    assert "target_account_id" not in load_ui_settings(tmp_path)["email_calendar"]
    assert ConfigLoader(tmp_path).load().email_calendar.target_account_id is None


def make_config(**kw: Any) -> AppConfig:
    return AppConfig(
        organizations=[{
            "name": "Work", "organization_type": "employer", "domains": ["work.com"],
            "provider_accounts": [
                {"provider_type": "email", "provider_name": "gmail", "account_id": "me@work.com"},
                {"provider_type": "email", "provider_name": "outlook", "account_id": "me@outlook.com"},
            ],
        }],
        **kw,
    )


def test_accounts_listing_never_returns_secrets(tmp_path: Path) -> None:
    cm = CredentialManager(tmp_path)
    cm.save_credential("calendar", "google", "me@work.com", client_id="CID", client_secret="SECRET",
                       access_token="ACCESS", refresh_token="REFRESH")
    cm.save_credential("ai", "openai", "default", api_key="sk-SECRET")
    listing = AccountsService(make_config(), cm).list_accounts()
    blob = str(listing)
    for secret in ("CID", "SECRET", "ACCESS", "REFRESH", "sk-SECRET"):
        assert secret not in blob
    google = next(a for a in listing["oauth"] if a["account_id"] == "me@work.com")
    assert google["status"] == "connected" and google["services"] == ["Calendar"]
    outlook = next(a for a in listing["oauth"] if a["account_id"] == "me@outlook.com")
    assert outlook["status"] == "no_client"
    openai = next(t for t in listing["tokens"] if t["kind"] == "openai")
    assert openai["status"] == "connected"


def test_oauth_client_reused_and_disconnect_keeps_it(tmp_path: Path) -> None:
    cm = CredentialManager(tmp_path)
    cm.save_credential("email", "gmail", "me@work.com", client_id="CID", client_secret="S", access_token="A", refresh_token="R")
    service = AccountsService(make_config(), cm)
    status = service.set_oauth_client("google", "New@Gmail.com", None, None)
    assert status["account_id"] == "new@gmail.com" and status["status"] == "not_connected"
    assert cm.get_oauth_client("google", "new@gmail.com") == {"client_id": "CID", "client_secret": "S", "tenant_id": "common"}

    service.disconnect("google", "me@work.com")
    cred = cm.get_credential("email", "gmail", "me@work.com")
    assert cred is not None and "access_token" not in cred and cred["client_id"] == "CID"

    with pytest.raises(ValueError):
        AccountsService(make_config(), CredentialManager(tmp_path / "empty")).set_oauth_client("microsoft", "x@y.com", None, None)


def test_settings_endpoint_saves_email_target(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("calendar_defaults:\n  account_id: me@work.com\n")
    app = FastAPI()
    app.include_router(create_workspace_router(config_dir, CredentialManager(tmp_path / "creds")))
    client = TestClient(app)

    body = {"email_target": {"provider": "outlook", "account_id": "me@outlook.com", "calendar_id": "fam"},
            "source_accounts": ["me@outlook.com"]}
    out = client.put("/api/ws/settings/calendar", json=body).json()
    assert out["email_target"] == {"provider": "outlook", "account_id": "me@outlook.com", "calendar_id": "fam"}
    assert out["default_calendar"]["account_id"] == "me@work.com"
    assert out["email_target_is_default"] is False

    out = client.put("/api/ws/settings/calendar", json={"use_default_for_email": True}).json()
    assert out["email_target_is_default"] is True


def test_mentioned_requires_full_name_phrase() -> None:
    labels = ["Carlos Gomez", "Josue Hernandez Martinez", "Rafael Ayala (Rafa)"]
    text = "Carlos Diez y Alexia Gomez revisaron; Josué Hernández entrega. Rafael Ayala aprueba."
    assert mentioned(text, labels) == ["Josue Hernandez Martinez", "Rafael Ayala (Rafa)"]


def test_topic_tokens_skip_generic_words() -> None:
    assert topic_tokens("Seguimiento PCI DSS") == {"pci", "dss"}
    assert topic_tokens("Reunión diaria de Paycode") == set()


def test_islands_are_attached_to_me() -> None:
    class Stub:
        config = make_config()
        credential_manager = None
        tz = None

    svc = KnowledgeGraphService(Stub())  # type: ignore[arg-type]
    g: GraphBuilder = svc.g
    g.node("me", "me", "Me")
    g.node("person:a", "person", "A")
    g.node("delegation:1", "delegation", "Do it")
    g.link("delegation:1", "person:a", "delegated_to")
    svc._attach_islands()
    assert any({"person:a", "me"} == {link["source"], link["target"]} for link in g.links.values())


def test_remove_account_also_unlinks_it_from_organizations(tmp_path: Path) -> None:
    import yaml

    (tmp_path / "organizations.yaml").write_text(yaml.safe_dump({"organizations": [{
        "name": "Work", "organization_type": "employer", "domains": ["work.com"],
        "provider_accounts": [
            {"provider_type": "email", "provider_name": "gmail", "account_id": "me@work.com"},
            {"provider_type": "calendar", "provider_name": "outlook", "account_id": "me@work.com"},
        ],
    }]}))
    config = ConfigLoader(tmp_path).load()
    cm = CredentialManager(tmp_path / ".credentials")
    cm.save_credential("calendar", "outlook", "me@work.com", client_id="X")
    out = AccountsService(config, cm).remove("microsoft", "me@work.com")
    assert out["removed"] == 1 and out["removed_from_organizations"] == ["Work"]
    accounts = ConfigLoader(tmp_path).load().organizations[0].provider_accounts
    assert [(pa.provider_name, pa.account_id) for pa in accounts] == [("gmail", "me@work.com")]
    listing = AccountsService(ConfigLoader(tmp_path).load(), cm).list_accounts()
    assert not [a for a in listing["oauth"] if a["family"] == "microsoft"]


@pytest.mark.asyncio
async def test_model_list_falls_back_to_configured_models() -> None:
    from executive_copilot.services import chat

    class Broken:
        async def list(self) -> Any:
            raise RuntimeError("no network")

    class Client:
        models = Broken()

    class AI:
        class container:
            config = AppConfig()

        def _get_openai_client(self) -> Any:
            return Client()

    chat._models_cache.clear()
    models = await chat.available_models(AI())
    ids = [m["id"] for m in models]
    assert AppConfig().ai.default_model in ids
    assert sum(m["default"] for m in models) == 1
    chat._models_cache.clear()


def test_vault_index_search_read_and_person(tmp_path: Path) -> None:
    from executive_copilot.services.vault_index import VaultIndex, split_sections

    vault = tmp_path / "vault"
    (vault / "Work" / "Personas").mkdir(parents=True)
    (vault / "Work" / "Reuniones").mkdir(parents=True)
    (vault / "Other").mkdir()
    (vault / "Work" / "Personas" / "Josue Hernandez Martinez.md").write_text("# Josue\n\n- **Rol:** backend\n")
    (vault / "Work" / "Reuniones" / "1a1.md").write_text(
        "# 1a1\n\n## 2026-08-31\n- Error de tokenización con [[Josue Hernandez Martinez]].\n\n"
        "## 2026-08-24\n- Plugin WooCommerce listo.\n"
    )
    (vault / "Other" / "secret.md").write_text("# Secret\n\ntokenización privada\n")
    index = VaultIndex(vault, ["Work"], tmp_path / "idx.db", people_folder="Work/Personas")

    assert index.refresh(force=True)["indexed"] == 2
    hits = index.search("tokenizacion")
    assert [h["note"] for h in hits] == ["Work/Reuniones/1a1.md"]
    assert hits[0]["date"] == "2026-08-31" and "**tokenización**" in hits[0]["snippet"]
    assert index.search("woocommerce plugin")[0]["section"] == "2026-08-24"

    one = index.read("1a1", section="2026-08-24")
    assert "WooCommerce" in one["content"] and "tokenización" not in one["content"]
    assert "error" in index.read("secret")  # outside indexed folders

    person = index.person_context("Josue")
    assert person["person_note"]["note"] == "Work/Personas/Josue Hernandez Martinez.md"
    assert person["linked_from"] == [{"note": "Work/Reuniones/1a1.md", "title": "1a1"}]

    (vault / "Work" / "Reuniones" / "1a1.md").unlink()
    assert index.refresh(force=True)["removed"] == 1
    assert index.search("tokenizacion") == []

    long = "# T\n\n## A\n" + ("palabra " * 600 + "\n\n") * 3
    assert all(len(c["body"]) <= 1800 for c in split_sections(long, "T"))


DECISION_NOTE = """# Decisiones

### Sesión 2 — Decisión
- [ ] Revisar gates

## 11. Registro de decisión

### Decisión sobre Hyperswitch

- [ ] A. Core completo
- [ ] B. Componentes selectivos
- [ ] C. Modelo de referencia

### Siguiente paso autorizado

### Responsables y fecha de revisión
"""


def test_apply_edit_check_append_replace() -> None:
    from executive_copilot.services.vault_index import VaultEditError, apply_edit

    out = apply_edit(DECISION_NOTE, "check", section="Decisión sobre Hyperswitch", find="Componentes selectivos")
    assert "- [x] B. Componentes selectivos" in out and "- [ ] A. Core completo" in out
    assert "- [ ] Revisar gates" in out  # other sections untouched

    out = apply_edit(out, "append", section="Siguiente paso autorizado", new_text="- PoC de conectores (2026-10-08)")
    lines = out.split("\n")
    i = lines.index("### Siguiente paso autorizado")
    assert lines[i + 1] == "" or lines[i + 1] == "- PoC de conectores (2026-10-08)"
    assert out.index("PoC de conectores") < out.index("### Responsables")

    out = apply_edit(out, "replace", section="Decisión sobre Hyperswitch", find="C. Modelo de referencia", new_text="C. Modelo de referencia (descartado)")
    assert "(descartado)" in out

    with pytest.raises(VaultEditError, match="ambiguous"):
        apply_edit(DECISION_NOTE, "check", section="Decisión", find="Core")
    with pytest.raises(VaultEditError, match="found 3"):
        apply_edit(DECISION_NOTE, "check", section="Decisión sobre Hyperswitch", find=".")


def test_vault_edit_writes_backup_and_reindexes(tmp_path: Path) -> None:
    from executive_copilot.services.vault_index import VaultIndex

    vault = tmp_path / "vault"
    (vault / "Work").mkdir(parents=True)
    note = vault / "Work" / "decisiones.md"
    note.write_text(DECISION_NOTE, encoding="utf-8")
    index = VaultIndex(vault, ["Work"], tmp_path / "idx.db")

    preview = index.edit("decisiones", "check", section="Decisión sobre Hyperswitch", find="Core completo", dry_run=True)
    assert preview["dry_run"] and "+- [x] A. Core completo" in preview["diff"]
    assert note.read_text(encoding="utf-8") == DECISION_NOTE

    done = index.edit("decisiones", "check", section="Decisión sobre Hyperswitch", find="Core completo", backup_dir=tmp_path / "bk")
    assert "- [x] A. Core completo" in note.read_text(encoding="utf-8")
    assert Path(done["backup"]).read_text(encoding="utf-8") == DECISION_NOTE
    assert index.edit("decisiones", "check", section="Decisión sobre Hyperswitch", find="Core completo")["changed"] is False


def test_redaction_hides_credentials() -> None:
    from executive_copilot.services.vault_index import redact

    text = (
        "Account SID: AC" + "a" * 32 + "\n"
        "- **Auth token:** abcd1234efgh5678\n"
        "aws AKIA" + "B" * 16 + " and key sk-proj-" + "x" * 30 + "\n"
        "Decidimos usar tokens de un solo uso."
    )
    out = redact(text)
    assert "AC" + "a" * 32 not in out and "abcd1234efgh5678" not in out
    assert "AKIA" not in out and "sk-proj" not in out
    assert "Decidimos usar tokens de un solo uso." in out


def test_redaction_ignores_placeholders_empty_values_and_prose() -> None:
    from executive_copilot.services.vault_index import redact

    text = (
        '-H "x-api-key: $SAFE_API_KEY" \\\n'
        "Authorization token: <YOUR_TOKEN_HERE>\n"
        "API_KEY=\nSECRET_KEY=\n"
        "1. **Consentimiento como atributo del token:** rechazada — no escala.\n"
    )
    assert redact(text) == text
    assert "s3cr3tvalue" not in redact("- **Password:** s3cr3tvalue\n")
    assert "Abcdefgh12" not in redact("api_key = 'Abcdefgh12'")


@pytest.mark.asyncio
async def test_hybrid_search_ranks_by_meaning_and_falls_back(tmp_path: Path) -> None:
    from executive_copilot.services.vault_index import VaultIndex

    vault = tmp_path / "vault"
    (vault / "W").mkdir(parents=True)
    (vault / "W" / "reverso.md").write_text("# ADR-010\n\n## Decisión\nAnte timeout del adquirente se envía un reverso automático.\n")
    (vault / "W" / "kpis.md").write_text("# KPIs\n\n## Métricas\nTasa de aprobación y latencia p95 del programa.\n")
    index = VaultIndex(vault, ["W"], tmp_path / "i.db")

    class FakeEmbedder:
        model = "fake"

        async def __call__(self, texts: list[str]) -> list[list[float]]:
            # "meaning": banks not answering ~ timeout; measuring ~ KPIs
            return [[1.0, 0.0] if any(w in t.lower() for w in ("timeout", "contesta")) else [0.0, 1.0] for t in texts]

    hits = await index.hybrid_search("¿qué pasa si el banco no contesta?", FakeEmbedder(), limit=2)  # type: ignore[arg-type]
    assert hits[0]["note"] == "W/reverso.md"
    words_only = await index.hybrid_search("latencia", None, limit=2)
    assert [h["note"] for h in words_only] == ["W/kpis.md"]
