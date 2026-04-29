def format_context(ctx: dict) -> str:
    """Return a ## CONTEXT markdown block, or '' if all fields are blank."""
    pairs = [
        ("Country / Region", ctx.get("country", "")),
        ("Industry / Sector", ctx.get("industry", "")),
        ("Stage",             ctx.get("stage", "")),
        ("Target Audience",   ctx.get("target_audience", "")),
        ("Notes",             ctx.get("notes", "")),
    ]
    lines = [f"- **{label}:** {val.strip()}" for label, val in pairs if val.strip()]
    if not lines:
        return ""
    return "## CONTEXT\n" + "\n".join(lines)
