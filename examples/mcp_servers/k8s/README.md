# Kubernetes MCP Example Server

This example exposes read-only Kubernetes inspection over MCP stdio.

It reuses the same SSH and microk8s/kubectl backend as the built-in Vermay Kubernetes tools. SSH credentials remain inside the MCP server boundary and are read from `VERMAY_SSH_*` environment configuration.

## Capabilities

Tools:

- `kubectl_get`
- `kubectl_describe`
- `cluster_events`

Resources:

- `k8s://cluster/nodes`
- `k8s://cluster/services`
- `k8s://namespace/{namespace}/pods`

Prompts:

- `k8s-readonly-debug`
- `k8s-service-health-check`

## Usage

The tracked `config/mcp_servers.json` includes this server as `k8s`.

```bash
vermay mcp list-tools --server k8s
vermay mcp list-resources --server k8s
vermay mcp list-prompts --server k8s
```

Use it in a run by explicitly selecting the server:

```bash
vermay "check k8s status" --mcp-server k8s --mcp-prompt k8s-readonly-debug
```
