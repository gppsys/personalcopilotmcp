"""Terminal UI application using Rich."""

from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from executive_copilot.admin.config_manager import ConfigManager
from executive_copilot.credentials import CredentialManager

console = Console()

AI_PROVIDERS = [
    ("1", "openai", "OpenAI"),
    ("2", "anthropic", "Anthropic"),
    ("3", "azure_openai", "Azure OpenAI"),
    ("4", "google", "Google (Gemini)"),
    ("5", "local", "Local (Ollama/LM Studio)"),
]

MODELS_BY_PROVIDER = {
    "openai": [
        ("1", "gpt-4o", "Most capable"),
        ("2", "gpt-4o-mini", "Recommended"),
        ("3", "gpt-4-turbo", "Fast"),
        ("4", "gpt-3.5-turbo", "Fastest"),
        ("5", "o1", "Reasoning"),
        ("6", "o1-mini", "Fast reasoning"),
    ],
    "anthropic": [
        ("1", "claude-sonnet-4-20250514", "Recommended"),
        ("2", "claude-opus-4-20250514", "Most capable"),
        ("3", "claude-3-5-sonnet-20241022", "Claude 3.5 Sonnet"),
        ("4", "claude-3-5-haiku-20241022", "Fastest"),
    ],
    "azure_openai": [
        ("1", "gpt-4o", "Use deployment name"),
        ("2", "gpt-4o-mini", "Use deployment name"),
        ("3", "gpt-4-turbo", "Use deployment name"),
    ],
    "google": [
        ("1", "gemini-1.5-pro", "Most capable"),
        ("2", "gemini-1.5-flash", "Fast"),
        ("3", "gemini-2.0-flash", "Newest"),
    ],
    "local": [
        ("1", "llama3.2", "Llama 3.2"),
        ("2", "llama3.1", "Llama 3.1"),
        ("3", "mistral", "Mistral"),
        ("4", "mixtral", "Mixtral"),
        ("5", "codellama", "Code Llama"),
        ("6", "phi3", "Phi-3"),
    ],
}


def run_tui(config_dir: Path | None = None) -> None:
    """Run the terminal UI for configuration."""
    config_dir = config_dir or Path("config")
    config_manager = ConfigManager(config_dir)
    credential_manager = CredentialManager(config_dir / ".credentials")

    console.print(Panel.fit(
        "[bold blue]Executive Copilot[/bold blue]\n"
        "[dim]Terminal Configuration Tool[/dim]",
        border_style="blue",
    ))

    while True:
        console.print("\n[bold]Main Menu[/bold]")
        console.print("1. General Settings")
        console.print("2. Organizations")
        console.print("3. Context Rules")
        console.print("4. Credentials")
        console.print("5. Automation Jobs")
        console.print("0. Exit")

        choice = Prompt.ask("Select option", choices=["0", "1", "2", "3", "4", "5"])

        if choice == "0":
            console.print("[green]Goodbye![/green]")
            break
        elif choice == "1":
            general_settings_menu(config_manager)
        elif choice == "2":
            organizations_menu(config_manager)
        elif choice == "3":
            context_rules_menu(config_manager)
        elif choice == "4":
            credentials_menu(credential_manager)
        elif choice == "5":
            automation_menu(config_manager)


