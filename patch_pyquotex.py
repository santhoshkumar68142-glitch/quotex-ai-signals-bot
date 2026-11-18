from pathlib import Path
import pyquotex

login_file = Path(pyquotex.__file__).parent / "network" / "login.py"

source = login_file.read_text()

old = "reason_msg = f'Error on access page: {home.reason_phrase}'"
new = "reason_msg = f'Error on access page: {getattr(home, \"reason_phrase\", f\"HTTP {home.status_code}\")}'"

if old in source:
    login_file.write_text(source.replace(old, new))
    print("pyquotex compatibility patch applied successfully")
elif new in source:
    print("Patch already applied")
else:
    print("Expected code not found. No changes made.")
