"""Launch Dagster runs over its GraphQL API.

Lets the web app's "materialize" button start a real, first-class Dagster run
(visible in the UI, with lineage and logs) without the API container needing
Dagster installed or the data volume writable. The API service only speaks
HTTP to the webserver.

The run carries the ``dagster/partition`` tag, which is how a single-partition
launch of a partitioned job is expressed; asset contexts read their
``partition_key`` from it.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger("mpm.dagster")

JOB_NAME = "build_prospectivity"


class DagsterUnavailable(RuntimeError):
    pass


class LaunchFailed(RuntimeError):
    pass


def graphql_url() -> str:
    base = os.environ.get("MPM_DAGSTER_URL", "http://dagster:3000/dagster").rstrip("/")
    return f"{base}/graphql"


def _post(query: str, variables: dict | None = None, timeout: float = 15.0) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        graphql_url(), data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise DagsterUnavailable(f"dagster webserver unreachable at {graphql_url()}: {e}") from e
    if payload.get("errors"):
        raise LaunchFailed(str(payload["errors"]))
    return payload["data"]


def _find_repository() -> tuple[str, str]:
    """(location, repository) for the single code location this stack runs."""
    data = _post(
        """
        query {
          repositoriesOrError {
            ... on RepositoryConnection { nodes { name location { name } } }
            ... on PythonError { message }
          }
        }
        """
    )
    nodes = data.get("repositoriesOrError", {}).get("nodes") or []
    if not nodes:
        raise LaunchFailed(f"no repositories loaded: {data}")
    return nodes[0]["location"]["name"], nodes[0]["name"]


def launch_partition(profile: str) -> str:
    """Launch ``build_prospectivity`` for one profile partition; return run id."""
    location, repository = _find_repository()
    data = _post(
        """
        mutation Launch($params: ExecutionParams!) {
          launchPipelineExecution(executionParams: $params) {
            __typename
            ... on LaunchRunSuccess { run { runId } }
            ... on PythonError { message }
            ... on RunConfigValidationInvalid { errors { message } }
            ... on PipelineNotFoundError { message }
          }
        }
        """,
        {
            "params": {
                "selector": {
                    "repositoryLocationName": location,
                    "repositoryName": repository,
                    "pipelineName": JOB_NAME,
                },
                "executionMetadata": {
                    "tags": [{"key": "dagster/partition", "value": profile}]
                },
            }
        },
    )
    result = data["launchPipelineExecution"]
    if result["__typename"] != "LaunchRunSuccess":
        raise LaunchFailed(result.get("message") or str(result))
    run_id = result["run"]["runId"]
    log.info("launched dagster run %s for partition %s", run_id, profile)
    return run_id