def general_settings_menu(config_manager: ConfigManager) -> None:
    """General settings submenu."""
    config = config_manager.load_config()
    ai_config = config.get("ai", {})
    legacy_model_routing = config.get("model_routing", {})

    console.print("\n[bold]General Settings[/bold]")

    # Display current settings
    table = Table(title="Current Settings")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Environment", config.get("environment", "development"))
    table.add_row("Debug", str(config.get("debug", True)))
    table.add_row("Log Level", config.get("logging", {}).get("level", "INFO"))
    table.add_row("AI Provider", ai_config.get("provider", "openai"))
    table.add_row(
        "Default Model",
        ai_config.get("default_model", legacy_model_routing.get("default_model", "gpt-4o-mini")),
    )
    table.add_row("Premium Model", ai_config.get("premium_model", "gpt-4o"))
    table.add_row(
        "Max Tokens",
        str(ai_config.get("max_output_tokens", legacy_model_routing.get("max_tokens", 4096))),
    )
    table.add_row(
        "Temperature",
        str(ai_config.get("temperature", legacy_model_routing.get("temperature", 0.7))),
    )
    if ai_config.get("azure_endpoint"):
        table.add_row("Azure Endpoint", ai_config.get("azure_endpoint", ""))
    if ai_config.get("local_base_url"):
        table.add_row("Local Base URL", ai_config.get("local_base_url", ""))

    console.print(table)

    if Confirm.ask("Edit settings?"):
        env = Prompt.ask(
            "Environment",
            choices=["development", "staging", "production"],
            default=config.get("environment", "development"),
        )
        debug = Confirm.ask("Debug mode?", default=config.get("debug", True))
        log_level = Prompt.ask(
            "Log level",
            choices=["DEBUG", "INFO", "WARNING", "ERROR"],
            default=config.get("logging", {}).get("level", "INFO"),
        )

        # Select AI provider
        console.print("\n[bold]AI Provider:[/bold]")
        provider_map = {choice: provider_id for choice, provider_id, _ in AI_PROVIDERS}
        current_provider = ai_config.get("provider", "openai")
        current_choice = next((c for c, p, _ in AI_PROVIDERS if p == current_provider), "1")
        for choice, provider_id, label in AI_PROVIDERS:
            console.print(f"{choice}. {label}")
        provider_choice = Prompt.ask("Select provider", choices=list(provider_map), default=current_choice)
        provider = provider_map[provider_choice]

        # Select models for the chosen provider
        model_options = MODELS_BY_PROVIDER.get(provider, MODELS_BY_PROVIDER["openai"])
        console.print(f"\n[bold]{provider.replace('_', ' ').title()} Models:[/bold]")
        for choice, model_id, label in model_options:
            console.print(f"{choice}. {model_id} ({label})")

        model_map = {choice: model_id for choice, model_id, _ in model_options}
        model_choice = Prompt.ask("Select default model", choices=list(model_map), default="2")
        model = model_map[model_choice]
        premium_choice = Prompt.ask("Select premium model", choices=list(model_map), default="1")
        premium_model = model_map[premium_choice]

        max_tokens = IntPrompt.ask(
            "Max tokens",
            default=ai_config.get("max_output_tokens", legacy_model_routing.get("max_tokens", 4096)),
        )
        temp = Prompt.ask(
            "Temperature (0-2)",
            default=str(ai_config.get("temperature", legacy_model_routing.get("temperature", 0.7))),
        )

        # Provider-specific settings
        azure_endpoint = None
        azure_deployment = None
        local_base_url = None

        if provider == "azure_openai":
            console.print("\n[bold]Azure OpenAI Settings:[/bold]")
            azure_endpoint = Prompt.ask(
                "Azure endpoint URL",
                default=ai_config.get("azure_endpoint", "https://your-resource.openai.azure.com"),
            )
            azure_deployment = Prompt.ask(
                "Deployment name",
                default=ai_config.get("azure_deployment", model),
            )
        elif provider == "local":
            console.print("\n[bold]Local Model Settings:[/bold]")
            local_base_url = Prompt.ask(
                "API base URL",
                default=ai_config.get("local_base_url", "http://localhost:11434"),
            )

        ai_update: dict[str, Any] = {
            "provider": provider,
            "default_model": model,
            "premium_model": premium_model,
            "max_output_tokens": max_tokens,
            "temperature": float(temp),
        }
        if azure_endpoint:
            ai_update["azure_endpoint"] = azure_endpoint
        if azure_deployment:
            ai_update["azure_deployment"] = azure_deployment
        if local_base_url:
            ai_update["local_base_url"] = local_base_url

        config_manager.update_config({
            "environment": env,
            "debug": debug,
            "logging": {"level": log_level},
            "ai": ai_update,
        })
        console.print("[green]Settings saved![/green]")


