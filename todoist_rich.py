import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from rich import box
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table
from rich.theme import Theme
from rich.text import Text

DEFAULT_BASE_URL = os.getenv("TODOIST_API_URL", "https://api.todoist.com/rest/v2")
TOKEN_ENV_VARS = ["TODOIST_API_TOKEN", "TODOIST_TOKEN"]

DRACULA = {
    "background": "#282a36",
    "foreground": "#f8f8f2",
    "comment": "#6272a4",
    "cyan": "#8be9fd",
    "green": "#50fa7b",
    "orange": "#ffb86c",
    "pink": "#ff79c6",
    "purple": "#bd93f9",
    "red": "#ff5555",
    "yellow": "#f1fa8c",
}

custom_theme = Theme({
    "info": DRACULA["cyan"],
    "success": DRACULA["green"],
    "danger": f"bold {DRACULA['red']}",
    "warning": DRACULA["orange"],
    "title": f"bold {DRACULA['pink']}",
    "header": f"bold {DRACULA['purple']}",
    "border": DRACULA["purple"],
    "text": DRACULA["foreground"],
    "dim": DRACULA["comment"],
})
console = Console(theme=custom_theme)
BOX_STYLE = box.HEAVY_EDGE

SCRIPT_DIR = Path(__file__).resolve().parent
TOKEN_FILE_CANDIDATES = [
    ".todoist_token",
    "todoist_token.txt",
    ".todoist_api_key",
]


def _wcm_target_name() -> str:
    try:
        parsed = urlparse(DEFAULT_BASE_URL)
        netloc = (parsed.netloc or "api.todoist.com").strip()
    except Exception:
        netloc = "api.todoist.com"
    return f"todoist-cli:{netloc}"


def _wcm_read_token() -> Optional[str]:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        CRED_TYPE_GENERIC = 1

        class CREDENTIALW(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", wintypes.LPBYTE),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", wintypes.LPVOID),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        PCREDENTIALW = ctypes.POINTER(CREDENTIALW)

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        CredReadW = advapi32.CredReadW
        CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(PCREDENTIALW)]
        CredReadW.restype = wintypes.BOOL

        CredFree = advapi32.CredFree
        CredFree.argtypes = [wintypes.LPVOID]
        CredFree.restype = None

        pcred = PCREDENTIALW()
        if not CredReadW(_wcm_target_name(), CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
            return None

        try:
            cred = pcred.contents
            if not cred.CredentialBlob or cred.CredentialBlobSize <= 0:
                return None
            blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        finally:
            CredFree(pcred)

        try:
            return _normalize_token(blob.decode("utf-16-le", errors="replace"))
        except Exception:
            return _normalize_token(blob.decode("utf-8", errors="replace"))
    except Exception:
        return None


def _wcm_write_token(token: str, overwrite: bool = False) -> None:
    if os.name != "nt":
        raise RuntimeError("Windows Credential Manager is only available on Windows.")

    token_norm = _normalize_token(token)
    if not token_norm:
        raise RuntimeError("Token is empty.")

    existing = _wcm_read_token()
    if existing and not overwrite:
        raise RuntimeError("Token already stored. Use --force to overwrite.")

    import ctypes
    from ctypes import wintypes

    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2

    class CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", wintypes.LPBYTE),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", wintypes.LPVOID),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    CredWriteW = advapi32.CredWriteW
    CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
    CredWriteW.restype = wintypes.BOOL

    blob = token_norm.encode("utf-16-le")
    blob_buf = ctypes.create_string_buffer(blob, len(blob))

    cred = CREDENTIALW()
    cred.Flags = 0
    cred.Type = CRED_TYPE_GENERIC
    cred.TargetName = ctypes.c_wchar_p(_wcm_target_name())
    cred.Comment = ctypes.c_wchar_p("Todoist CLI token")
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(blob_buf, wintypes.LPBYTE)
    cred.Persist = CRED_PERSIST_LOCAL_MACHINE
    cred.AttributeCount = 0
    cred.Attributes = None
    cred.TargetAlias = None
    cred.UserName = ctypes.c_wchar_p("todoist-cli")

    if not CredWriteW(ctypes.byref(cred), 0):
        err = ctypes.get_last_error()
        raise RuntimeError(f"Failed to write token to Windows Credential Manager (error {err}).")


