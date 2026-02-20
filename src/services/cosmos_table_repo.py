from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any
from urllib.parse import urlparse

from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ResourceExistsError
from azure.data.tables import TableServiceClient, UpdateMode


@dataclass
class CosmosTableRepoError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


class CosmosTableRepository:
    def __init__(
        self,
        table_name: str,
        auth_mode: str = "auto",
        connection_string: str | None = None,
        endpoint: str | None = None,
        ensure_table_exists: bool = False,
    ) -> None:
        auth_mode_normalized = auth_mode.strip().lower()
        effective_auth_mode = self._resolve_auth_mode(
            auth_mode_normalized,
            connection_string=connection_string,
            endpoint=endpoint,
        )

        try:
            if effective_auth_mode == "aad":
                resolved_endpoint = endpoint or self._derive_table_endpoint(connection_string)
                if not resolved_endpoint:
                    raise CosmosTableRepoError(
                        "COSMOS_TABLE_ENDPOINT is required when COSMOS_TABLE_AUTH_MODE=aad"
                    )
                credential = DefaultAzureCredential()
                table_service_client = TableServiceClient(endpoint=resolved_endpoint, credential=credential)
            else:
                if not connection_string:
                    raise CosmosTableRepoError(
                        "COSMOS_TABLE_CONNECTION_STRING is required when COSMOS_TABLE_AUTH_MODE=connection_string"
                    )
                normalized_connection_string = self._normalize_connection_string(connection_string)
                table_service_client = TableServiceClient.from_connection_string(normalized_connection_string)
        except CosmosTableRepoError:
            raise
        except Exception as exc:
            raise CosmosTableRepoError("Invalid Cosmos Table client configuration") from exc

        if ensure_table_exists:
            try:
                table_service_client.create_table(table_name=table_name)
            except ResourceExistsError:
                pass

        self.table_client = table_service_client.get_table_client(table_name=table_name)

    def upsert_entity(self, entity: dict[str, Any]) -> None:
        try:
            self.table_client.upsert_entity(mode=UpdateMode.REPLACE, entity=entity)
        except Exception as exc:  # pragma: no cover
            raise CosmosTableRepoError("Failed to write entity to Cosmos Table") from exc

    @staticmethod
    def _resolve_auth_mode(
        auth_mode: str,
        connection_string: str | None,
        endpoint: str | None,
    ) -> str:
        if auth_mode in {"aad", "connection_string"}:
            return auth_mode
        if auth_mode != "auto":
            raise CosmosTableRepoError(
                "COSMOS_TABLE_AUTH_MODE must be one of: auto, aad, connection_string"
            )
        if endpoint:
            return "aad"
        if connection_string and CosmosTableRepository._is_managed_identity_available():
            if CosmosTableRepository._derive_table_endpoint(connection_string):
                return "aad"
        if connection_string:
            return "connection_string"
        raise CosmosTableRepoError(
            "COSMOS table configuration missing. Provide COSMOS_TABLE_ENDPOINT (AAD) or COSMOS_TABLE_CONNECTION_STRING"
        )

    @staticmethod
    def _is_managed_identity_available() -> bool:
        return bool(os.getenv("IDENTITY_ENDPOINT") or os.getenv("MSI_ENDPOINT"))

    @staticmethod
    def _derive_table_endpoint(connection_string: str | None) -> str | None:
        if not connection_string:
            return None

        parts: dict[str, str] = {}
        for token in connection_string.split(";"):
            token = token.strip()
            if not token or "=" not in token:
                continue
            key, value = token.split("=", 1)
            parts[key.strip().lower()] = value.strip()

        table_endpoint = parts.get("tableendpoint")
        if table_endpoint:
            return table_endpoint

        account_endpoint = parts.get("accountendpoint")
        if not account_endpoint:
            return None

        parsed = urlparse(account_endpoint)
        host = parsed.hostname or ""
        account_name = host.split(".")[0] if host else ""
        if not account_name:
            return None

        return f"https://{account_name}.table.cosmos.azure.com:443/"

    @staticmethod
    def _normalize_connection_string(connection_string: str) -> str:
        parts: dict[str, str] = {}
        for token in connection_string.split(";"):
            token = token.strip()
            if not token or "=" not in token:
                continue
            key, value = token.split("=", 1)
            parts[key.strip().lower()] = value.strip()

        has_table_format = all(k in parts for k in ("accountname", "accountkey", "tableendpoint"))
        if has_table_format:
            return connection_string

        account_endpoint = parts.get("accountendpoint")
        account_key = parts.get("accountkey")
        if account_endpoint and account_key:
            parsed = urlparse(account_endpoint)
            host = parsed.hostname or ""
            account_name = host.split(".")[0] if host else ""
            if account_name:
                table_endpoint = f"https://{account_name}.table.cosmos.azure.com:443/"
                return (
                    "DefaultEndpointsProtocol=https;"
                    f"AccountName={account_name};"
                    f"AccountKey={account_key};"
                    f"TableEndpoint={table_endpoint};"
                )

        return connection_string