def organizations_menu(config_manager: ConfigManager) -> None:
    """Organizations submenu."""
    while True:
        orgs = config_manager.load_organizations()

        console.print("\n[bold]Organizations[/bold]")

        if orgs:
            table = Table()
            table.add_column("#", style="dim")
            table.add_column("Name", style="cyan")
            table.add_column("Type", style="green")
            table.add_column("Domains", style="yellow")
            table.add_column("Importance", style="magenta")

            for i, org in enumerate(orgs, 1):
                table.add_row(
                    str(i),
                    org.get("name", ""),
                    org.get("organization_type", ""),
                    ", ".join(org.get("domains", [])),
                    str(org.get("importance", 5)),
                )
            console.print(table)
        else:
            console.print("[dim]No organizations configured[/dim]")

        console.print("\n1. Add organization")
        console.print("2. Edit organization")
        console.print("3. Delete organization")
        console.print("0. Back")

        choice = Prompt.ask("Select option", choices=["0", "1", "2", "3"])

        if choice == "0":
            break
        elif choice == "1":
            add_organization(config_manager, orgs)
        elif choice == "2" and orgs:
            idx = IntPrompt.ask("Organization number to edit", default=1) - 1
            if 0 <= idx < len(orgs):
                edit_organization(config_manager, orgs, idx)
        elif choice == "3" and orgs:
            idx = IntPrompt.ask("Organization number to delete", default=1) - 1
            if 0 <= idx < len(orgs):
                if Confirm.ask(f"Delete '{orgs[idx].get('name')}'?"):
                    orgs.pop(idx)
                    config_manager.save_organizations(orgs)
                    console.print("[green]Organization deleted![/green]")


def add_organization(config_manager: ConfigManager, orgs: list[dict[str, Any]]) -> None:
    """Add a new organization."""
    console.print("\n[bold]Add Organization[/bold]")

    name = Prompt.ask("Name")
    short_name = Prompt.ask("Short name (optional)", default="")
    org_type = Prompt.ask(
        "Type",
        choices=["employer", "client", "vendor", "partner", "school", "personal", "other"],
        default="client",
    )
    domains = Prompt.ask("Email domains (comma-separated)", default="")
    importance = IntPrompt.ask("Importance (1-10)", default=5)

    console.print("\n[bold]Context Sources (comma-separated):[/bold]")
    console.print("[dim]Options: asana, outlook, gmail, google_calendar, memory, documentation, previous_threads, tasks[/dim]")
    sources = Prompt.ask("Sources", default="memory,previous_threads")

    org: dict[str, Any] = {
        "name": name,
        "organization_type": org_type,
        "domains": [d.strip() for d in domains.split(",") if d.strip()],
        "context_sources": [s.strip() for s in sources.split(",") if s.strip()],
        "importance": importance,
        "tags": [],
    }
    if short_name:
        org["short_name"] = short_name

    orgs.append(org)
    config_manager.save_organizations(orgs)
    console.print("[green]Organization added![/green]")


def edit_organization(config_manager: ConfigManager, orgs: list[dict[str, Any]], idx: int) -> None:
    """Edit an existing organization."""
    org = orgs[idx]
    console.print(f"\n[bold]Edit Organization: {org.get('name')}[/bold]")

    org["name"] = Prompt.ask("Name", default=org.get("name", ""))
    org["short_name"] = Prompt.ask("Short name", default=org.get("short_name", ""))
    org["organization_type"] = Prompt.ask(
        "Type",
        choices=["employer", "client", "vendor", "partner", "school", "personal", "other"],
        default=org.get("organization_type", "client"),
    )
    domains = Prompt.ask("Email domains", default=", ".join(org.get("domains", [])))
    org["domains"] = [d.strip() for d in domains.split(",") if d.strip()]
    org["importance"] = IntPrompt.ask("Importance (1-10)", default=org.get("importance", 5))

    sources = Prompt.ask("Context sources", default=", ".join(org.get("context_sources", [])))
    org["context_sources"] = [s.strip() for s in sources.split(",") if s.strip()]

    orgs[idx] = org
    config_manager.save_organizations(orgs)
    console.print("[green]Organization updated![/green]")


