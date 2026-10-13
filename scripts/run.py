"""Run the local instance using a generated local admin account."""

import json
import os
import runpy
import secrets
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
os.chdir(root)
env_file = root / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
p = root / ".local/admin.json"
p.parent.mkdir(exist_ok=True)
if not p.exists():
    p.write_text(
        json.dumps({"username": "admin", "password": secrets.token_urlsafe(15)})
    )
    p.chmod(0o600)
creds = json.loads(p.read_text())
os.environ.setdefault("ADMIN_USERNAME", creds["username"])
os.environ.setdefault("ADMIN_PASSWORD", creds["password"])
os.environ.setdefault("DATA_DIR", str(root / ".local/data"))
os.environ.setdefault("PORT", "18791")
Path(os.environ["DATA_DIR"]).mkdir(parents=True, exist_ok=True, mode=0o700)
sys.path.insert(0, str(root / "server"))
runpy.run_path(str(root / "server/app.py"), run_name="__main__")
