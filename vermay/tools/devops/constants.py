from __future__ import annotations

from enum import Enum


class KubectlGetResource(str, Enum):
    pods = "pods"
    services = "services"
    deployments = "deployments"
    nodes = "nodes"
    namespaces = "namespaces"
    events = "events"
    certificates = "certificates"
    challenges = "challenges"
    orders = "orders"
    certificaterequests = "certificaterequests"


class KubectlDescribeResource(str, Enum):
    pod = "pod"
    service = "service"
    deployment = "deployment"
    node = "node"
    certificate = "certificate"
    challenge = "challenge"
    order = "order"
    certificaterequest = "certificaterequest"


class KubectlDeleteResource(str, Enum):
    pod = "pod"
    service = "service"
    deployment = "deployment"
    statefulset = "statefulset"
    daemonset = "daemonset"
    job = "job"
    cronjob = "cronjob"


KUBECTL_GET_RESOURCES = [resource.value for resource in KubectlGetResource]
KUBECTL_DESCRIBE_RESOURCES = [resource.value for resource in KubectlDescribeResource]
KUBECTL_DELETE_RESOURCES = [resource.value for resource in KubectlDeleteResource]