def context_rules_menu(config_manager: ConfigManager) -> None:
    """Context rules submenu."""
    while True:
        rules = config_manager.load_context_rules()

        console.print("\n[bold]Context Rules[/bold]")

        if rules:
            table = Table()
            table.add_column("#", style="dim")
            table.add_column("Name", style="cyan")
            table.add_column("Priority", style="green")
            table.add_column("Enabled", style="yellow")
            table.add_column("Sources", style="magenta")

            for i, rule in enumerate(rules, 1):
                table.add_row(
                    str(i),
                    rule.get("name", ""),
                    str(rule.get("priority", 0)),
                    "Yes" if rule.get("enabled", True) else "No",
                    ", ".join(rule.get("sources", [])[:3]) + ("..." if len(rule.get("sources", [])) > 3 else ""),
                )
            console.print(table)
        else:
            console.print("[dim]No context rules configured[/dim]")

        console.print("\n1. Add rule")
        console.print("2. Edit rule")
        console.print("3. Delete rule")
        console.print("0. Back")

        choice = Prompt.ask("Select option", choices=["0", "1", "2", "3"])

        if choice == "0":
            break
        elif choice == "1":
            add_context_rule(config_manager, rules)
        elif choice == "2" and rules:
            idx = IntPrompt.ask("Rule number to edit", default=1) - 1
            if 0 <= idx < len(rules):
                edit_context_rule(config_manager, rules, idx)
        elif choice == "3" and rules:
            idx = IntPrompt.ask("Rule number to delete", default=1) - 1
            if 0 <= idx < len(rules):
                if Confirm.ask(f"Delete '{rules[idx].get('name')}'?"):
                    rules.pop(idx)
                    config_manager.save_context_rules(rules)
                    console.print("[green]Rule deleted![/green]")


def add_context_rule(config_manager: ConfigManager, rules: list[dict[str, Any]]) -> None:
    """Add a new context rule."""
    console.print("\n[bold]Add Context Rule[/bold]")

    name = Prompt.ask("Rule name")
    description = Prompt.ask("Description (optional)", default="")
    priority = IntPrompt.ask("Priority (higher = first)", default=0)
    enabled = Confirm.ask("Enabled?", default=True)

    console.print("\n[bold]Conditions (leave empty to skip):[/bold]")
    domain = Prompt.ask("Match sender domain", default="")
    org_name = Prompt.ask("Match organization name", default="")
    content = Prompt.ask("Content contains", default="")

    conditions: dict[str, str] = {}
    if domain:
        conditions["domain"] = domain
    if org_name:
        conditions["organization"] = org_name
    if content:
        conditions["content_contains"] = content

    console.print("\n[bold]Sources (comma-separated):[/bold]")
    console.print("[dim]Options: asana, memory, documentation, previous_threads, calendar, tasks, people, organizations[/dim]")
    sources = Prompt.ask("Sources", default="memory")

    rule: dict[str, Any] = {
        "name": name,
        "priority": priority,
        "enabled": enabled,
        "conditions": conditions,
        "sources": [s.strip() for s in sources.split(",") if s.strip()],
    }
    if description:
        rule["description"] = description

    rules.append(rule)
    config_manager.save_context_rules(rules)
    console.print("[green]Rule added![/green]")


def edit_context_rule(config_manager: ConfigManager, rules: list[dict[str, Any]], idx: int) -> None:
    """Edit an existing context rule."""
    rule = rules[idx]
    console.print(f"\n[bold]Edit Rule: {rule.get('name')}[/bold]")

    rule["name"] = Prompt.ask("Name", default=rule.get("name", ""))
    rule["description"] = Prompt.ask("Description", default=rule.get("description", ""))
    rule["priority"] = IntPrompt.ask("Priority", default=rule.get("priority", 0))
    rule["enabled"] = Confirm.ask("Enabled?", default=rule.get("enabled", True))

    conditions = rule.get("conditions", {})
    conditions["domain"] = Prompt.ask("Match domain", default=conditions.get("domain", ""))
    conditions["organization"] = Prompt.ask("Match organization", default=conditions.get("organization", ""))
    conditions["content_contains"] = Prompt.ask("Content contains", default=conditions.get("content_contains", ""))

    # Clean empty conditions
    rule["conditions"] = {k: v for k, v in conditions.items() if v}

    sources = Prompt.ask("Sources", default=", ".join(rule.get("sources", [])))
    rule["sources"] = [s.strip() for s in sources.split(",") if s.strip()]

    rules[idx] = rule
    config_manager.save_context_rules(rules)
    console.print("[green]Rule updated![/green]")


