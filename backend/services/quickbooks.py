import os
from pathlib import Path
from typing import Any, Dict, List

import requests

from backend.config import get_env

TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
_TOKEN_CACHE: Dict[str, str] = {}


def _auth_tuple() -> tuple[str, str]:
    return (get_env("QB_CLIENT_ID"), get_env("QB_CLIENT_SECRET"))


def _refresh_access_token() -> str:
    refresh_token = _TOKEN_CACHE.get("refresh_token") or get_env("QB_REFRESH_TOKEN")
    response = requests.post(
        TOKEN_URL,
        auth=_auth_tuple(),
        headers={"Accept": "application/json"},
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=30,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = response.text[:2000]
        raise requests.HTTPError(
            f"{exc}. Intuit token response: {detail}",
            response=response,
        ) from exc
    data = response.json()
    access_token = data.get("access_token")
    new_refresh_token = data.get("refresh_token")
    if not access_token:
        raise ValueError("Token refresh succeeded but access_token was missing in response.")

    _TOKEN_CACHE["access_token"] = access_token
    os.environ["QB_ACCESS_TOKEN"] = access_token

    if new_refresh_token:
        _TOKEN_CACHE["refresh_token"] = new_refresh_token
        os.environ["QB_REFRESH_TOKEN"] = new_refresh_token

    return access_token


def _persist_env_values(values: Dict[str, str]) -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return

    lines = env_path.read_text().splitlines()
    keys = set(values.keys())
    replaced: Dict[str, bool] = {key: False for key in keys}
    output: List[str] = []

    for line in lines:
        stripped = line.strip()
        if "=" not in stripped or stripped.startswith("#"):
            output.append(line)
            continue
        key = stripped.split("=", 1)[0]
        if key in values:
            output.append(f'{key}="{values[key]}"')
            replaced[key] = True
        else:
            output.append(line)

    for key, done in replaced.items():
        if not done:
            output.append(f'{key}="{values[key]}"')

    env_path.write_text("\n".join(output) + "\n")


class QuickBooksClient:
    def __init__(self, realm_id: str, access_token: str, minor_version: str = "69"):
        self.realm_id = realm_id
        self.access_token = access_token
        self.minor_version = minor_version
        self.base = f"https://sandbox-quickbooks.api.intuit.com/v3/company/{realm_id}"

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.access_token}",
        }

    def _raise_http_error(self, response: requests.Response, exc: requests.HTTPError) -> None:
        detail = response.text[:2000]
        raise requests.HTTPError(
            f"{exc}. QuickBooks response: {detail}",
            response=response,
        ) from exc

    def _request(
        self,
        *,
        method: str,
        resource_or_url: str,
        params: Dict[str, Any] | None = None,
        json_body: Dict[str, Any] | None = None,
        retry_on_401: bool = True,
    ) -> Dict[str, Any]:
        url = resource_or_url if resource_or_url.startswith("http") else f"{self.base}/{resource_or_url}"
        response = requests.request(
            method=method,
            url=url,
            params=params,
            headers=self.headers,
            json=json_body,
            timeout=30,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            if response.status_code == 401 and retry_on_401:
                self.access_token = _refresh_access_token()
                return self._request(
                    method=method,
                    resource_or_url=resource_or_url,
                    params=params,
                    json_body=json_body,
                    retry_on_401=False,
                )
            self._raise_http_error(response, exc)
        return response.json()

    def _query(self, sql: str) -> Dict[str, Any]:
        return self._request(
            method="GET",
            resource_or_url="query",
            params={"minorversion": self.minor_version, "query": sql},
        )

    def _post(self, resource: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request(
            method="POST",
            resource_or_url=resource,
            params={"minorversion": self.minor_version},
            json_body=payload,
        )

    def get_bills(self) -> List[Dict[str, Any]]:
        data = self._query("select * from Bill")
        return data.get("QueryResponse", {}).get("Bill", [])

    def get_invoices(self) -> List[Dict[str, Any]]:
        data = self._query("select * from Invoice")
        return data.get("QueryResponse", {}).get("Invoice", [])

    def get_items(self) -> List[Dict[str, Any]]:
        data = self._query("select * from Item")
        return data.get("QueryResponse", {}).get("Item", [])

    def get_customers(self) -> List[Dict[str, Any]]:
        data = self._query("select * from Customer")
        return data.get("QueryResponse", {}).get("Customer", [])

    def get_vendors(self) -> List[Dict[str, Any]]:
        data = self._query("select * from Vendor")
        return data.get("QueryResponse", {}).get("Vendor", [])

    def get_accounts(self) -> List[Dict[str, Any]]:
        data = self._query("select * from Account")
        return data.get("QueryResponse", {}).get("Account", [])

    def validate_auth(self) -> Dict[str, Any]:
        data = self._query("select * from CompanyInfo")
        return data.get("QueryResponse", {})

    def create_bill(self, bill_payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._post("bill", bill_payload)

    def create_invoice(self, invoice_payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._post("invoice", invoice_payload)


def get_qb_client() -> QuickBooksClient:
    access_token = _TOKEN_CACHE.get("access_token") or get_env("QB_ACCESS_TOKEN")
    refresh_token = _TOKEN_CACHE.get("refresh_token") or get_env("QB_REFRESH_TOKEN", "")
    if refresh_token:
        _TOKEN_CACHE["refresh_token"] = refresh_token

    return QuickBooksClient(
        realm_id=get_env("QB_REALM_ID"),
        access_token=access_token,
        minor_version=get_env("QB_MINOR_VERSION", "69"),
    )


def exchange_authorization_code(code: str, redirect_uri: str) -> Dict[str, Any]:
    response = requests.post(
        TOKEN_URL,
        auth=_auth_tuple(),
        headers={"Accept": "application/json"},
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = response.text[:2000]
        raise requests.HTTPError(
            f"{exc}. Intuit token response: {detail}",
            response=response,
        ) from exc
    data = response.json()

    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    if access_token:
        _TOKEN_CACHE["access_token"] = access_token
        os.environ["QB_ACCESS_TOKEN"] = access_token
    if refresh_token:
        _TOKEN_CACHE["refresh_token"] = refresh_token
        os.environ["QB_REFRESH_TOKEN"] = refresh_token

    persist_payload: Dict[str, str] = {}
    if access_token:
        persist_payload["QB_ACCESS_TOKEN"] = access_token
    if refresh_token:
        persist_payload["QB_REFRESH_TOKEN"] = refresh_token
    if persist_payload:
        _persist_env_values(persist_payload)

    return data

