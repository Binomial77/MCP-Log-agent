import os
from fastmcp import FastMCP

mcp = FastMCP("support-tools")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
RULES_FILE = os.path.join(BASE_DIR, "data", "rules.txt")


@mcp.tool()
def get_company_rules() -> str:
    """Return the company rules and policies (working hours, leave, refunds, expenses, IT policy).
    Use this for ANY question about company rules or policies."""
    with open(RULES_FILE, encoding="utf-8") as f:
        return f.read()


@mcp.tool()
def get_log_file_names() -> list:
    """List all available log files, each with its first line as a preview."""
    files = []
    for name in sorted(os.listdir(LOG_DIR)):
        if name.endswith((".log", ".txt")):
            with open(os.path.join(LOG_DIR, name), encoding="utf-8", errors="ignore") as f:
                files.append({"file": name, "preview": f.readline().strip()})
    return files


@mcp.tool()
def search_log(file_name: str, keyword: str, limit: int = 20) -> dict:
    """Search a log file for a keyword. Returns matching entries from LAST to FIRST
    (newest first), up to `limit` entries."""
    path = os.path.join(LOG_DIR, os.path.basename(file_name))  
    if not os.path.isfile(path):
        return {"error": f"File '{file_name}' not found. Call get_log_file_names first."}

    with open(path, encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    matches = []
    for i in range(len(lines) - 1, -1, -1):          
        if keyword.lower() in lines[i].lower():
            matches.append({"line": i + 1, "text": lines[i].strip()})
            if len(matches) >= limit:
                break

    return {"file": file_name, "keyword": keyword, "found": len(matches), "matches": matches}


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=5000)