def credentials_menu(credential_manager: CredentialManager) -> None:
    """Credentials submenu."""
    while True:
        creds = credential_manager.list_credentials()

        console.print("\n[bold]Provider Credentials[/bold]")
        console.print("[dim]Each provider can have multiple accounts (e.g., work and personal email)[/dim]\n")

        if creds:
            table = Table()
            table.add_column("#", style="dim")
            table.add_column("Account", style="cyan")
            table.add_column("Provider", style="green")
            table.add_column("Type", style="yellow")
            table.add_column("Auth", style="magenta")

            for i, cred in enumerate(creds, 1):
                auth_info = []
                if cred.get("has_client_id"):
                    auth_info.append("OAuth")
                if cred.get("has_api_key"):
                    auth_info.append("API Key")
                if cred.get("has_access_token"):
                    auth_info.append("Token")
                table.add_row(
                    str(i),
                    cred.get("account_label") or cred.get("account_id", "default"),
                    cred["provider_name"],
                    cred["provider_type"],
                    ", ".join(auth_info) if auth_info else "None",
                )
            console.print(table)
        else:
            console.print("[dim]No credentials configured[/dim]")

        console.print("\n1. Add account")
        console.print("2. Delete account")
        console.print("0. Back")

        choice = Prompt.ask("Select option", choices=["0", "1", "2"])

        if choice == "0":
            break
        elif choice == "1":
            add_credential(credential_manager)
        elif choice == "2" and creds:
            idx = IntPrompt.ask("Account number to delete", default=1) - 1
            if 0 <= idx < len(creds):
                cred = creds[idx]
                label = cred.get("account_label") or cred.get("account_id", "default")
                if Confirm.ask(f"Delete credentials for '{label}' ({cred['provider_type']}/{cred['provider_name']})?"):
                    credential_manager.delete_credential(
                        cred["provider_type"],
                        cred["provider_name"],
                        cred.get("account_id", "default"),
                    )
                    console.print("[green]Credential deleted![/green]")


def add_credential(credential_manager: CredentialManager) -> None:
    """Add a new credential/account."""
    console.print("\n[bold]Add Account Credentials[/bold]")

    console.print("\n[bold]Provider Types:[/bold]")
    console.print("  ai: openai, anthropic, azure_openai, google")
    console.print("  email: outlook, gmail")
    console.print("  calendar: outlook, google")
    console.print("  task: asana, todoist")
    console.print("  document: google_drive, onedrive")
    console.print("  communication: slack")
    console.print("  development: github")

    provider_type = Prompt.ask("Provider type")
    provider_name = Prompt.ask("Provider name")

    console.print("\n[bold]Account Identification:[/bold]")
    console.print("[dim]For email/calendar: use your email address (e.g., user@example.com)[/dim]")
    console.print("[dim]For API keys: use 'default' or a descriptive label[/dim]")

    account_id = Prompt.ask("Account ID", default="default")
    account_label = Prompt.ask("Display label (e.g., 'Work - Primary')", default="")

    console.print("\n[dim]Leave empty to skip a field[/dim]")

    # Microsoft tenant ID for Outlook
    tenant_id = None
    if provider_name == "outlook":
        console.print("\n[bold]Microsoft Tenant ID:[/bold]")
        console.print("[dim]Use 'common' for personal, 'organizations' for work, or your specific tenant ID[/dim]")
        tenant_id = Prompt.ask("Tenant ID", default="common")

    client_id = Prompt.ask("Client ID", default="", password=False)
    client_secret = Prompt.ask("Client Secret", default="", password=True)
    api_key = Prompt.ask("API Key", default="", password=True)
    access_token = Prompt.ask("Access Token", default="", password=True)

    credential_manager.save_credential(
        provider_type=provider_type,
        provider_name=provider_name,
        account_id=account_id,
        account_label=account_label if account_label else None,
        tenant_id=tenant_id if tenant_id else None,
        client_id=client_id if client_id else None,
        client_secret=client_secret if client_secret else None,
        api_key=api_key if api_key else None,
        access_token=access_token if access_token else None,
    )
    console.print("[green]Credential saved![/green]")


