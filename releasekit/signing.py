import base64
import os
from pathlib import Path
import tempfile
import uuid


def configure_signing():
    values = {"SIGNING_KEY_ALIAS": os.environ.get("RELEASEKIT_KEY_ALIAS", ""),
              "SIGNING_KEY_PASSWORD": os.environ.get("RELEASEKIT_KEY_PASSWORD", ""),
              "SIGNING_STORE_PASSWORD": os.environ.get("RELEASEKIT_STORE_PASSWORD", "")}
    encoded = os.environ.get("RELEASEKIT_KEYSTORE_BASE64", "")
    if not encoded or not all(values.values()):
        raise ValueError("Configure the Android keystore, alias, and signing password secrets")
    decoded = base64.b64decode("".join(encoded.split()), validate=True)
    with tempfile.NamedTemporaryFile(prefix="releasekit-", suffix=".jks", dir=os.environ.get("RUNNER_TEMP"), delete=False) as key:
        key.write(decoded)
        filename = key.name
    try:
        values["SIGNING_STORE_FILE"] = filename
        with open(os.environ["GITHUB_ENV"], "a") as stream:
            for name, value in values.items():
                delimiter = "releasekit_" + uuid.uuid4().hex
                stream.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")
    except BaseException:
        Path(filename).unlink()
        raise
    return {"keystore": filename}
