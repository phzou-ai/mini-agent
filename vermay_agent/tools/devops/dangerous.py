def exec_shell(command: str) -> dict:
    return {"command": command, "status": "placeholder_not_executed"}


def kubectl_apply(manifest: str) -> dict:
    return {"manifest": manifest, "status": "placeholder_not_applied"}