def automation_menu(config_manager: ConfigManager) -> None:
    """Automation jobs submenu."""
    while True:
        jobs = config_manager.load_automation_jobs()

        console.print("\n[bold]Automation Jobs[/bold]")

        if jobs:
            table = Table()
            table.add_column("#", style="dim")
            table.add_column("Name", style="cyan")
            table.add_column("Type", style="green")
            table.add_column("Schedule", style="yellow")
            table.add_column("Enabled", style="magenta")

            for i, job in enumerate(jobs, 1):
                table.add_row(
                    str(i),
                    job.get("job_name", ""),
                    job.get("job_type", ""),
                    job.get("schedule", ""),
                    "Yes" if job.get("enabled", True) else "No",
                )
            console.print(table)
        else:
            console.print("[dim]No automation jobs configured[/dim]")

        console.print("\n1. Add job")
        console.print("2. Edit job")
        console.print("3. Delete job")
        console.print("4. Toggle job enabled/disabled")
        console.print("0. Back")

        choice = Prompt.ask("Select option", choices=["0", "1", "2", "3", "4"])

        if choice == "0":
            break
        elif choice == "1":
            add_automation_job(config_manager, jobs)
        elif choice == "2" and jobs:
            idx = IntPrompt.ask("Job number to edit", default=1) - 1
            if 0 <= idx < len(jobs):
                edit_automation_job(config_manager, jobs, idx)
        elif choice == "3" and jobs:
            idx = IntPrompt.ask("Job number to delete", default=1) - 1
            if 0 <= idx < len(jobs):
                if Confirm.ask(f"Delete '{jobs[idx].get('job_name')}'?"):
                    jobs.pop(idx)
                    config_manager.save_automation_jobs(jobs)
                    console.print("[green]Job deleted![/green]")
        elif choice == "4" and jobs:
            idx = IntPrompt.ask("Job number to toggle", default=1) - 1
            if 0 <= idx < len(jobs):
                jobs[idx]["enabled"] = not jobs[idx].get("enabled", True)
                config_manager.save_automation_jobs(jobs)
                status = "enabled" if jobs[idx]["enabled"] else "disabled"
                console.print(f"[green]Job {status}![/green]")


def add_automation_job(config_manager: ConfigManager, jobs: list[dict[str, Any]]) -> None:
    """Add a new automation job."""
    console.print("\n[bold]Add Automation Job[/bold]")

    console.print("\n[bold]Job Types:[/bold]")
    console.print("  email_sync, calendar_sync, task_sync, news_sync, daily_briefing, follow_up_detection")

    job_name = Prompt.ask("Job name")
    job_type = Prompt.ask(
        "Job type",
        choices=["email_sync", "calendar_sync", "task_sync", "news_sync", "daily_briefing", "follow_up_detection"],
        default="email_sync",
    )

    console.print("\n[bold]Schedule (cron syntax):[/bold]")
    console.print("  */5 * * * *   - Every 5 minutes")
    console.print("  0 * * * *     - Every hour")
    console.print("  0 7 * * *     - Daily at 7 AM")
    console.print("  0 9,14 * * *  - At 9 AM and 2 PM")

    schedule = Prompt.ask("Schedule", default="*/5 * * * *")
    enabled = Confirm.ask("Enabled?", default=True)

    job: dict[str, Any] = {
        "job_name": job_name,
        "job_type": job_type,
        "schedule": schedule,
        "enabled": enabled,
        "settings": {},
    }

    jobs.append(job)
    config_manager.save_automation_jobs(jobs)
    console.print("[green]Job added![/green]")


def edit_automation_job(config_manager: ConfigManager, jobs: list[dict[str, Any]], idx: int) -> None:
    """Edit an existing automation job."""
    job = jobs[idx]
    console.print(f"\n[bold]Edit Job: {job.get('job_name')}[/bold]")

    job["job_name"] = Prompt.ask("Job name", default=job.get("job_name", ""))
    job["job_type"] = Prompt.ask(
        "Job type",
        choices=["email_sync", "calendar_sync", "task_sync", "news_sync", "daily_briefing", "follow_up_detection"],
        default=job.get("job_type", "email_sync"),
    )
    job["schedule"] = Prompt.ask("Schedule", default=job.get("schedule", "*/5 * * * *"))
    job["enabled"] = Confirm.ask("Enabled?", default=job.get("enabled", True))

    jobs[idx] = job
    config_manager.save_automation_jobs(jobs)
    console.print("[green]Job updated![/green]")
