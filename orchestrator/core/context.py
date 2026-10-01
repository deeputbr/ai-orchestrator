"""Read only explicit, project-contained UTF-8 context files."""
import hashlib
from pathlib import Path


def collect(root, paths, limit):
    root = Path(root).resolve()
    result, used = [], 0
    for name in paths:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or any(p.startswith(".") for p in relative.parts):
            raise ValueError("Context requires non-hidden relative project paths")
        if relative.suffix.lower() in (".pem", ".key", ".p12", ".pfx"):
            raise ValueError("Credential-like context file blocked")
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Context file missing or outside project")
        with path.open("rb") as stream:
            data = stream.read(limit - used + 1)
        used += len(data)
        if used > limit:
            raise ValueError("Context budget exceeded; select smaller relevant files")
        result.append({"path": name, "sha256": hashlib.sha256(data).hexdigest(), "text": data.decode("utf-8")})
    return result