def _wcm_delete_token() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        CRED_TYPE_GENERIC = 1

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        CredDeleteW = advapi32.CredDeleteW
        CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        CredDeleteW.restype = wintypes.BOOL

        ok = CredDeleteW(_wcm_target_name(), CRED_TYPE_GENERIC, 0)
        return bool(ok)
    except Exception:
        return False


def _read_dotenv(path: Path) -> dict:
    values = {}
    try:
        if not path.exists():
            return values
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            values[key.strip()] = val.strip().strip('"').strip("'")
    except Exception:
        pass
    return values


def _normalize_token(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    token = token.strip()
    if token.startswith("Bearer ") or token.startswith("bearer "):
        token = token.split(" ", 1)[1].strip()
    return token or None


def _find_api_token() -> Optional[str]:
    return _wcm_read_token()

    candidates = [Path.cwd(), SCRIPT_DIR, Path.home()]
    for base in candidates:
        token_path = base / ".env"
        values = _read_dotenv(token_path)
        token = _normalize_token(values.get("TODOIST_API_TOKEN"))
        if token:
            return token

        for name in TOKEN_FILE_CANDIDATES:
            file_path = base / name
            if not file_path.exists():
                continue
            try:
                value = _normalize_token(file_path.read_text(encoding="utf-8", errors="ignore"))
                if value:
                    return value
            except Exception:
                continue

    return None


def _get_headers(token: Optional[str] = None) -> dict:
    resolved = _normalize_token(token) or _find_api_token()
    if not resolved:
        console.print(
            "[danger]Missing Todoist API token. Store it in Windows Credential Manager via `tod token set`.[/danger]"
        )
        sys.exit(1)
    return {
        "Authorization": f"Bearer {resolved}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _format_date(date_str: Optional[str]) -> str:
    if not date_str:
        return "----"
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return date_str


def _format_time(date_str: Optional[str]) -> str:
    if not date_str:
        return ""
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.strftime("%I:%M %p")
    except Exception:
        return ""


def _handle_response(response: requests.Response) -> Optional[dict]:
    try:
        response.raise_for_status()
        if response.content:
            return response.json()
        return {}
    except Exception as exc:
        console.print(f"[danger]API error: {exc}[/danger]")
        if response.content:
            try:
                console.print(response.text)
            except Exception:
                pass
        return None


def _print_tasks(tasks: list[dict], title: str = "Tasks") -> None:
    if not tasks:
        console.print(f"[warning]No {title.lower()} found.[/warning]")
        return

    table = Table(
        title=title,
        title_style="title",
        box=BOX_STYLE,
        show_header=True,
        header_style="header",
        border_style="border",
    )
    table.add_column("#", justify="right", width=4, style="dim")
    table.add_column("ID", style="info", width=10)
    table.add_column("Content", style="text", overflow="fold")
    table.add_column("Project", style="info", width=20)
    table.add_column("Due", style="dim", width=18)
    table.add_column("Priority", justify="center", width=9, style="warning")

    for idx, task in enumerate(tasks, 1):
        project = task.get("project_id") or "-"
        due = task.get("due") or {}
        due_str = due.get("string") or _format_date(due.get("date"))
        due_time = _format_time(due.get("datetime")) if due.get("datetime") else ""
        due_display = f"{due_str} {due_time}".strip()
        content = Text(str(task.get("content", "")))
        table.add_row(
            str(idx),
            str(task.get("id")),
            content,
            str(project),
            due_display,
            str(task.get("priority", 1)),
        )
    console.print(table)


def cmd_list(args: argparse.Namespace) -> None:
    params = {}
    if args.project:
        params["project_id"] = args.project
    if args.label:
        params["label_id"] = args.label
    if args.filter:
        params["filter"] = args.filter
    try:
        response = requests.get(
            f"{DEFAULT_BASE_URL}/tasks",
            headers=_get_headers(args.token),
            params=params,
            timeout=10,
        )
    except Exception as exc:
        console.print(f"[danger]Could not fetch tasks: {exc}[/danger]")
        return
    data = _handle_response(response)
    if data is None:
        return
    _print_tasks(data, title="Todoist Tasks")


def cmd_show(args: argparse.Namespace) -> None:
    task_id = args.task_id
    if not task_id:
        console.print("[danger]Usage: tod show <task-id>[/danger]")
        return
    try:
        response = requests.get(
            f"{DEFAULT_BASE_URL}/tasks/{task_id}",
            headers=_get_headers(args.token),
            timeout=10,
        )
    except Exception as exc:
        console.print(f"[danger]Request failed: {exc}[/danger]")
        return
    task = _handle_response(response)
    if not task:
        return
    console.print(json.dumps(task, indent=2))


def cmd_add(args: argparse.Namespace) -> None:
    if not args.content:
        console.print("[danger]Provide task content via --content or positional argument.[/danger]")
        return
    body = {"content": args.content}
    if args.project:
        body["project_id"] = args.project
    if args.label:
        body["label_ids"] = [args.label]
    if args.due:
        body["due_string"] = args.due
    if args.priority:
        body["priority"] = max(1, min(4, args.priority))
    try:
        response = requests.post(
            f"{DEFAULT_BASE_URL}/tasks",
            headers=_get_headers(args.token),
            json=body,
            timeout=10,
        )
    except Exception as exc:
        console.print(f"[danger]Error creating task: {exc}[/danger]")
        return
    task = _handle_response(response)
    if task:
        console.print(f"[success]Task created:[/success] {task.get('content')} ([info]{task.get('id')}[/info])")


def cmd_complete(args: argparse.Namespace) -> None:
    if not args.task_id:
        console.print("[danger]Usage: tod complete <task-id>[/danger]")
        return
    try:
        response = requests.post(
            f"{DEFAULT_BASE_URL}/tasks/{args.task_id}/close",
            headers=_get_headers(args.token),
            timeout=10,
        )
    except Exception as exc:
        console.print(f"[danger]Error closing task: {exc}[/danger]")
        return
    if response.status_code == 204:
        console.print(f"[success]Completed task {args.task_id}.[/success]")
    else:
        _handle_response(response)


def cmd_projects(_: argparse.Namespace) -> None:
    try:
        response = requests.get(
            f"{DEFAULT_BASE_URL}/projects",
            headers=_get_headers(),
            timeout=10,
        )
    except Exception as exc:
        console.print(f"[danger]Error fetching projects: {exc}[/danger]")
        return
    projects = _handle_response(response)
    if not projects:
        return
    table = Table(
        title="Projects",
        title_style="title",
        box=BOX_STYLE,
        show_header=True,
        header_style="header",
        border_style="border",
    )
    table.add_column("ID", style="info", width=8, justify="right")
    table.add_column("Name", style="text")
    for project in projects:
        table.add_row(str(project.get("id")), project.get("name", ""))
    console.print(table)


def cmd_labels(_: argparse.Namespace) -> None:
    try:
        response = requests.get(
            f"{DEFAULT_BASE_URL}/labels",
            headers=_get_headers(),
            timeout=10,
        )
    except Exception as exc:
        console.print(f"[danger]Error fetching labels: {exc}[/danger]")
        return
    labels = _handle_response(response)
    if not labels:
        return
    table = Table(
        title="Labels",
        title_style="title",
        box=BOX_STYLE,
        show_header=True,
        header_style="header",
        border_style="border",
    )
    table.add_column("ID", style="info", width=8, justify="right")
    table.add_column("Name", style="text")
    for label in labels:
        table.add_row(str(label.get("id")), label.get("name", ""))
    console.print(table)


def cmd_token(args: argparse.Namespace) -> None:
    cmd = getattr(args, "token_command", None)
    if not cmd:
        console.print("[danger]Usage: tod token <set|clear|status>[/danger]")
        return

    if cmd == "set":
        token = args.token
        if not token:
            token = Prompt.ask("[bold cyan]Paste Todoist API token[/]", password=True)
        try:
            _wcm_write_token(token, overwrite=getattr(args, "force", False))
            console.print(f"[success]Stored token in Windows Credential Manager as '{_wcm_target_name()}'.[/success]")
        except Exception as exc:
            console.print(f"[danger]{exc}[/danger]")
    elif cmd == "clear":
        if _wcm_delete_token():
            console.print(f"[success]Deleted token '{_wcm_target_name()}'.[/success]")
        else:
            console.print(f"[warning]No stored token found for '{_wcm_target_name()}'.[/warning]")
    elif cmd == "status":
        env_set = bool(_normalize_token(os.getenv("TODOIST_API_TOKEN") or os.getenv("TODOIST_TOKEN")))
        wcm_set = bool(_wcm_read_token())
        console.print(f"Env token set: {'yes' if env_set else 'no'}")
        console.print(f"WCM token set: {'yes' if wcm_set else 'no'} ({_wcm_target_name()})")
        if not env_set and not wcm_set:
            console.print("[warning]No token configured. Run `tod token set`.[/warning]")
    else:
        console.print("[danger]Unknown token command. Use set|clear|status.[/danger]")


def main() -> None:
    parser = argparse.ArgumentParser(description="Todoist CLI (Rich)")
    parser.add_argument("--token", help="Todoist API token (overrides discovery)")
    subparsers = parser.add_subparsers(dest="command")

    list_p = subparsers.add_parser("list", help="List tasks")
    list_p.add_argument("--project", type=int, help="Filter by project ID")
    list_p.add_argument("--label", type=int, help="Filter by label ID")
    list_p.add_argument("--filter", help="Todoist filter string (e.g. today)")

    subparsers.add_parser("projects", help="List projects").set_defaults(func=cmd_projects)
    subparsers.add_parser("labels", help="List labels").set_defaults(func=cmd_labels)

    show_p = subparsers.add_parser("show", help="Show task detail")
    show_p.add_argument("task_id", help="Task ID")
    show_p.set_defaults(func=cmd_show)

    add_p = subparsers.add_parser("add", help="Create a task")
    add_p.add_argument("content", nargs="?", help="Task content")
    add_p.add_argument("--project", type=int, help="Project ID")
    add_p.add_argument("--label", type=int, help="Label ID")
    add_p.add_argument("--due", help="Due string (e.g. tomorrow at 9am)")
    add_p.add_argument("--priority", type=int, choices=[1, 2, 3, 4], help="Priority (1=low,4=urgent)")
    add_p.set_defaults(func=cmd_add)

    complete_p = subparsers.add_parser("complete", help="Complete a task")
    complete_p.add_argument("task_id", help="Task ID")
    complete_p.set_defaults(func=cmd_complete)

    list_p.set_defaults(func=cmd_list)

    token_p = subparsers.add_parser("token", help="Manage stored Todoist API token")
    token_sub = token_p.add_subparsers(dest="token_command")
    token_set = token_sub.add_parser("set", help="Store token in Windows Credential Manager")
    token_set.add_argument("--token", help="Token value")
    token_set.add_argument("--force", action="store_true", help="Overwrite existing stored token")
    token_sub.add_parser("clear", help="Delete stored token from Windows Credential Manager")
    token_sub.add_parser("status", help="Show whether a token is configured")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return
    if args.command == "token":
        cmd_token(args)
        return
    if hasattr(args, "func"):
        try:
            args.func(args)
        except SystemExit:
            raise
        except Exception as exc:
            console.print(f"[danger]Unhandled error: {exc}[/danger]")
    else:
        parser.print_help()

    token_set = token_sub.add_parser("set", help="Store token in Windows Credential Manager")
    token_set.add_argument("--token", help="Token value")
    token_set.add_argument("--force", action="store_true", help="Overwrite existing stored token")
    token_sub.add_parser("clear", help="Delete stored token from Windows Credential Manager")
    token_sub.add_parser("status", help="Show whether a token is configured")


if __name__ == "__main__":
    main()
